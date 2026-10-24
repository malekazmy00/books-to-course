"""Generic stage pause/resume controls and failed-stage retry on one resume path."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from b2c_core import ArtifactStore

from fastapi import HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from b2c_api.deps import (
    STAGE_CONTROL_ID,
    describe_or_404,
    s3_json,
)
from b2c_api.workflow_resume import (
    FAILED as RETRY_STATUSES,
    ResumeStageRequest,
    retry_failed_stage,
    retryable_kind,
    workflow_resume_router,
)

from fastapi import APIRouter
from b2c_api.deps import ApiContext


def workflow_controls_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    app = ctx.app
    settings = ctx.settings
    get_temporal_client = ctx.get_temporal_client
    connected_temporal = ctx.connected_temporal
    pause_course_qa = ctx.late("pause_course_qa")
    resume_course_qa = ctx.late("resume_course_qa")
    remember_book_progress = ctx.late("remember_book_progress")
    verify_resume_owner = ctx.late("verify_resume_owner")

    def stage_control_kind(workflow_id: str) -> str:
        match = STAGE_CONTROL_ID.fullmatch(workflow_id)
        if match is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return match.group(1)

    async def read_stage_control(workflow_id: str) -> dict[str, Any]:
        store = app.state.artifact_store or ArtifactStore.from_settings(settings)
        app.state.artifact_store = store
        try:
            return await s3_json(store.s3, store.bucket, f"_system/workflow-controls/{workflow_id}.json") or {}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="could not read pause control") from exc

    @router.get("/v1/workflows/{workflow_id}/control")
    async def stage_control_status(workflow_id: str) -> dict[str, Any]:
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return {"workflow_id": workflow_id, "control_kind": "qa_checkpoint",
                    "pause_url": f"/v1/course-qa/{workflow_id}/pause",
                    "resume_url": f"/v1/course-qa/{workflow_id}/resume"}
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id)
        record = await read_stage_control(workflow_id)
        boundary = {"ingest": "after_complete_parsed_book", "index": "after_indexed_book",
                    "translate": "after_saved_translation_batch", "mp3": "after_saved_audio_track"}[kind]
        return {"workflow_id": workflow_id, "temporal_status": description.status.name,
                "status": record.get("state", "ACTIVE"), "checkpoint_boundary": boundary,
                "checkpoint": record.get("checkpoint"), "updated_at": record.get("acknowledged_at")}

    @router.post("/v1/workflows/{workflow_id}/pause")
    async def pause_stage_workflow(workflow_id: str) -> dict[str, Any]:
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return await pause_course_qa(workflow_id)
        client = await connected_temporal()
        handle, description = await describe_or_404(client, workflow_id)
        if description.status.name != "RUNNING":
            raise HTTPException(status_code=409, detail="only a running workflow can be paused")
        if kind == "mp3":
            history = await handle.fetch_history()
            for event in history.events:
                if event.HasField("workflow_execution_started_event_attributes"):
                    values = await client.data_converter.decode(event.workflow_execution_started_event_attributes.input.payloads)
                    if values:
                        inp = values[0]
                        inp = inp.model_dump(mode="json") if hasattr(inp, "model_dump") else dict(inp)
                        if inp.get("source_kind") == "book" and inp.get("book_audio_layout", "full_book") != "lessons":
                            raise HTTPException(status_code=409, detail="whole-book audio has no per-track checkpoint; use lesson audio")
                    break
        current = await read_stage_control(workflow_id)
        if current.get("action") == "pause_after_checkpoint":
            return {"workflow_id": workflow_id, "status": current.get("state", "PAUSE_REQUESTED")}
        store = app.state.artifact_store
        record = {"workflow_id": workflow_id, "action": "pause_after_checkpoint",
                  "state": "PAUSE_REQUESTED", "requested_at": datetime.now(UTC).isoformat()}
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=store.bucket,
                Key=f"_system/workflow-controls/{workflow_id}.json",
                Body=json.dumps(record).encode(), ContentType="application/json")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="pause request could not be saved") from exc
        return {"workflow_id": workflow_id, "status": "PAUSE_REQUESTED",
                "message": "Pauses after the current durable checkpoint; it does not interrupt unfinished output."}

    @router.post("/v1/workflows/{workflow_id}/resume")
    async def resume_stage_workflow(workflow_id: str, response: Response,
                                    request: ResumeStageRequest | None = None) -> dict[str, Any]:
        """One path, dispatched by status: QA checkpoint resume, failed-stage
        retry (FAILED/TIMED_OUT/CANCELED/TERMINATED), or live paused resume."""
        kind = stage_control_kind(workflow_id)
        if kind == "qa":
            return await resume_course_qa(workflow_id)
        client = await connected_temporal()
        _, description = await describe_or_404(client, workflow_id)
        status_name = description.status.name
        if status_name in RETRY_STATUSES and retryable_kind(workflow_id):
            if request is None:
                raise HTTPException(status_code=422, detail="book_id is required to retry a failed stage")
            try:
                result = await retry_failed_stage(client, workflow_id, request.book_id,
                                                  verify_resume_owner, remember_book_progress, status_name)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=503, detail="stage could not resume safely") from exc
            response.status_code = 202
            return result
        if status_name != "RUNNING":
            raise HTTPException(status_code=409, detail="this endpoint resumes a paused live workflow; use the stage retry endpoint for a finished workflow")
        record = await read_stage_control(workflow_id)
        if record.get("action") not in {"pause_after_checkpoint", "resume"}:
            raise HTTPException(status_code=409, detail="workflow has no pending pause")
        record.update({"action": "resume", "state": "ACTIVE", "resumed_at": datetime.now(UTC).isoformat()})
        store = app.state.artifact_store
        try:
            await run_in_threadpool(store.s3.put_object, Bucket=store.bucket,
                Key=f"_system/workflow-controls/{workflow_id}.json",
                Body=json.dumps(record).encode(), ContentType="application/json")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="resume request could not be saved") from exc
        try:
            await client.get_workflow_handle(workflow_id).signal("resume_stage")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="resume was saved but signaling failed; retry resume") from exc
        return {"workflow_id": workflow_id, "status": "RESUME_REQUESTED"}

    # The resume POST is served above (status dispatch); only the capability
    # probe comes from this router so the two can never shadow each other.
    router.include_router(workflow_resume_router(get_temporal_client, verify_resume_owner,
                                                 remember_book_progress, include_resume=False))
    return router
