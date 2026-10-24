"""Validated original translation inputs, saved checkpoints and per-unit projections."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from b2c_contracts import (
    ArtifactRef,
)
from b2c_translation.generation import translation_completeness_issue
from b2c_orchestration.translation import TranslateContentInput, translation_source_segments, translation_manual_overrides

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from b2c_translation.generation import validate_manual_translation
from b2c_api.deps import (
    require_workflow_id,
    describe_or_404,
    s3_json,
)

from b2c_api.deps import ApiContext, log


def bind_translation_units(ctx: ApiContext) -> None:
    settings = ctx.settings
    get_store = ctx.get_store
    get_temporal_client = ctx.get_temporal_client
    connected_temporal = ctx.connected_temporal

    async def original_translation_input(workflow_id: str) -> TranslateContentInput:
        store = get_store()
        key = f"_system/translation-requests/{workflow_id}.json"
        saved_request = await s3_json(store.s3, settings.s3_bucket, key)
        if saved_request is not None:
            inp = TranslateContentInput.model_validate(saved_request)
        else:
            client = await get_temporal_client()
            history = await client.get_workflow_handle(workflow_id).fetch_history()
            started = next((e for e in history.events if e.HasField("workflow_execution_started_event_attributes")), None)
            if started is None:
                raise ValueError("original translation input is unavailable")
            decoded = await client.data_converter.decode(started.workflow_execution_started_event_attributes.input.payloads, [TranslateContentInput])
            if len(decoded) != 1:
                raise ValueError("original translation input is ambiguous")
            inp = TranslateContentInput.model_validate(decoded[0])
        ref = ArtifactRef.model_validate(inp.source_artifact_ref)
        expected = "ingest-parse-pdf" if inp.source_kind == "book" else "generate-course-qa"
        if ref.stage != expected or not ref.uri.startswith(f"s3://{settings.s3_bucket}/{inp.course_id}/"):
            raise ValueError("original translation source failed scope validation")
        return inp

    async def translation_checkpoints(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        store = get_store()
        prefix=f"_system/translation-checkpoints/{workflow_id}/"
        def read_batches() -> list[dict[str,Any]]:
            output=[]
            token=None
            while True:
                args={"Bucket":settings.s3_bucket,"Prefix":prefix}
                if token:
                    args["ContinuationToken"]=token
                page=store.s3.list_objects_v2(**args)
                for entry in page.get("Contents",[]):
                    key=entry["Key"]
                    relative=key[len(prefix):]
                    parts=relative.split("/")
                    if len(parts)!=2 or not parts[0] or not parts[1].startswith("batch-") or not parts[1].endswith(".json"):
                        continue
                    try:
                        payload=json.loads(store.s3.get_object(Bucket=settings.s3_bucket,Key=key)["Body"].read())
                        if payload.get("run_fingerprint")!=parts[0] or int(payload.get("batch_index",0))<1:
                            continue
                        segments=payload.get("segments")
                        if not isinstance(segments,list) or not segments:
                            continue
                        if any(not isinstance(x,dict) or not x.get("segment_id") or not x.get("text") for x in segments):
                            continue
                    except Exception:
                        continue
                    output.append({"run_fingerprint":parts[0],"batch_index":payload["batch_index"],
                        "source_sha256":payload.get("source_sha256"),"model_id":payload.get("model_id"),"source_language":payload.get("source_language"),
                        "target_language":payload.get("target_language"),"completed_at":payload.get("completed_at"),
                        "segments":segments})
                if not page.get("IsTruncated"):
                    break
                token=page.get("NextContinuationToken")
                if not token:
                    raise ValueError("truncated translation checkpoint listing has no continuation token")
            return output
        try:
            batches=await run_in_threadpool(read_batches)
            return {"workflow_id":workflow_id,"batch_count":len(batches),"batches":batches}
        except Exception as exc:
            log.exception("failed to read translation batch checkpoints")
            raise HTTPException(status_code=503,detail="translation checkpoints are unavailable") from exc

    async def translation_units(workflow_id: str) -> dict[str, Any]:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        try:
            original = await original_translation_input(workflow_id)
            store = get_store()
            source = await run_in_threadpool(translation_source_segments, store, original)
            # A resumed workflow gets a new ID but must keep exposing its
            # parent's durable work while it processes the remaining units.
            # Timestamp ordering lets newer child checkpoints supersede older ones.
            checkpoint_workflow_ids = list(dict.fromkeys(
                item for item in (original.resume_from_workflow_id, workflow_id) if item
            ))
            checkpoint_batches: list[dict[str, Any]] = []
            for checkpoint_workflow_id in checkpoint_workflow_ids:
                checkpoint_batches.extend(
                    (await translation_checkpoints(checkpoint_workflow_id))["batches"]
                )
            source_ids = {x.segment_id for x in source}
            saved: dict[str, dict[str, Any]] = {}
            for batch in sorted(checkpoint_batches, key=lambda b: b.get("completed_at") or ""):
                if batch.get("source_sha256") != original.source_artifact_ref["sha256"] or batch.get("source_language") != original.source_language or batch.get("target_language") != original.target_language:
                    continue
                for segment in batch["segments"]:
                    if segment["segment_id"] in source_ids:
                        saved[segment["segment_id"]] = segment
            overrides: list[dict[str, Any]] = []
            for override_workflow_id in checkpoint_workflow_ids:
                overrides.extend(await run_in_threadpool(
                    translation_manual_overrides, store, original, override_workflow_id
                ))
            overrides.sort(key=lambda item: (item["created_at"], item["revision_id"]))
            latest = {x["segment_id"]:x for x in overrides}
            units = [{"segment_id":x.segment_id,"source_text":x.text,"citations":x.citations,
                "source_artifact_ref":original.source_artifact_ref,
                "translated_text":latest.get(x.segment_id, saved.get(x.segment_id, {})).get("text"),
                "original_translated_text":saved.get(x.segment_id,{}).get("text"),
                "revisions":[r for r in overrides if r["segment_id"] == x.segment_id],
                "manual_override_revision":latest.get(x.segment_id, {}).get("revision_id"),
                "checkpoint_status":"saved" if x.segment_id in saved else "not_saved"} for x in source]
            for unit in units:
                text = unit["translated_text"]
                issue = translation_completeness_issue(unit["source_text"], text, target_language=original.target_language) if text is not None else None
                unit["completeness_issue"] = issue
                unit["suspect"] = issue is not None
                if issue:
                    unit["checkpoint_status"] = "suspect"
            suspect = [u for u in units if u["suspect"]]
            valid_saved = sum(u["checkpoint_status"] == "saved" for u in units)
            return {"workflow_id":workflow_id,"course_id":original.course_id,"source_kind":original.source_kind,
                "total_segments":len(source),"saved_segments":valid_saved,"checkpointed_segments":len(saved),
                "retry_segments":len(source)-valid_saved,"suspect_segments":len(suspect),
                "suspect_segment_ids":[u["segment_id"] for u in suspect],
                "suspect_details":[{"segment_id":u["segment_id"], **u["completeness_issue"]} for u in suspect],
                "completeness_policy":"exact ordered numeric sequence; recognized rating-table cells/literals preserved; balanced-source brackets; no introduced markup/provenance; long English prose blocks in Arabic output are rejected; conservative length triage (source>=200 chars and target/source<0.45)",
                "manual_correction_count":len(latest),"units":units}
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("translation units unavailable")
            raise HTTPException(status_code=503, detail="original source or checkpoints could not be verified") from exc

    async def terminal_translation(workflow_id: str) -> TranslateContentInput:
        require_workflow_id("translate", workflow_id, "translation workflow not found")
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id, "translation workflow not found")
        if getattr(getattr(description, "status", None), "name", "") not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}:
            raise HTTPException(status_code=409, detail="selected-unit changes require a terminal parent translation")
        return await original_translation_input(workflow_id)

    async def selected_candidate(workflow_id: str, candidate_id: str, revision_id: str | None = None) -> dict[str, Any]:
        require_workflow_id("retranslate", candidate_id, "candidate not found")
        original = await original_translation_input(workflow_id)
        store = get_store()
        record = await s3_json(store.s3, settings.s3_bucket, (f"_system/translation-candidate-revisions/{candidate_id}/{revision_id}.json" if revision_id else f"_system/translation-candidates/{candidate_id}.json"))
        if record is None:
            raise HTTPException(status_code=409, detail="candidate is not ready for review")
        sources = await run_in_threadpool(translation_source_segments, store, original)
        matches = [x for x in sources if x.segment_id == record.get("segment_id")]
        if (record.get("parent_workflow_id") != workflow_id or record.get("candidate_workflow_id") != candidate_id
            or record.get("source_artifact_ref") != original.source_artifact_ref
            or record.get("source_language") != original.source_language or record.get("target_language") != original.target_language
            or len(matches) != 1 or record.get("source_text_sha256") != hashlib.sha256(matches[0].text.encode()).hexdigest()):
            raise HTTPException(status_code=422, detail="candidate source identity mismatch")
        if revision_id and record.get("revision_id") != revision_id:
            raise HTTPException(status_code=422, detail="candidate revision identity mismatch")
        try:
            record["text"] = validate_manual_translation(matches[0].text, record["text"], target_language=original.target_language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return record

    ctx.provide(original_translation_input=original_translation_input, translation_checkpoints=translation_checkpoints, translation_units=translation_units, terminal_translation=terminal_translation, selected_candidate=selected_candidate)
