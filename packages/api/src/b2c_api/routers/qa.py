"""Course question generation, status (with saved-artifact recovery), pause/resume, checkpoints."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from b2c_contracts import (
    ArtifactRef,
)
from b2c_orchestration.qa import GenerateCourseQAInput, GenerateCourseQAWorkflow
from b2c_pedagogy.qa_generation import ScopeResult

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode
from b2c_api.deps import (
    require_book_id,
    require_course_id,
    require_workflow_id,
    PROGRESS_WARNING,
    with_progress_warning,
    s3_json,
    verified_ref,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, REUSE_ONLY_AFTER_FAILURE, log
from b2c_api.schemas import CourseQAGenerationRequest


def qa_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    app = ctx.app
    settings = ctx.settings
    get_store = ctx.get_store
    library_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    generation_profile = ctx.generation_profile
    book_progress = ctx.late("book_progress")
    indexing_status = ctx.late("indexing_status")
    remember_book_progress = ctx.late("remember_book_progress")

    @router.post("/v1/courses/{course_id}/qa/generate", status_code=202)
    async def generate_course_questions(course_id: str, request: CourseQAGenerationRequest) -> dict[str, Any]:
        manager = app.state.model_lifecycle
        saved_retrieval_default = manager.defaults.get("rag")
        requested_retrieval_provider = request.retrieval_provider_id is not None or saved_retrieval_default is not None
        retrieval_provider_id = request.retrieval_provider_id or saved_retrieval_default
        if retrieval_provider_id is None:
            configured_mode = request.retrieval_mode or settings.retrieval_mode
            retrieval_provider_id = "bge-m3" if configured_mode == "hybrid" else "lexical"
        expected_modes = {
            "lexical": "lexical", "bge-m3": "hybrid", "embeddinggemma2-lite": "hybrid",
            "lightrag-157": "lightrag", "hipporag-2": "hipporag",
        }
        retrieval_mode = expected_modes[retrieval_provider_id]
        if retrieval_provider_id != "lexical":
            provider = manager.providers.get(retrieval_provider_id)
            if provider is None or provider.adapter == "unsupported":
                raise HTTPException(status_code=503, detail="the selected retrieval route is not configured on this server")
            if requested_retrieval_provider and (state := await manager.status(retrieval_provider_id))["state"] != "ready":
                raise HTTPException(status_code=503, detail="the selected retrieval route is not ready")
            if not requested_retrieval_provider and retrieval_mode == "hybrid" and not settings.embedding_base_url:
                raise HTTPException(status_code=503, detail="hybrid retrieval endpoint is not configured")
            retrieval_model_id = provider.model
        else:
            retrieval_model_id = "lexical-v1"
        retrieval_top_k = request.retrieval_top_k or settings.retrieval_top_k
        approved_ref = request.approved_outline_ref
        if approved_ref.stage != "approve-outline" or not approved_ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="approved outline is outside the requested course")
        await verified_ref(get_store(), course_id, approved_ref, "approve-outline", "approved outline")
        try:
            client = await get_temporal_client()
            saved_index = await indexing_status(request.index_workflow_id, recovery_course_id=course_id)
            if saved_index["status"] != "COMPLETED":
                raise HTTPException(status_code=409, detail="course indexing must complete before question generation")
            index_result = saved_index["result"]
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to verify completed course indexing")
            raise HTTPException(status_code=503, detail="indexing status is unavailable") from exc
        if (not isinstance(index_result, dict) or index_result.get("course_id") != course_id
                or index_result.get("outline_sha256") != approved_ref.sha256):
            raise HTTPException(status_code=422, detail="index workflow result does not match the approved course outline")
        cache_bust = uuid.uuid4().hex if request.force_regenerate else ""
        if request.lesson_ids is not None and len(set(request.lesson_ids)) != len(request.lesson_ids):
            raise HTTPException(status_code=422, detail="lesson_ids must not contain duplicates")
        selected_model, selected_queue, selected_provider = generation_profile("questions")
        generation_config = (
            f"{request.lesson_question_count}:{request.group_question_count}:"
            f"{request.course_question_count}:{request.lesson_mcq_count}:{request.max_tokens}:"
            f"enrichment={int(request.include_lesson_enrichment)}:assessments={int(request.include_review_assessments)}:{request.assessment_question_count}:"
            f"{retrieval_provider_id}:{retrieval_model_id}:{retrieval_mode}:{retrieval_top_k}:"
            f"{','.join(request.lesson_ids or [])}"
        )
        reasoning_identity = (
            f"\0reasoning:{settings.llm_reasoning_effort}"
            if settings.llm_reasoning_effort else ""
        )
        workflow_id = "b2c-qa-" + hashlib.sha256(
            (course_id + "\0" + approved_ref.sha256 + "\0" + generation_config
             + "\0" + selected_model + "\0" + selected_provider + "\0" + selected_queue + reasoning_identity
             + "\0phase5-grounded-qa-v4\0" + cache_bust).encode("utf-8")
        ).hexdigest()
        inp = GenerateCourseQAInput(
            course_id=course_id, approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result=index_result, lesson_question_count=request.lesson_question_count,
            group_question_count=request.group_question_count, course_question_count=request.course_question_count,
            lesson_mcq_count=request.lesson_mcq_count,
            include_lesson_enrichment=request.include_lesson_enrichment,
            include_review_assessments=request.include_review_assessments,
            assessment_question_count=request.assessment_question_count,
            max_tokens=request.max_tokens, lesson_ids=request.lesson_ids,
            index_workflow_id=request.index_workflow_id, cache_bust=cache_bust or None,
            retrieval_mode=retrieval_mode,
            retrieval_provider_id=retrieval_provider_id,
            retrieval_model_id=retrieval_model_id,
            retrieval_top_k=retrieval_top_k,
            activity_task_queue=selected_queue,
            selected_model_id=selected_model, selected_provider_id=selected_provider,
        )
        try:
            try:
                await client.start_workflow(GenerateCourseQAWorkflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue,
                                            id_reuse_policy=REUSE_ONLY_AFTER_FAILURE)
            except WorkflowAlreadyStartedError:
                pass
        except Exception as exc:
            log.exception("failed to start course question generation")
            raise HTTPException(status_code=503, detail="question generation could not be queued") from exc
        progress_warning = None
        try:
            store = get_store()
            approved_payload = json.loads(await run_in_threadpool(store.read, approved_ref, "approved-outline.json"))
            book_id = approved_payload["outline"]["spine_book_id"]
            spine_ref = ArtifactRef.model_validate(approved_payload["spine_parsed_ref"])
            progress_warning = await remember_book_progress(course_id, book_id, "qa", workflow_id, {
                "source_parsed_sha256": spine_ref.sha256,
                "outline_sha256": approved_ref.sha256,
                "index_workflow_id": request.index_workflow_id,
            })
        except Exception:
            log.exception("could not associate QA workflow with its book")
            progress_warning = PROGRESS_WARNING
        return with_progress_warning({"course_id": course_id, "workflow_id": workflow_id, "status_url": f"/v1/course-qa/{workflow_id}", "force_regenerate": request.force_regenerate}, progress_warning)

    @router.get("/v1/course-qa/{workflow_id}")
    async def course_qa_status(workflow_id: str, recovery_course_id: str | None = None, recovery_book_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        if recovery_course_id is not None:
            require_course_id(recovery_course_id)
        if recovery_book_id is not None:
            require_book_id(recovery_book_id)
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
        except Exception as exc:
            if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND and not (recovery_course_id and recovery_book_id):
                # Older APKs know only the workflow ID. Discover its exact saved
                # owner (within the given course when one is supplied), then use
                # the same source-lineage validation as new APKs.
                from b2c_api.qa_recovery import saved_qa_owner
                owner = await run_in_threadpool(saved_qa_owner, library_store(), workflow_id, recovery_course_id)
                if owner:
                    recovery_course_id, recovery_book_id = owner
            if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND and recovery_course_id and recovery_book_id:
                registry = await book_progress(recovery_course_id, recovery_book_id)
                saved = registry.get("stages", {}).get("qa", {})
                if saved.get("workflow_id") == workflow_id and saved.get("artifact_ref"):
                    recovered_ref = ArtifactRef.model_validate(saved["artifact_ref"])
                    if recovered_ref.stage != "generate-course-qa" or not recovered_ref.uri.startswith(f"s3://{settings.s3_bucket}/{recovery_course_id}/"):
                        raise HTTPException(status_code=503, detail="question generation artifact failed scope validation")
                    recovered_store = get_store()
                    qa = json.loads(await run_in_threadpool(recovered_store.read, recovered_ref, "course-qa.json"))
                    scopes = qa.get("scopes", [])
                    recovered_status = "COMPLETED" if qa.get("status") == "final" else "PAUSED" if qa.get("status") == "paused" else "PARTIAL"
                    return {"workflow_id": workflow_id, "status": recovered_status, "recovered_from_artifact": True,
                        "result": {"course_id": recovery_course_id, "artifact": recovered_ref.model_dump(mode="json")},
                        "course_qa": qa, "progress": {"phase": "questions", "status": recovered_status,
                        "completed_scopes": sum(scope.get("status") == "final" for scope in scopes),
                        "failed_scopes": sum(scope.get("status") != "final" for scope in scopes),
                        "total_scopes": len(scopes),
                        "question_count": sum(len(scope.get("items", [])) for scope in scopes),
                        "covered_lessons": len(qa.get("covered_lesson_ids", [])),
                        "expected_lessons": len(qa.get("expected_lesson_ids", []))}}
            log.exception("failed to read question generation status")
            raise HTTPException(status_code=503, detail="Temporal is unavailable") from exc
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        store = get_store()
        if status_name == "RUNNING":
            progress_key = f"_system/qa-progress/{workflow_id}.json"
            progress_value = None
            if getattr(store, "s3", None) is not None:
                try:
                    progress_value = await s3_json(store.s3, settings.s3_bucket, progress_key)
                except Exception as exc:
                    log.exception("failed to read QA progress checkpoint")
                    raise HTTPException(status_code=503, detail="question generation progress is unavailable") from exc
            if progress_value is not None:
                response["progress"] = progress_value
            else:
                started = getattr(description, "start_time", None)
                elapsed = max(0, int((datetime.now(UTC) - started).total_seconds())) if started else None
                response["progress"] = {"phase": "questions", "status": "RUNNING",
                    "message": "الرحلة تعمل؛ لا تملك هذه الرحلة القديمة نقاط تقدم داخلية محفوظة.",
                    "elapsed_seconds": elapsed, "checkpointing": False}
        if status_name == "COMPLETED":
            try:
                result = await handle.result()
                response["result"] = result
                if isinstance(result, dict) and result.get("artifact"):
                    result_ref = ArtifactRef.model_validate(result["artifact"])
                    if (result_ref.stage != "generate-course-qa" or
                            not result_ref.uri.startswith(f"s3://{settings.s3_bucket}/{result.get('course_id')}/")):
                        raise HTTPException(status_code=503, detail="question generation artifact failed scope validation")
                    store = get_store()
                    response["course_qa"] = json.loads(await run_in_threadpool(
                        store.read, result_ref, "course-qa.json"))
                    qa_result = response["course_qa"]
                    scopes = qa_result.get("scopes", [])
                    content_ok = qa_result.get("status") == "final"
                    paused = qa_result.get("status") == "paused"
                    response["status"] = "COMPLETED" if content_ok else "PAUSED" if paused else "FAILED"
                    response["progress"] = {"phase": "complete" if content_ok else "questions",
                        "status": response["status"],
                        "completed_scopes": sum(scope.get("status") == "final" for scope in scopes),
                        "failed_scopes": sum(scope.get("status") != "final" for scope in scopes),
                        "total_scopes": len(scopes),
                        "question_count": sum(len(scope.get("items", [])) for scope in scopes),
                        "covered_lessons": len(qa_result.get("covered_lesson_ids", [])),
                        "expected_lessons": len(qa_result.get("expected_lesson_ids", []))}
            except HTTPException:
                raise
            except Exception as exc:
                log.exception("failed to read completed question generation")
                raise HTTPException(status_code=503, detail="question generation result is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            store = get_store()
            try:
                progress_key = f"_system/qa-progress/{workflow_id}.json"
                saved_progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
            except Exception as progress_error:
                log.exception("failed to read terminal question generation progress checkpoint")
                raise HTTPException(status_code=503, detail="question generation progress is unavailable") from progress_error
            if saved_progress is not None:
                response["progress"] = saved_progress
            else:
                response["progress"] = {"progress_state": "not_reported", "completed_scopes": 0,
                                         "total_scopes": 0, "message": "لا توجد نقاط تقدم محفوظة لهذا التشغيل."}
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        return response

    @router.post("/v1/course-qa/{workflow_id}/pause")
    async def pause_course_qa(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        try:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(workflow_id)
            description = await handle.describe()
            status_name = getattr(getattr(description, "status", None), "name", "")
            if status_name != "RUNNING":
                raise HTTPException(status_code=409, detail="only a running question generation can be paused")
            store = get_store()
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/qa-controls/{workflow_id}.json",
                Body=json.dumps({"action":"pause_after_checkpoint","requested_at":datetime.now(UTC).isoformat()}).encode(),
                ContentType="application/json")
            return {"workflow_id": workflow_id, "status": "PAUSE_REQUESTED",
                    "message": "The current lesson will finish its checkpoint before pausing."}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to request a safe QA pause")
            raise HTTPException(status_code=503, detail="pause request could not be saved") from exc

    @router.post("/v1/course-qa/{workflow_id}/resume")
    async def resume_course_qa(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        try:
            client = await get_temporal_client()
            old_handle = client.get_workflow_handle(workflow_id)
            description = await old_handle.describe()
            temporal_status = getattr(getattr(description, "status", None), "name", "")
            if temporal_status not in {"COMPLETED", "FAILED"}:
                raise HTTPException(status_code=409, detail="the workflow must stop before it can be resumed")
            prior = {}
            if temporal_status == "COMPLETED":
                prior = await old_handle.result()
            context = dict(prior.get("resume_context") or {})
            if not context:
                # Older failed runs did not return resume_context. Rebuild it from the
                # immutable Temporal start event, then read only compatible saved checkpoints.
                history = await old_handle.fetch_history()
                for event in history.events:
                    if event.HasField("workflow_execution_started_event_attributes"):
                        payloads = event.workflow_execution_started_event_attributes.input.payloads
                        values = await client.data_converter.decode(payloads)
                        if values:
                            raw = values[0]
                            context = raw.model_dump(mode="json") if hasattr(raw, "model_dump") else dict(raw)
                            break
            if not context or (temporal_status == "COMPLETED" and
                    prior.get("status") not in {"paused", "failed"}):
                raise HTTPException(status_code=409, detail="this workflow has no safe checkpoint to resume")
            expected_model = context.get("selected_model_id") or settings.llm_model_id
            if prior.get("model_id") and prior.get("model_id") != expected_model:
                raise HTTPException(status_code=409,
                    detail="the saved workflow model differs from its checkpoints")
            context["checkpoint_source_workflow_ids"] = list(dict.fromkeys(
                [*context.get("checkpoint_source_workflow_ids", []), workflow_id]))
            inp = GenerateCourseQAInput.model_validate(context)
            resumed_id = "b2c-qa-" + hashlib.sha256((workflow_id + "\0resume").encode()).hexdigest()
            try:
                await client.start_workflow(GenerateCourseQAWorkflow.run, inp,
                    id=resumed_id, task_queue=inp.activity_task_queue)
            except WorkflowAlreadyStartedError:
                pass
            progress_warning = None
            try:
                store = get_store()
                approved_ref = ArtifactRef.model_validate(inp.approved_outline_ref)
                approved_payload = json.loads(await run_in_threadpool(store.read, approved_ref, "approved-outline.json"))
                book_id = approved_payload["outline"]["spine_book_id"]
                progress_warning = await remember_book_progress(inp.course_id, book_id, "qa", resumed_id)
            except Exception:
                log.exception("could not associate resumed QA workflow with its book")
                progress_warning = PROGRESS_WARNING
            return with_progress_warning({"course_id": inp.course_id, "workflow_id": resumed_id,
                    "status_url": f"/v1/course-qa/{resumed_id}", "resumed_from": workflow_id}, progress_warning)
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to resume QA from saved checkpoints")
            raise HTTPException(status_code=503, detail="QA could not resume from its saved checkpoint") from exc

    @router.get("/v1/course-qa/{workflow_id}/checkpoints")
    async def course_qa_checkpoints(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("qa", workflow_id, "question generation workflow not found")
        store = get_store()
        prefix=f"_system/qa-checkpoints/{workflow_id}/"
        def read_scopes() -> list[dict[str,Any]]:
            output=[]; token=None
            while True:
                args={"Bucket":settings.s3_bucket,"Prefix":prefix}
                if token: args["ContinuationToken"]=token
                page=store.s3.list_objects_v2(**args)
                for entry in page.get("Contents",[]):
                    key=entry["Key"]; relative=key[len(prefix):]; parts=relative.split("/")
                    if len(parts)!=2 or not parts[0] or not parts[1].endswith(".json"): continue
                    try:
                        payload=json.loads(store.s3.get_object(Bucket=settings.s3_bucket,Key=key)["Body"].read())
                        scope=ScopeResult.model_validate(payload.get("scope_result"))
                    except Exception: continue
                    if not scope.items: continue
                    output.append({"checkpoint_fingerprint":parts[0],"identity":payload.get("identity",{}),
                        "saved_at":payload.get("saved_at"),"scope_result":scope.model_dump(mode="json")})
                if not page.get("IsTruncated"): break
                token=page.get("NextContinuationToken")
                if not token: raise ValueError("truncated scope checkpoint listing has no continuation token")
            return output
        def read_lesson_assets() -> list[dict[str, Any]]:
            output: dict[str, tuple[str, dict[str, Any]]] = {}
            token = None
            while True:
                args = {"Bucket": settings.s3_bucket, "Prefix": prefix}
                if token:
                    args["ContinuationToken"] = token
                page = store.s3.list_objects_v2(**args)
                for entry in page.get("Contents", []):
                    key = entry["Key"]
                    relative = key[len(prefix):]
                    parts = relative.split("/")
                    if len(parts) != 3 or parts[1] != "enrichment" or not parts[2].endswith(".json"):
                        continue
                    try:
                        payload = json.loads(store.s3.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read())
                        enrichment = payload.get("enrichment")
                        lesson_id = str(enrichment.get("lesson_id", "")) if isinstance(enrichment, dict) else ""
                        saved_at = str(payload.get("saved_at", ""))
                        if lesson_id:
                            previous = output.get(lesson_id)
                            if previous is None or saved_at >= previous[0]:
                                output[lesson_id] = (saved_at, enrichment)
                    except Exception:
                        continue
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token:
                    raise ValueError("truncated lesson asset listing has no continuation token")
            return [item[1] for item in output.values()]

        try:
            scopes, lesson_assets = await run_in_threadpool(lambda: (read_scopes(), read_lesson_assets()))
            return {"workflow_id":workflow_id,"scope_count":len(scopes),"scopes":scopes,
                    "lesson_assets": lesson_assets}
        except Exception as exc:
            log.exception("failed to read QA scope checkpoints")
            raise HTTPException(status_code=503,detail="question checkpoints are unavailable") from exc

    ctx.provide(course_qa_status=course_qa_status, pause_course_qa=pause_course_qa, resume_course_qa=resume_course_qa)
    return router
