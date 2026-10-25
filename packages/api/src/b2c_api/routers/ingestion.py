"""OCR/extraction status, text preview, processing plan and outline approval."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal

from b2c_contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    CourseOutline,
    OutlineApprovalProvenance,
    ParsedBook,
    stage_key,
)
from b2c_core.source_identity import artifact_source_identity
from b2c_core.processing_plan import plan_parsed_book
from b2c_orchestration.indexing import IndexApprovedBookInput, IndexApprovedBookWorkflow

from b2c_outline.review import (
    POLICY_ID,
    POLICY_VERSION,
    OutlineReviewRejected,
    review_outline,
    merge_bodyless_lessons,
    include_leading_blocks_in_first_lesson,
)
from fastapi import HTTPException, Query
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.ingestion_recovery import recover_extraction
from b2c_api.deps import (
    TERMINAL_FAILURE_STATUSES,
    workflow_failure,
    is_workflow_id,
    require_course_id,
    require_workflow_id,
    validation_locations,
    with_progress_warning,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, REUSE_ONLY_AFTER_FAILURE, log
from b2c_api.schemas import OutlineReview, SystemOutlineApprovalRequest


SYSTEM_OUTLINE_APPROVAL_DISABLED = {
    "message_ar": ("الاعتماد الآلي للمخطط معطّل: يجب أن يراجع إنسانٌ المخطط ويعتمده عبر "
                   "‎/outline/approve‎ مع review_type=human وreviewer_id."),
    "message_en": ("system outline approval is disabled: a human must review the outline and approve it "
                   "through /outline/approve with review_type=human and a reviewer_id"),
    "setting": "B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL",
}


def _validate_course_supplementary_identity(parsed: ParsedBook, expected_book_id: str) -> None:
    """A source uploaded as a spine may be assigned as a course supplement later."""
    if parsed.book_id != expected_book_id:
        raise ValueError(f"supplementary book identity mismatch: {expected_book_id}")


def ingestion_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    book_progress = ctx.late("book_progress")
    indexing_status = ctx.late("indexing_status")
    course_qa_status = ctx.late("course_qa_status")
    remember_book_progress = ctx.late("remember_book_progress")

    @router.get("/v1/ingestions/{workflow_id}/text")
    async def ingestion_text_preview(workflow_id: str, course_id: str | None = None, offset: int = Query(0, ge=0),
                                     limit: int = Query(40, ge=1, le=100)) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        status = await ingestion_status(workflow_id, recovery_course_id=course_id)
        if status["status"] != "COMPLETED":
            raise HTTPException(status_code=409, detail="OCR extraction is not complete")
        result = status["result"]
        parsed = result.get("parsed_book", {}).get("ref")
        if not parsed:
            raise HTTPException(status_code=404, detail="parsed text artifact not found")
        ref = ArtifactRef.model_validate(parsed)
        if ref.stage != "ingest-parse-pdf" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/") or (course_id and not ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/")):
            raise HTTPException(status_code=503, detail="parsed artifact reference is invalid")
        store = get_store()
        book = ParsedBook.model_validate_json(await run_in_threadpool(store.read, ref, "parsed-book.json"))
        blocks = book.blocks[offset:offset + limit]
        return {"workflow_id": workflow_id, "book_id": book.book_id, "title": book.title,
            "offset": offset, "limit": limit, "total_blocks": len(book.blocks),
            "blocks": [{"block_id": b.block_id, "type": b.type, "text": b.text,
                        "pages": sorted({r.page_no for r in b.refs})} for b in blocks]}
    @router.get("/v1/ingestions/{workflow_id}")
    async def ingestion_status(workflow_id: str, recovery_course_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
            store = get_store()
            try:
                recovered = await recover_extraction(store, client, workflow_id, course_id=recovery_course_id)
            except Exception as recovery_exc:
                log.exception("failed to recover committed extraction")
                raise HTTPException(status_code=503, detail="stored extraction is unavailable") from recovery_exc
            if recovered is None:
                raise HTTPException(status_code=404, detail="ingestion not found and no committed extraction is associated") from exc
            return {"workflow_id": workflow_id, "status": "COMPLETED", "start_time": None,
                    "close_time": None, "result": recovered, "recovered_from_artifact": True,
                    "completion_scope": "extraction", "workflow_history_available": False}
        except Exception as exc:
            log.exception("failed to read ingestion status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status = getattr(description, "status", None)
        status_name = getattr(status, "name", str(status))
        response = {
            "workflow_id": workflow_id,
            "status": status_name,
            "start_time": getattr(description, "start_time", None),
            "close_time": getattr(description, "close_time", None),
        }
        if status_name == "COMPLETED":
            try:
                response["result"] = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed ingestion result")
                raise HTTPException(status_code=503, detail="ingestion result is unavailable") from exc
        elif status_name in TERMINAL_FAILURE_STATUSES:
            if failure := await workflow_failure(handle):
                response["failure"] = failure
        return response

    @router.get("/v1/ingestions/{workflow_id}/processing-plan")
    async def ingestion_processing_plan(
        workflow_id: str,
        course_id: str = Query(..., min_length=1, max_length=80),
        target_output: Literal["ocr", "outline", "rag", "course", "questions", "translation", "audio"] = "ocr",
        target_language: Literal["en", "ar"] | None = None,
        source_language: Literal["en", "ar"] | None = None,
        content_kind: Literal["book", "course", "questions"] = "course",
        include_questions: bool = True,
    ) -> dict[str, Any]:
        """Plan dependencies from server-verified extraction and book progress."""
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        try:
            status_response = await ingestion_status(workflow_id, recovery_course_id=course_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before downstream planning")
            result = status_response["result"]
            parsed_ref = ArtifactRef.model_validate(result["parsed_book"]["ref"])
            if parsed_ref.stage != "ingest-parse-pdf" or not parsed_ref.uri.startswith(
                    f"s3://{settings.s3_bucket}/{course_id}/"):
                raise HTTPException(status_code=422, detail="parsed book is outside the requested course")
            store = get_store()
            parsed = ParsedBook.model_validate_json(await run_in_threadpool(
                store.read, parsed_ref, "parsed-book.json"))
            progress = await book_progress(course_id, parsed.book_id)
            available: list[str] = ["ocr"]
            stages = progress.get("stages", {})
            parsed_sha = parsed_ref.sha256

            # Old progress records have no content lineage; never reuse them.
            outline_item = stages.get("outline", {})
            approved_ref_data = outline_item.get("approved_outline_ref")
            approved_ref = None
            if (outline_item.get("source_parsed_sha256") == parsed_sha and approved_ref_data):
                try:
                    candidate = ArtifactRef.model_validate(approved_ref_data)
                    if candidate.stage == "approve-outline" and candidate.uri.startswith(
                            f"s3://{settings.s3_bucket}/{course_id}/"):
                        approved_payload = json.loads(await run_in_threadpool(
                            store.read, candidate, "approved-outline.json"))
                        spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
                        outline = CourseOutline.model_validate(approved_payload["outline"])
                        if spine_ref.sha256 == parsed_sha and outline.spine_book_id == parsed.book_id:
                            approved_ref = candidate
                            available.append("outline")
                except Exception:
                    approved_ref = None

            index_item = stages.get("index", {})
            index_valid = False
            if (approved_ref and index_item.get("status") == "COMPLETED" and
                    index_item.get("source_parsed_sha256") == parsed_sha and
                    index_item.get("outline_sha256") == approved_ref.sha256):
                try:
                    index_state = await indexing_status(index_item["workflow_id"], recovery_course_id=course_id)
                    index_valid = (index_state.get("status") == "COMPLETED" and
                                   index_state.get("result", {}).get("outline_sha256") == approved_ref.sha256 and
                                   index_state.get("result", {}).get("course_id") == course_id)
                except Exception:
                    index_valid = False
            if index_valid:
                available.append("rag")

            qa_item = stages.get("qa", {})
            if (approved_ref and index_valid and qa_item.get("status") == "COMPLETED" and
                    qa_item.get("source_parsed_sha256") == parsed_sha and
                    qa_item.get("outline_sha256") == approved_ref.sha256):
                try:
                    qa_state = await course_qa_status(qa_item["workflow_id"])
                    qa = qa_state.get("course_qa", {})
                    if (qa_state.get("status") == "COMPLETED" and qa.get("status") == "final" and
                            qa.get("approved_outline_sha256") == approved_ref.sha256 and
                            qa.get("spine_parsed_sha256") == parsed_sha):
                        available.extend(["course", "questions"])
                except Exception:
                    pass
            # Translation reuse is deliberately not advertised here: the mobile
            # client must resolve the exact matching translation workflow/result,
            # not infer it from a generic COMPLETED stage flag. Deterministic
            # translation workflow IDs still make a repeat request idempotent.
            plan = plan_parsed_book(
                parsed, target_output, target_language,
                source_language=source_language,
                available_stages=available,
                content_kind=content_kind,
                include_questions=include_questions,
            )
            return {
                "course_id": course_id, "book_id": parsed.book_id,
                "source_artifact": parsed_ref.model_dump(mode="json"),
                "plan": plan.model_dump(mode="json"),
            }
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to build processing plan")
            raise HTTPException(status_code=503, detail="processing plan is unavailable") from exc

    @router.get("/v1/ingestions/{workflow_id}/outline")
    async def ingestion_outline(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        try:
            status_response = await ingestion_status(workflow_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before outline review")
            result = status_response["result"]
            outline_result = result.get("outline") if isinstance(result, dict) else None
            if not outline_result:
                raise HTTPException(status_code=409, detail="this ingestion has no spine outline")
            ref = ArtifactRef.model_validate(outline_result["ref"])
            store = get_store()
            outline = CourseOutline.model_validate_json(
                await run_in_threadpool(store.read, ref, "course-outline.json"))
            return {"outline": outline.model_dump(mode="json"), "artifact": ref.model_dump(mode="json")}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to read draft outline")
            raise HTTPException(status_code=503, detail="outline artifact is unavailable") from exc

    def require_system_outline_approval_enabled() -> None:
        if not settings.allow_system_outline_approval:
            raise HTTPException(status_code=409, detail=SYSTEM_OUTLINE_APPROVAL_DISABLED)

    @router.post("/v1/ingestions/{workflow_id}/outline/approve-system", status_code=201)
    async def approve_system_outline(
        workflow_id: str, request: SystemOutlineApprovalRequest,
    ) -> dict[str, Any]:
        require_system_outline_approval_enabled()
        draft = await ingestion_outline(workflow_id)
        options = request
        draft_outline = CourseOutline.model_validate(draft["outline"])
        # The selection is a set; keep the draft's order for the books it lists.
        selected = set(options.supplementary_workflow_ids)
        selected_supplementary_ids = (
            [book_id for book_id in draft_outline.supplementary_book_ids if book_id in selected]
            + sorted(selected - set(draft_outline.supplementary_book_ids)))
        draft_outline = CourseOutline.model_validate({
            **draft_outline.model_dump(mode="python"),
            "supplementary_book_ids": selected_supplementary_ids,
        })
        review = OutlineReview(
            review_type="system", outline=draft_outline,
            supplementary_workflow_ids=options.supplementary_workflow_ids,
            target_tokens=options.target_tokens, overlap_ratio=options.overlap_ratio,
            index_after_approval=options.index_after_approval)
        return await approve_outline(workflow_id, review)

    @router.post("/v1/ingestions/{workflow_id}/outline/approve", status_code=201)
    async def approve_outline(workflow_id: str, review: OutlineReview) -> dict[str, Any]:
        require_workflow_id("ingest", workflow_id, "ingestion not found")
        if review.review_type == "system":
            require_system_outline_approval_enabled()
        try:
            status_response = await ingestion_status(workflow_id)
            if status_response["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="ingestion must complete before outline review")
            result = status_response["result"]
            client = await get_temporal_client()
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to retrieve outline for review")
            raise HTTPException(status_code=503, detail="ingestion result is unavailable") from exc

        outline_result = result.get("outline") if isinstance(result, dict) else None
        parsed_result = result.get("parsed_book") if isinstance(result, dict) else None
        if not outline_result or not parsed_result:
            raise HTTPException(status_code=409, detail="this ingestion has no spine outline to review")
        draft_ref = ArtifactRef.model_validate(outline_result["ref"])
        parsed_ref = ArtifactRef.model_validate(parsed_result["ref"])
        expected_prefix = f"s3://{settings.s3_bucket}/{review.outline.course_id}/"
        if not parsed_ref.uri.startswith(expected_prefix):
            raise HTTPException(status_code=422, detail="spine ParsedBook artifact is outside the approved course")
        store = get_store()
        try:
            draft = CourseOutline.model_validate_json(
                await run_in_threadpool(store.read, draft_ref, "course-outline.json")
            )
            parsed = ParsedBook.model_validate_json(
                await run_in_threadpool(store.read, parsed_ref, "parsed-book.json")
            )
        except Exception as exc:
            log.exception("failed to load outline review artifacts")
            raise HTTPException(status_code=503, detail="outline artifacts are unavailable") from exc

        outline = review.outline
        if (
            outline.course_id != draft.course_id
            or outline.spine_book_id != draft.spine_book_id
            or len(set(outline.supplementary_book_ids)) != len(outline.supplementary_book_ids)
            or set(outline.supplementary_book_ids) != set(draft.supplementary_book_ids)
        ):
            raise HTTPException(status_code=422, detail="reviewed outline identity does not match the draft")
        system_review = None
        if review.review_type == "system":
            try:
                outline = merge_bodyless_lessons(parsed, outline)
                outline = include_leading_blocks_in_first_lesson(parsed, outline)
                system_review = review_outline(parsed, outline)
                outline = CourseOutline.model_validate({**outline.model_dump(mode="python"), "requires_review": False, "schema_version": SCHEMA_VERSION})
            except OutlineReviewRejected as exc:
                # Fixed policy reasons from b2c_outline.review; no input values.
                raise HTTPException(status_code=422, detail=f"system structural review rejected: {exc}") from exc
            except ValidationError as exc:
                log.warning("system structural review produced an invalid outline: %s", exc)
                raise HTTPException(status_code=422, detail={
                    "message": "system structural review rejected",
                    "errors": validation_locations(exc)}) from exc
        block_positions = {block.block_id: index for index, block in enumerate(parsed.blocks)}
        known_lessons: set[str] = set()
        for module in outline.modules:
            for lesson in module.lessons:
                span = lesson.spine_span
                if span.start_block not in block_positions or span.end_block not in block_positions:
                    raise HTTPException(status_code=422, detail="lesson span references an unknown spine block")
                if block_positions[span.start_block] > block_positions[span.end_block]:
                    raise HTTPException(status_code=422, detail="lesson span ends before it starts")
                if span.pages[0] < 1 or span.pages[1] < span.pages[0] or span.pages[1] > parsed.page_count:
                    raise HTTPException(status_code=422, detail="lesson page span is outside the parsed book")
                if any(prior not in known_lessons for prior in lesson.prior_lessons):
                    raise HTTPException(status_code=422, detail="lesson prerequisite must refer to an earlier lesson")
                known_lessons.add(lesson.lesson_id)

        if set(review.supplementary_workflow_ids) != set(outline.supplementary_book_ids):
            raise HTTPException(status_code=422, detail="provide a completed ingestion workflow for every supplementary book")
        supplementary_parsed_refs: dict[str, dict[str, Any]] = {}
        for book_id, supplementary_workflow_id in review.supplementary_workflow_ids.items():
            if not is_workflow_id("ingest", supplementary_workflow_id):
                raise HTTPException(status_code=422, detail=f"invalid ingestion workflow for supplementary book {book_id}")
            try:
                supplement_handle = client.get_workflow_handle(supplementary_workflow_id)
                supplement_description = await supplement_handle.describe()
                supplement_status = getattr(getattr(supplement_description, "status", None), "name", "")
                if supplement_status != "COMPLETED":
                    raise HTTPException(status_code=409, detail=f"supplementary book {book_id} is not parsed yet")
                supplement_result = await supplement_handle.result()
                supplement_ref = ArtifactRef.model_validate(supplement_result["parsed_book"]["ref"])
                expected_prefix = f"s3://{settings.s3_bucket}/{outline.course_id}/"
                if not supplement_ref.uri.startswith(expected_prefix):
                    raise HTTPException(status_code=422, detail=f"supplementary artifact is outside course scope: {book_id}")
                supplement_parsed = ParsedBook.model_validate_json(
                    await run_in_threadpool(store.read, supplement_ref, "parsed-book.json")
                )
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("failed to resolve supplementary ParsedBook artifact")
                raise HTTPException(status_code=503, detail=f"supplementary book {book_id} result is unavailable") from exc
            try:
                _validate_course_supplementary_identity(supplement_parsed, book_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="supplementary book identity mismatch") from exc
            supplementary_parsed_refs[book_id] = supplement_ref.model_dump(mode="json")

        provenance = (OutlineApprovalProvenance(
            review_type="system", system_id=POLICY_ID, policy_version=POLICY_VERSION,
            evidence_sha256=system_review.evidence_sha256,
        ) if system_review else OutlineApprovalProvenance(
            review_type="human", reviewer_id=review.reviewer_id,
        ))
        payload = {
            "outline": outline.model_dump(mode="json"),
            "approval_provenance": provenance.model_dump(mode="json"),
            "system_review_checks": list(system_review.checks) if system_review else None,
            "reviewer_id": review.reviewer_id,
            "approved_at": datetime.now(UTC).isoformat(),
            "draft_ref": draft_ref.model_dump(mode="json"),
            "spine_parsed_ref": parsed_ref.model_dump(mode="json"),
            "source_identity": await run_in_threadpool(artifact_source_identity, store, parsed_ref),
            "supplementary_parsed_refs": supplementary_parsed_refs,
        }
        payload_bytes = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        key = stage_key("approve-outline", "2", [draft_ref.sha256, sha256(payload_bytes).hexdigest()],
                        {"approval_provenance": provenance.model_dump(mode="json")})
        try:
            approved_ref = await run_in_threadpool(
                store.put, outline.course_id, "approve-outline", key,
                {"approved-outline.json": payload_bytes},
            )
        except Exception as exc:
            log.exception("failed to store approved outline")
            raise HTTPException(status_code=503, detail="could not store approved outline") from exc
        index_workflow_id = None
        if review.index_after_approval:
            index_input = IndexApprovedBookInput(
                course_id=outline.course_id, parsed_ref=parsed_ref.model_dump(mode="json"),
                approved_outline_ref=approved_ref.model_dump(mode="json"),
                supplementary_parsed_refs=supplementary_parsed_refs,
                activity_task_queue=settings.indexing_task_queue,
                target_tokens=review.target_tokens,
                overlap_ratio=review.overlap_ratio,
            )
            index_workflow_id = "b2c-index-" + hashlib.sha256(
                json.dumps(index_input.model_dump(mode="json"), sort_keys=True).encode("utf-8")
            ).hexdigest()
            try:
                await client.start_workflow(
                    IndexApprovedBookWorkflow.run, index_input,
                    id=index_workflow_id, task_queue=settings.indexing_task_queue,
                    id_reuse_policy=REUSE_ONLY_AFTER_FAILURE,
                )
            except WorkflowAlreadyStartedError:
                pass
            except Exception as exc:
                log.exception("approved outline persisted, but indexing did not start")
                raise HTTPException(status_code=503, detail={
                    "message": "outline was approved but indexing could not be queued",
                    "approved_outline": approved_ref.model_dump(mode="json"),
                    "index_workflow_id": index_workflow_id,
                }) from exc
        progress_warning = await remember_book_progress(outline.course_id, outline.spine_book_id, "outline",
                                     index_workflow_id or workflow_id,
                                     {"approved_outline_ref": approved_ref.model_dump(mode="json"),
                                      "source_parsed_sha256": parsed_ref.sha256,
                                      "source_identity": payload["source_identity"],
                                      "source_file_sha256": payload["source_identity"].get("source_file_sha256"),
                                      "source_ingest_workflow_id": workflow_id,
                                      "approval_provenance": provenance.model_dump(mode="json")})
        if index_workflow_id:
            progress_warning = await remember_book_progress(outline.course_id, outline.spine_book_id, "index",
                                         index_workflow_id, {"source_parsed_sha256": parsed_ref.sha256,
                                         "source_identity": payload["source_identity"],
                                         "source_file_sha256": payload["source_identity"].get("source_file_sha256"),
                                         "source_ingest_workflow_id": workflow_id,
                                         "outline_sha256": approved_ref.sha256,
                                         "index_parameters": {"target_tokens": review.target_tokens,
                                         "overlap_ratio": review.overlap_ratio, "embedding_model": index_input.embedding_model}}) or progress_warning
        return with_progress_warning({"approved_outline": approved_ref.model_dump(mode="json"),
                "reviewer_id": review.reviewer_id, "review_type": review.review_type,
                "approval_provenance": provenance.model_dump(mode="json"),
                "approved_at": payload["approved_at"], "index_workflow_id": index_workflow_id}, progress_warning)

    ctx.provide(ingestion_status=ingestion_status)
    return router
