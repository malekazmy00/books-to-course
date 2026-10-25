"""Operator view of stage outputs and their acceptance status (R1).

Read-only: candidate / accepted / rejected / quarantined, the gate results and the status
history of each artifact. Status changes happen in the workflows (promotion API), never here.
Learner routes do not use this router; they guard content with ``deps.require_accepted_artifact``.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from b2c_api.deps import ApiContext, log, require_course_id

STATUSES = ("candidate", "accepted", "rejected", "quarantined")
STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
KEY = re.compile(r"[0-9a-f]{64}")


def _summary(record: Any) -> dict[str, Any]:
    latest = record.latest_gate_results()
    return {"course_id": record.course_id, "stage": record.stage, "key": record.stage_key,
            "uri": record.artifact.uri, "sha256": record.artifact.sha256,
            "created_at": record.artifact.created_at.isoformat(), "status": record.status,
            "status_reason": record.status_reason, "status_changed_at": record.status_changed_at.isoformat(),
            "status_changed_by": record.status_changed_by, "code_revision": record.code_revision,
            "required_gates": list(record.required_gates),
            "gates": {g: latest[g].passed for g in sorted(latest)}}


def stage_outputs_router(ctx: ApiContext, router: Any = None) -> Any:
    router = APIRouter() if router is None else router

    async def call(fn, *args, **kwargs):
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("stage-output registry is unavailable")
            raise HTTPException(status_code=503, detail="stage-output registry is unavailable") from exc

    @router.get("/v1/operator/stage-outputs")
    async def list_stage_outputs(course_id: str | None = None, stage: str | None = None,
                                 status: str | None = None, limit: int = Query(100, ge=1, le=1000),
                                 offset: int = Query(0, ge=0)) -> dict[str, Any]:
        if course_id is not None:
            require_course_id(course_id)
        if stage is not None and not STAGE.fullmatch(stage):
            raise HTTPException(status_code=422, detail="invalid stage")
        if status is not None and status not in STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {list(STATUSES)}")
        records = await call(ctx.get_stage_registry().list, course_id=course_id, stage=stage, status=status,
                             limit=limit, offset=offset)
        return {"outputs": [_summary(r) for r in records], "limit": limit, "offset": offset}

    @router.get("/v1/operator/stage-outputs/counts")
    async def stage_output_counts(course_id: str | None = None) -> dict[str, Any]:
        if course_id is not None:
            require_course_id(course_id)
        return {"course_id": course_id, "counts": await call(ctx.get_stage_registry().counts, course_id=course_id)}

    @router.get("/v1/operator/stage-outputs/{course_id}/{stage}/{key}")
    async def get_stage_output(course_id: str, stage: str, key: str) -> dict[str, Any]:
        if not STAGE.fullmatch(stage) or not KEY.fullmatch(key):
            raise HTTPException(status_code=404, detail="stage output not found")
        require_course_id(course_id)
        registry = ctx.get_stage_registry()
        record = await call(registry.get, course_id, stage, key)
        if record is None:
            raise HTTPException(status_code=404, detail="stage output not found")
        history = await call(registry.history, course_id, stage, key)
        return {"record": record.model_dump(mode="json"), "summary": _summary(record),
                "history": [{"from_status": e.from_status, "to_status": e.to_status, "reason": e.reason,
                             "actor": e.actor, "code_revision": e.code_revision, "at": e.at.isoformat()}
                            for e in history]}

    return router
