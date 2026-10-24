"""Durable control records checked at committed stage boundaries."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any


def control_key(workflow_id: str) -> str:
    return f"_system/workflow-controls/{workflow_id}.json"


def pause_at_checkpoint(store: Any, workflow_id: str, checkpoint: str) -> bool:
    """Acknowledge a pause only after the caller has durably committed its unit."""
    s3 = getattr(store, "s3", None)
    if not s3 or not workflow_id:
        return False
    key = control_key(workflow_id)
    try:
        record = json.loads(s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())
    except Exception as exc:
        code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code == 404 or type(exc).__name__ in {"NoSuchKey", "KeyError"}:
            return False
        raise
    if record.get("workflow_id") != workflow_id or record.get("action") != "pause_after_checkpoint":
        return False
    record.update({"state": "PAUSED", "checkpoint": checkpoint,
                   "acknowledged_at": datetime.now(UTC).isoformat()})
    s3.put_object(Bucket=store.bucket, Key=key,
                  Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                  ContentType="application/json")
    return True


def wait_at_checkpoint(store: Any, workflow_id: str, checkpoint: str) -> None:
    """Leave the activity after a durable unit; the workflow waits for resume."""
    from temporalio.exceptions import ApplicationError
    if pause_at_checkpoint(store, workflow_id, checkpoint):
        raise ApplicationError("paused after durable checkpoint", type="StageCheckpointPaused", non_retryable=True)


async def execute_checkpointed(owner: Any, name: str, inp: Any, **options: Any) -> Any:
    """Wait in workflow history, releasing activity capacity and its deadlines."""
    from temporalio import workflow
    from temporalio.exceptions import ActivityError
    while True:
        owner._stage_resume_requested = False
        try:
            return await workflow.execute_activity(name, inp, **options)
        except ActivityError as exc:
            if getattr(exc.cause, "type", None) != "StageCheckpointPaused":
                raise
            await workflow.wait_condition(lambda: owner._stage_resume_requested)


def wait_for_activity_checkpoint(store: Any, checkpoint: str) -> None:
    from temporalio import activity
    try:
        workflow_id = activity.info().workflow_id
    except RuntimeError:
        return
    wait_at_checkpoint(store, workflow_id, checkpoint)
