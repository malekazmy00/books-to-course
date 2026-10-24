"""Recover B2C activities proven to belong to worker processes lost on reboot.

Executed inside the existing API container; no inference or artifact mutation.
Live B2C container identities/start times are supplied by the startup helper.
"""
import asyncio
import json
import os
import re
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.workflowservice.v1 import ResetActivityRequest


def worker_was_lost(identity, activity_started, heartbeat, live):
    match=re.fullmatch(r"1@([a-f0-9]{12})", identity)
    if not match:
        return False
    hostname=match.group(1)
    if hostname not in live:
        return True
    boot=live[hostname]
    return activity_started < boot and heartbeat < boot


RECOVERABLE_ACTIVITIES = {
    "b2c-translate-": {"translate_content"},
    "b2c-qa-": {"generate_course_qa"},
    "b2c-ingest-": {"ingest_parse_pdf", "outline_from_parsed_book"},
    "b2c-index-": {"index_approved_book"},
}


def recoverable_activity(workflow_id, activity_name):
    return any(workflow_id.startswith(prefix) and activity_name in names
               for prefix, names in RECOVERABLE_ACTIVITIES.items())


async def main():
    live=json.loads(os.environ["B2C_LIVE_CONTAINER_STARTS"])
    if not live:
        raise RuntimeError("No live B2C container evidence; refusing recovery")
    client=await Client.connect("temporal:7233")
    exact_workflow_id = os.environ.get("B2C_RECOVERY_WORKFLOW_ID", "")
    if exact_workflow_id and not exact_workflow_id.startswith(tuple(RECOVERABLE_ACTIVITIES)):
        raise RuntimeError("Recovery target must be a B2C workflow")
    recovered=0
    async for workflow in client.list_workflows("ExecutionStatus = 'Running'"):
        if exact_workflow_id and workflow.id != exact_workflow_id:
            continue
        if not workflow.id.startswith(tuple(RECOVERABLE_ACTIVITIES)):
            continue
        handle=client.get_workflow_handle(workflow.id,run_id=workflow.run_id)
        description=await handle.describe()
        if description.status.name != "RUNNING":
            continue
        for pending in description.raw_description.pending_activities:
            if pending.state != 2 or not recoverable_activity(workflow.id, pending.activity_type.name):
                continue
            if not worker_was_lost(pending.last_worker_identity,pending.last_started_time.seconds,
                                   pending.last_heartbeat_time.seconds,live):
                continue
            # Reset only this lost attempt's retry accounting, retaining heartbeat
            # and source/checkpoint identities. A failure then dispatches its retry.
            await client.workflow_service.reset_activity(ResetActivityRequest(
                namespace=client.namespace,execution=WorkflowExecution(workflow_id=workflow.id,run_id=workflow.run_id),
                identity="b2c-post-mount-recovery",id=pending.activity_id,reset_heartbeat=False))
            activity=client.get_async_activity_handle(workflow_id=workflow.id,run_id=workflow.run_id,activity_id=pending.activity_id)
            await activity.fail(ApplicationError("Confirmed worker process lost during restart; recover saved checkpoints",
                type="B2CWorkerLostAfterRestart",non_retryable=False))
            recovered+=1
            print("Recovered lost B2C activity",workflow.id,pending.activity_id)
    print("B2C lost-worker activities recovered:",recovered)


if __name__=="__main__":
    asyncio.run(asyncio.wait_for(main(),timeout=90))
