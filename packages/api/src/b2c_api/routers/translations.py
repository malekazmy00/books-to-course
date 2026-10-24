"""Translation requests, status/progress, selected-unit retranslation and corrections."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from b2c_contracts import (
    ArtifactRef,
    ParsedBook,
)
from b2c_core import ArtifactStore
from b2c_core.source_identity import artifact_source_identity
from b2c_translation.glossary import glossary_identity
from b2c_orchestration.translation import TranslateContentInput, TranslateContentWorkflow, RetranslateSegmentInput, RetranslateSegmentWorkflow, translation_source_segments, translation_manual_overrides

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from temporalio.exceptions import WorkflowAlreadyStartedError
from b2c_translation.generation import validate_manual_translation
from b2c_api.deps import (
    require_workflow_id,
    PROGRESS_WARNING,
    describe_or_404,
    with_progress_warning,
    s3_json,
    verified_ref,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext, MissingObject, REUSE_ONLY_AFTER_FAILURE, log
from b2c_api.schemas import TranslationRequest, TranslationRetranslateRequest, TranslationRevisionSelectionRequest, TranslationCandidateAcceptRequest, TranslationCorrectionRequest


def translations_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    app = ctx.app
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    connected_temporal = ctx.connected_temporal
    generation_profile = ctx.generation_profile
    remember_book_progress = ctx.late("remember_book_progress")
    original_translation_input = ctx.late("original_translation_input")
    terminal_translation = ctx.late("terminal_translation")
    selected_candidate = ctx.late("selected_candidate")
    translation_units = ctx.late("translation_units")
    translation_checkpoints = ctx.late("translation_checkpoints")

    @router.post("/v1/courses/{course_id}/translations", status_code=202)
    async def translate_course_content(course_id: str, request: TranslationRequest) -> dict[str, Any]:
        source = request.source_artifact_ref
        expected_stage = "ingest-parse-pdf" if request.source_kind == "book" else "generate-course-qa"
        if source.stage != expected_stage or not source.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
            raise HTTPException(status_code=422, detail="source artifact is outside this course or has the wrong stage")
        if request.source_language == request.target_language:
            raise HTTPException(status_code=422, detail="source and target language must differ")
        await verified_ref(get_store(), course_id, source, expected_stage, "source artifact")
        try:
            if request.resume_from_workflow_id:
                previous = await original_translation_input(request.resume_from_workflow_id)
                if previous.course_id != course_id or previous.source_artifact_ref != source.model_dump(mode="json") or any(getattr(previous,name) != getattr(request,name) for name in
                    ("source_kind", "source_language", "target_language", "max_tokens", "contextual_glossary")):
                    raise HTTPException(status_code=422, detail="resume request must match the validated original translation input")
            correction_ids = []
            if request.resume_from_workflow_id:
                correction_store = app.state.artifact_store
                corrections = await run_in_threadpool(translation_manual_overrides, correction_store, previous, request.resume_from_workflow_id)
                correction_ids = sorted(x["revision_id"] for x in corrections)
            correction_identity = hashlib.sha256(json.dumps(correction_ids).encode()).hexdigest() if correction_ids else ""
            client = await get_temporal_client()
            if request.provider_id:
                manager = app.state.model_lifecycle
                provider = manager.provider(request.provider_id)
                if "translation" not in provider.stages or not provider.task_queue:
                    raise HTTPException(409, "provider has no enabled translation worker route")
                if (await manager.status(provider.id))["state"] != "ready":
                    raise HTTPException(503, "selected translation model service is not ready")
                selected_model, selected_queue, selected_provider = provider.model, provider.task_queue, provider.id
                selected_translation_engine = provider.translation_engine
            elif request.resume_from_workflow_id:
                selected_model = previous.selected_model_id or settings.llm_model_id
                selected_queue = previous.activity_task_queue
                selected_provider = previous.selected_provider_id or "configured-llm"
                selected_translation_engine = previous.selected_translation_engine
            else:
                selected_model, selected_queue, selected_provider = generation_profile("translation")
                configured_provider = app.state.model_lifecycle.providers.get(selected_provider)
                selected_translation_engine = (
                    configured_provider.translation_engine if configured_provider else "b2c"
                )
            nonce = uuid.uuid4().hex if request.force_regenerate else ""
            reasoning_identity = f"\0reasoning:{settings.llm_reasoning_effort}" if settings.llm_reasoning_effort else ""
            suffix = hashlib.sha256((course_id + source.sha256 + request.source_kind +
                request.source_language + request.target_language + selected_model +
                str(request.max_tokens) + "\0provider:" + selected_provider + "\0queue:" + selected_queue + (request.resume_from_workflow_id or "") +
                reasoning_identity + correction_identity + "\0glossary:" + glossary_identity(request.contextual_glossary) + nonce).encode()).hexdigest()
            workflow_id = "b2c-translate-" + suffix
            inp = TranslateContentInput(course_id=course_id, source_artifact_ref=source.model_dump(mode="json"),
                source_kind=request.source_kind, source_language=request.source_language,
                target_language=request.target_language, max_tokens=request.max_tokens,
                resume_from_workflow_id=request.resume_from_workflow_id, manual_override_revision_ids=correction_ids,
                activity_task_queue=selected_queue, selected_model_id=selected_model,
                selected_provider_id=selected_provider,
                selected_translation_engine=selected_translation_engine,
                contextual_glossary=request.contextual_glossary)
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/translation-requests/{workflow_id}.json",
                Body=inp.model_dump_json().encode("utf-8"), ContentType="application/json")
            try:
                await client.start_workflow(TranslateContentWorkflow.run, inp, id=workflow_id, task_queue=inp.activity_task_queue,
                                            id_reuse_policy=REUSE_ONLY_AFTER_FAILURE)
            except WorkflowAlreadyStartedError:
                pass
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to queue translation workflow")
            raise HTTPException(status_code=503, detail="translation could not be queued") from exc
        progress_warning = None
        try:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            book_id = None
            source_parsed_sha256 = None
            outline_sha256 = None
            if request.source_kind == "book":
                parsed_book = ParsedBook.model_validate_json(await run_in_threadpool(
                    store.read, source, "parsed-book.json"))
                book_id = parsed_book.book_id
                source_parsed_sha256 = source.sha256
            else:
                qa_source = json.loads(await run_in_threadpool(store.read, source, "course-qa.json"))
                book_id = qa_source.get("spine_book_id")
                source_parsed_sha256 = qa_source.get("spine_parsed_sha256")
                outline_sha256 = qa_source.get("approved_outline_sha256")
            if book_id and source_parsed_sha256:
                progress_warning = await remember_book_progress(course_id, book_id,
                    "book_translation" if request.source_kind == "book" else "translation", workflow_id, {
                        "source_parsed_sha256": source_parsed_sha256,
                        "source_artifact_sha256": source.sha256,
                        "source_identity": await run_in_threadpool(artifact_source_identity, store, source),
                        "source_kind": request.source_kind,
                        "target_language": request.target_language,
                        "outline_sha256": outline_sha256,
                    })
        except Exception:
            # Book association reads stored source artifacts; it is best-effort
            # because the workflow is already queued.
            log.exception("could not associate translation workflow with its exact book source")
            progress_warning = PROGRESS_WARNING
        return with_progress_warning({"course_id": course_id, "workflow_id": workflow_id,
                                      "status_url": f"/v1/translations/{workflow_id}"}, progress_warning)

    @router.get("/v1/translations/{workflow_id}")
    async def translation_status(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        handle, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        status_name = getattr(getattr(description, "status", None), "name", "")
        response: dict[str, Any] = {"workflow_id": workflow_id, "status": status_name}
        if status_name == "RUNNING":
            # Durable progress is written by the activity itself. Temporal event enum
            # encodings vary across SDK versions, so do not infer queue state from a
            # magic event number; an absent progress object is handled below as queued.
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            progress_key = f"_system/translation-progress/{workflow_id}.json"
            try:
                progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
                if progress is None:
                    raise MissingObject(progress_key)
                now = datetime.now(UTC)
                checkpoint = progress.get("last_progress_at") or progress.get("started_at") or progress.get("updated_at")
                age_seconds = None
                if checkpoint:
                    age_seconds = max(0, int((now - datetime.fromisoformat(checkpoint.replace("Z", "+00:00"))).total_seconds()))
                state = "active" if age_seconds is None or age_seconds < 600 else "no_recent_checkpoint"
                progress["progress_state"] = state
                progress["checkpoint_age_seconds"] = age_seconds
                done = int(progress.get("completed_segments", 0))
                total = int(progress.get("total_segments", 0))
                progress["remaining_segments"] = max(0, total - done) if total else None
                progress["successful_segments"] = done
                current_segment_ids = set(progress.get("current_segment_ids", []))
                failed_segment_ids = set(progress.get("failed_segment_ids", []))
                progress["active_segments"] = len(current_segment_ids)
                # deferred_batches is transient and excludes the batch currently
                # being retried; unresolved per-segment failures are durable.
                progress["deferred_units_count"] = len(failed_segment_ids - current_segment_ids)
                progress["failed_segments"] = len(failed_segment_ids)
                progress["completion_percent"] = round(100 * done / total, 1) if total else None
                if progress.get("target_language") == "ar" and not isinstance(progress.get("arabic_correction"), dict):
                    progress["arabic_correction"] = {
                        "status": "waiting_for_translation",
                        "processed_segments": 0,
                        "total_segments": total,
                        "remaining_segments": total,
                        "accepted_edits": 0,
                        "rejected_edits": 0,
                        "message": "يبدأ التصحيح العربي بعد انتهاء الترجمة؛ هذا عداد مستقل عن عداد الترجمة.",
                    }
                citations = progress.get("completed_citations", [])
                current = progress.get("current_citations", [])
                if state == "no_recent_checkpoint":
                    progress["message"] = f"\u0644\u0645 \u064a\u0633\u062c\u0644 \u062a\u0642\u062f\u0645 \u0645\u0646\u0630 {age_seconds // 60} \u062f\u0642\u064a\u0642\u0629\u061b \u0642\u062f \u064a\u0643\u0648\u0646 \u0627\u0644\u0637\u0644\u0628 \u0628\u0637\u064a\u0626\u064b\u0627 \u0623\u0648 \u0645\u062a\u0648\u0642\u0641\u064b\u0627."
                else:
                    progress["message"] = (
                        f"\u062a\u0631\u062c\u0645\u0629 {done}/{total} \u0645\u0642\u0637\u0639\u064b\u0627\u061b "
                        f"\u0627\u0644\u062f\u0641\u0639\u0629 {progress.get('batch_index', 0)}/"
                        f"{progress.get('total_batches', 0)}. "
                        f"\u0627\u0644\u0635\u0641\u062d\u0627\u062a \u0627\u0644\u0645\u0643\u062a\u0645\u0644\u0629: {', '.join(citations[-12:]) or '\u062c\u0627\u0631\u064d \u0627\u0644\u062a\u062d\u0636\u064a\u0631'}; "
                        f"\u0627\u0644\u062f\u0641\u0639\u0629 \u0627\u0644\u062d\u0627\u0644\u064a\u0629: {', '.join(current) or '\u0644\u0627 \u062a\u0648\u062c\u062f \u062f\u0641\u0639\u0629 \u0646\u0634\u0637\u0629'}."
                    )
                response["progress"] = progress
            except Exception as exc:
                if not isinstance(exc, MissingObject):
                    log.exception("failed to read translation progress checkpoint")
                    raise HTTPException(status_code=503, detail="translation progress is unavailable") from exc
                response["progress"] = {
                    "progress_state": "not_reported",
                    "checkpointing": False,
                    "message": "\u0627\u0644\u0631\u062d\u0644\u0629 \u0642\u064a\u062f \u0627\u0644\u062a\u0646\u0641\u064a\u0630 \u0644\u0643\u0646 \u0644\u0627 \u064a\u0648\u062c\u062f \u0633\u062c\u0644 \u062a\u0642\u062f\u0645 \u0645\u062d\u0641\u0648\u0638; \u0644\u0627 \u064a\u0645\u0643\u0646 \u062a\u062d\u062f\u064a\u062f \u0627\u0644\u0645\u0642\u0627\u0637\u0639 \u0627\u0644\u0645\u0646\u062c\u0632\u0629 \u062d\u062a\u0649 \u0627\u0644\u0622\u0646.",
                }
        if status_name == "COMPLETED":
            try:
                result = await handle.result()
            except Exception as exc:
                log.exception("failed to read completed translation result")
                raise HTTPException(status_code=503, detail="translation result is unavailable") from exc
            response["result"] = result
            count = int(result.get("segment_count", 0))
            total = int(result.get("total_segments", count))
            partial = result.get("status") == "partial"
            response["result_status"] = "partial" if partial else "completed"
            response["progress"] = {"completed_segments": count, "successful_segments": count,
                                     "total_segments": total, "active_segments": 0,
                                     "deferred_units_count": 0,
                                     "failed_segments": len(result.get("failed_segment_ids", [])),
                                     "failed_segment_ids": result.get("failed_segment_ids", []),
                                     "missing_segment_ids": result.get("missing_segment_ids", []),
                                     "retry_segment_ids": result.get("retry_segment_ids", []),
                                     "retry_segments": max(0, total - count),
                                     "completion_percent": round(100 * count / total, 1) if total else 0,
                                     "progress_state": "partial" if partial else "completed",
                                     "message": f"حُفظت ترجمة {count}/{total} مقطعًا؛ يمكن إعادة الأجزاء الناقصة." if partial else f"اكتملت ترجمة {count} مقطعًا."}
            course_id = result.get("course_id", "")
            ref = ArtifactRef.model_validate(result["artifact"])
            if ref.stage != "translate-content" or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{course_id}/"):
                raise HTTPException(status_code=503, detail="translation artifact failed scope validation")
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                response["translation"] = json.loads(await run_in_threadpool(store.read, ref, "translation.json"))
            except Exception as exc:
                log.exception("failed to read completed translation artifact")
                raise HTTPException(status_code=503, detail="translation artifact is unavailable") from exc
        elif status_name in {"FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            store = app.state.artifact_store or ArtifactStore.from_settings(settings)
            app.state.artifact_store = store
            try:
                progress_key = f"_system/translation-progress/{workflow_id}.json"
                progress = await s3_json(store.s3, settings.s3_bucket, progress_key)
                if progress is None:
                    raise MissingObject(progress_key)
                progress["successful_segments"] = int(progress.get("completed_segments", 0))
                progress["active_segments"] = len(progress.get("current_segment_ids", []))
                progress["deferred_units_count"] = len(progress.get("deferred_batches", []))
                progress["failed_segments"] = len(progress.get("failed_segment_ids", []))
                total = int(progress.get("total_segments", 0))
                done = int(progress.get("completed_segments", 0))
                progress["completion_percent"] = round(100 * done / total, 1) if total else None
                response["progress"] = progress
            except Exception as progress_error:
                if not isinstance(progress_error, MissingObject):
                    log.exception("failed to read terminal translation progress checkpoint")
                    raise HTTPException(status_code=503, detail="translation progress is unavailable") from progress_error
                response["progress"] = {"progress_state": "not_reported", "completed_segments": 0,
                                         "total_segments": 0, "message": "لا توجد نقاط تقدم محفوظة لهذا التشغيل."}
            try:
                await handle.result()
            except Exception as exc:
                cause = getattr(exc, "cause", None)
                response["failure"] = {"type": getattr(cause, "type", type(cause or exc).__name__)}
        try:
            original = await original_translation_input(workflow_id)
            from temporalio.api.enums.v1 import PendingActivityState
            pending_activities = getattr(getattr(description, "raw_description", None), "pending_activities", ())
            has_started_activity = any(a.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
                                       for a in pending_activities)
            progress_state = response.get("progress", {}).get("progress_state")
            if status_name == "RUNNING" and has_started_activity and progress_state == "no_recent_checkpoint":
                heartbeats = [getattr(a, "last_heartbeat_time", None) for a in pending_activities
                              if a.state == PendingActivityState.PENDING_ACTIVITY_STATE_STARTED]
                heartbeats = [value.ToDatetime() if hasattr(value, "ToDatetime") else value
                              for value in heartbeats if value is not None]
                if heartbeats:
                    latest_heartbeat = max(heartbeats)
                    if latest_heartbeat.tzinfo is None:
                        latest_heartbeat = latest_heartbeat.replace(tzinfo=UTC)
                    heartbeat_age = max(0, int((datetime.now(UTC) - latest_heartbeat).total_seconds()))
                    if heartbeat_age <= 180:
                        progress = response["progress"]
                        progress["progress_state"] = "active"
                        progress["activity_heartbeat_age_seconds"] = heartbeat_age
                        progress["segment_checkpoint_state"] = "waiting_during_active_request"
                        progress["message"] = (
                            f"العامل نشط ويرسل نبضات حياة؛ لم يُحفظ مقطع جديد منذ "
                            f"{int(progress.get('checkpoint_age_seconds', 0)) // 60} دقيقة، "
                            "وقد يكون النموذج ما زال يعالج الطلب الحالي."
                        )
            if (status_name == "RUNNING" and original.resume_from_workflow_id
                    and (progress_state == "not_reported"
                         or (progress_state == "no_recent_checkpoint" and not has_started_activity))):
                # A continuation can wait in the queue before its activity writes
                # its first heartbeat. Show validated immutable parent checkpoints.
                units = await translation_units(workflow_id)
                total, done = units["total_segments"], units["saved_segments"]
                parent_key = f"_system/translation-progress/{original.resume_from_workflow_id}.json"
                try:
                    parent_progress = await s3_json(store.s3, settings.s3_bucket, parent_key) or {}
                    missing = {u["segment_id"] for u in units["units"] if u["checkpoint_status"] != "saved"}
                    failed_ids = sorted(set(parent_progress.get("failed_segment_ids", [])) & missing)
                except Exception:
                    failed_ids = []
                response["progress"] = {
                    "progress_state": "queued", "checkpointing": True,
                    "inherited_checkpoint_workflow_id": original.resume_from_workflow_id,
                    "total_segments": total, "completed_segments": done,
                    "successful_segments": done, "checkpointed_segments": units["checkpointed_segments"],
                    "remaining_segments": max(0, total - done), "retry_segments": units["retry_segments"],
                    "suspect_segments": units["suspect_segments"], "active_segments": 0,
                    "failed_segments": len(failed_ids), "failed_segment_ids": failed_ids, "deferred_units_count": 0,
                    "completion_percent": round(100 * done / total, 1) if total else 0,
                    "message": f"محفوظ {done}/{total} مقطعًا من نقطة الاستئناف؛ ينتظر بدء المعالجة."
                }
            if status_name in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
                units = await translation_units(workflow_id)
                response["manual_correction_count"] = units["manual_correction_count"]
                progress = response.setdefault("progress", {})
                missing_ids = {u["segment_id"] for u in units["units"] if u["checkpoint_status"] == "not_saved"}
                confirmed_failed_ids = set(progress.get("failed_segment_ids", [])) & missing_ids
                progress["failed_segments"] = len(confirmed_failed_ids)
                progress["failed_segment_ids"] = sorted(confirmed_failed_ids)
                progress["not_saved_segments"] = len(missing_ids)
                progress["missing_segment_ids"] = sorted(missing_ids)
                progress["retry_segment_ids"] = [u["segment_id"] for u in units["units"] if u["checkpoint_status"] != "saved"]
                progress["suspect_segments"] = units["suspect_segments"]
                progress["suspect_segment_ids"] = units["suspect_segment_ids"]
                progress["suspect_details"] = units["suspect_details"]
                progress["checkpointed_segments"] = units["checkpointed_segments"]
                progress["completeness_policy"] = units["completeness_policy"]
                progress["remaining_unconfirmed_segments"] = len(missing_ids - confirmed_failed_ids)
                progress.update({"total_segments":units["total_segments"],"completed_segments":units["saved_segments"],
                    "successful_segments":units["saved_segments"],"retry_segments":units["retry_segments"],
                    "active_segments":0,"completion_percent":round(100*units["saved_segments"]/units["total_segments"],1) if units["total_segments"] else 0})
            response["course_id"] = original.course_id
            response["resume_request"] = {name: getattr(original, name) for name in
                ("source_artifact_ref", "source_kind", "source_language", "target_language", "max_tokens")}
            if original.contextual_glossary is not None:
                response["resume_request"]["contextual_glossary"] = original.contextual_glossary.model_dump(mode="json")
        except Exception:
            response["resume_unavailable_reason"] = "original translation input could not be validated"
        return response

    @router.post("/v1/translations/{workflow_id}/retranslations", status_code=202)
    async def retranslate_unit(workflow_id: str, request: TranslationRetranslateRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        store = app.state.artifact_store
        units = await translation_units(workflow_id)
        matches = [x for x in units["units"] if x["segment_id"] == request.segment_id]
        if len(matches) != 1:
            raise HTTPException(status_code=404, detail="selected segment missing or ambiguous")
        unit = matches[0]
        # Persist exact recovered history input, so worker checks the same original request.
        await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
            Key=f"_system/translation-requests/{workflow_id}.json", Body=original.model_dump_json().encode(), ContentType="application/json")
        inp = RetranslateSegmentInput(original=original, parent_workflow_id=workflow_id,
            segment_id=request.segment_id, source_text_sha256=hashlib.sha256(unit["source_text"].encode()).hexdigest())
        # Deterministic per (parent, segment, source text, current text, client
        # nonce): a retried POST returns the same candidate; a new attempt after
        # an accepted revision, or with a fresh nonce, gets a new candidate.
        current_text_sha = hashlib.sha256((unit.get("translated_text") or "").encode()).hexdigest()
        candidate_id = "b2c-retranslate-" + hashlib.sha256("\0".join((
            workflow_id, request.segment_id, inp.source_text_sha256, current_text_sha,
            request.request_nonce or "")).encode()).hexdigest()[:32]
        client = await connected_temporal()
        try:
            await client.start_workflow(RetranslateSegmentWorkflow.run, inp, id=candidate_id,
                                        task_queue=original.activity_task_queue,
                                        id_reuse_policy=REUSE_ONLY_AFTER_FAILURE)
        except WorkflowAlreadyStartedError:
            pass
        return {"workflow_id":workflow_id,"candidate_workflow_id":candidate_id,"segment_id":request.segment_id,
            "status":"queued","other_segments_unchanged":True}

    @router.get("/v1/translations/{workflow_id}/retranslations/{candidate_id}")
    async def retranslation_status(workflow_id: str, candidate_id: str) -> dict[str, Any]:
        ready = None
        try:
            ready = await selected_candidate(workflow_id,candidate_id)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
        if ready is not None:
            # Immutable saved candidates remain reviewable after Temporal retention expires.
            status = "COMPLETED"
        else:
            client = await get_temporal_client()
            handle = client.get_workflow_handle(candidate_id)
            history = await handle.fetch_history()
            started = next((e for e in history.events if e.HasField("workflow_execution_started_event_attributes")), None)
            if started is None:
                raise HTTPException(status_code=404, detail="candidate request missing")
            decoded = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [RetranslateSegmentInput])
            if len(decoded) != 1 or RetranslateSegmentInput.model_validate(decoded[0]).parent_workflow_id != workflow_id:
                raise HTTPException(status_code=404, detail="candidate does not belong to this translation")
            description = await handle.describe()
            status = getattr(getattr(description,"status",None),"name","")
        response = {"candidate_workflow_id":candidate_id,"workflow_id":workflow_id,"status":status}
        if status == "COMPLETED":
            response["candidate"] = ready or await selected_candidate(workflow_id, candidate_id)
            original = await original_translation_input(workflow_id)
            sources = await run_in_threadpool(translation_source_segments, app.state.artifact_store, original)
            response["source_text"] = next(x.text for x in sources if x.segment_id == response["candidate"]["segment_id"])
            units = await translation_units(workflow_id)
            response["current_text"] = next(x["translated_text"] for x in units["units"] if x["segment_id"] == response["candidate"]["segment_id"])
            def candidate_revisions() -> list[dict[str, Any]]:
                result = []
                store = app.state.artifact_store
                for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket, Prefix=f"_system/translation-candidate-revisions/{candidate_id}/"):
                    for item in page.get("Contents", []):
                        revision = json.loads(store.s3.get_object(Bucket=settings.s3_bucket, Key=item["Key"])["Body"].read())
                        result.append(revision)
                return sorted(result,key=lambda x:(x["created_at"],x["revision_id"]))
            response["revisions"] = await run_in_threadpool(candidate_revisions)
        elif status in {"FAILED","TIMED_OUT","CANCELED","TERMINATED"}:
            response["detail"] = "Selected-unit retranslation failed; original saved translation remains available."
        return response

    @router.post("/v1/translations/{workflow_id}/retranslations/{candidate_id}/revisions", status_code=201)
    async def edit_retranslation_candidate(workflow_id: str, candidate_id: str, request: TranslationCorrectionRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        candidate = await selected_candidate(workflow_id,candidate_id)
        if request.segment_id != candidate["segment_id"]:
            raise HTTPException(status_code=422,detail="revision must edit the selected candidate segment")
        sources = await run_in_threadpool(translation_source_segments,app.state.artifact_store,original)
        source = next(x for x in sources if x.segment_id == request.segment_id)
        try:
            text = validate_manual_translation(source.text,request.translated_text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        revision = {**candidate,"text":text,"revision_id":uuid.uuid4().hex,
            "created_at":datetime.now(UTC).isoformat(),"origin":"operator_candidate_edit","status":"awaiting_review"}
        await run_in_threadpool(app.state.artifact_store.s3.put_object,Bucket=settings.s3_bucket,
            Key=f"_system/translation-candidate-revisions/{candidate_id}/{revision['revision_id']}.json",
            Body=json.dumps(revision,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        return revision

    @router.post("/v1/translations/{workflow_id}/retranslations/{candidate_id}/accept", status_code=201)
    async def accept_retranslation(workflow_id: str, candidate_id: str, request: TranslationCandidateAcceptRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        record = await selected_candidate(workflow_id, candidate_id, request.revision_id)
        # Candidate-derived revision ID makes repeated acceptance idempotent.
        revision_id = hashlib.sha256((candidate_id + (request.revision_id or "")).encode()).hexdigest()
        override = {**record,"workflow_id":workflow_id,"revision_id":revision_id,
            "created_at":datetime.now(UTC).isoformat(),"origin":"operator_accepted_retranslation"}
        store = app.state.artifact_store
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
                Key=f"_system/translation-overrides/{workflow_id}/{revision_id}.json",
                Body=json.dumps(override,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        except Exception as exc:
            if getattr(exc,"response",{}).get("ResponseMetadata",{}).get("HTTPStatusCode") != 412:
                raise
        return {"workflow_id":workflow_id,"segment_id":record["segment_id"],"revision_id":revision_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @router.post("/v1/translations/{workflow_id}/revision-selection", status_code=201)
    async def select_translation_revision(workflow_id: str, request: TranslationRevisionSelectionRequest) -> dict[str, Any]:
        original = await terminal_translation(workflow_id)
        units = await translation_units(workflow_id)
        matches = [x for x in units["units"] if x["segment_id"] == request.segment_id]
        if len(matches) != 1:
            raise HTTPException(status_code=404,detail="source segment missing or ambiguous")
        unit = matches[0]
        if request.revision_id is None:
            text = unit.get("original_translated_text")
            if not text:
                raise HTTPException(status_code=409,detail="original machine translation is not checkpointed")
        else:
            versions = [x for x in unit["revisions"] if x["revision_id"] == request.revision_id]
            if len(versions) != 1:
                raise HTTPException(status_code=404,detail="selected saved revision not found")
            text = versions[0]["text"]
        try:
            text = validate_manual_translation(unit["source_text"],text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revision_id = uuid.uuid4().hex
        selection = {"workflow_id":workflow_id,"revision_id":revision_id,"segment_id":request.segment_id,
            "created_at":datetime.now(UTC).isoformat(),"text":text,"source_sha256":original.source_artifact_ref["sha256"],
            "source_artifact_ref":original.source_artifact_ref,"source_language":original.source_language,
            "target_language":original.target_language,"citations":unit["citations"],
            "origin":"operator_revision_selection","selected_revision_id":request.revision_id,
            "selected_original_machine_translation":request.revision_id is None}
        await run_in_threadpool(app.state.artifact_store.s3.put_object,Bucket=settings.s3_bucket,
            Key=f"_system/translation-overrides/{workflow_id}/{revision_id}.json",
            Body=json.dumps(selection,ensure_ascii=False).encode(),ContentType="application/json",IfNoneMatch="*")
        return {"workflow_id":workflow_id,"segment_id":request.segment_id,"revision_id":revision_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @router.post("/v1/translations/{workflow_id}/corrections", status_code=201)
    async def correct_translation_unit(workflow_id: str, request: TranslationCorrectionRequest) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        status = getattr(getattr(description, "status", None), "name", "")
        if status not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise HTTPException(status_code=409, detail="corrections require a terminal translation; active work cannot be edited")
        original = await original_translation_input(workflow_id)
        store = app.state.artifact_store
        sources = await run_in_threadpool(translation_source_segments, store, original)
        source = next((x for x in sources if x.segment_id == request.segment_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="segment not found in original source")
        try:
            text = validate_manual_translation(source.text, request.translated_text, target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = {"workflow_id":workflow_id,"revision_id":uuid.uuid4().hex,"created_at":datetime.now(UTC).isoformat(),
            "segment_id":source.segment_id,"text":text,"source_sha256":original.source_artifact_ref["sha256"],
            "source_language":original.source_language,"target_language":original.target_language,
            "source_artifact_ref":original.source_artifact_ref,"citations":source.citations,"origin":"operator_manual_edit"}
        await run_in_threadpool(store.s3.put_object, Bucket=settings.s3_bucket,
            Key=f"_system/translation-overrides/{workflow_id}/{record['revision_id']}.json",
            Body=json.dumps(record, ensure_ascii=False).encode("utf-8"), ContentType="application/json", IfNoneMatch="*")
        return {"workflow_id":workflow_id,"revision_id":record["revision_id"],"segment_id":source.segment_id,
            "status":"saved_for_resume","original_checkpoints_unchanged":True}

    @router.get("/v1/translations/{workflow_id}/checkpoints")
    async def translation_checkpoints_view(workflow_id: str) -> dict[str, Any]:
        return await translation_checkpoints(workflow_id)

    @router.get("/v1/translations/{workflow_id}/units")
    async def translation_units_view(workflow_id: str) -> dict[str, Any]:
        return await translation_units(workflow_id)

    return router
