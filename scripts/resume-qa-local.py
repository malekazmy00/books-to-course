"""Preview or apply a QA model switch after a durable paused/failed boundary.
Run inside a B2C container with the project's existing secret environment.
The default is read-only and never submits inference.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
from b2c_core.config import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_orchestration.qa import GenerateCourseQAInput, GenerateCourseQAWorkflow, _load_qa_checkpoints
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import WorkflowAlreadyStartedError


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--model", default="qwen3-coder-30b")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--queue", default="heavy")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace,
                                  data_converter=pydantic_data_converter)
    handle = client.get_workflow_handle(args.workflow_id)
    status = (await handle.describe()).status.name
    history = await handle.fetch_history()
    start = next(event.workflow_execution_started_event_attributes for event in history.events
                 if event.HasField("workflow_execution_started_event_attributes"))
    prior = (await client.data_converter.decode(start.input.payloads, [GenerateCourseQAInput]))[0]
    store = ArtifactStore.from_settings(settings)
    checkpoint_sources = list(dict.fromkeys([*prior.checkpoint_source_workflow_ids, args.workflow_id]))
    # Derive the expected immutable source identity from a persisted compatible checkpoint.
    identities = []
    paginator = store.s3.get_paginator("list_objects_v2")
    for source in checkpoint_sources:
        for page in paginator.paginate(Bucket=store.bucket, Prefix=f"_system/qa-checkpoints/{source}/"):
            for entry in page.get("Contents", []):
                payload = json.loads(store.s3.get_object(Bucket=store.bucket, Key=entry["Key"])["Body"].read())
                if "scope_result" in payload:
                    identities.append(payload["identity"])
    if not identities:
        raise RuntimeError("No saved scope checkpoint; model switch cannot proceed.")
    identity = dict(identities[-1])
    if identity["approved_outline_sha256"] != prior.approved_outline_ref["sha256"]:
        raise RuntimeError("Saved source outline differs from immutable workflow input.")
    expected_index_sha = hashlib.sha256(json.dumps(prior.index_result, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    if identity["index_result_sha256"] != expected_index_sha:
        raise RuntimeError("Saved index identity differs from immutable workflow input.")
    identity["model_id"] = args.model
    identity["config"] = {**identity["config"], "model_id": args.model}
    identity["config"].pop("reasoning_effort", None)
    scopes = _load_qa_checkpoints(store, checkpoint_sources, identity, allow_model_switch=True)
    if not scopes:
        raise RuntimeError("No compatible checkpoints were restored.")
    inp = prior.model_copy(update={
        "selected_model_id": args.model, "selected_provider_id": args.provider,
        "activity_task_queue": args.queue, "allow_checkpoint_model_switch": True,
        "checkpoint_source_workflow_ids": checkpoint_sources,
    })
    new_id = "b2c-qa-" + hashlib.sha256(
        (args.workflow_id + "\0explicit-model-switch-v1\0" + args.model + "\0" + args.queue).encode()).hexdigest()
    summary = {"source_workflow_id": args.workflow_id, "source_status": status,
        "target_workflow_id": new_id, "target_model": args.model, "target_queue": args.queue,
        "restored_scopes": len(scopes), "final_scopes": sum(s.status == "final" for s in scopes),
        "accepted_questions": sum(len(s.items) for s in scopes),
        "scope_snapshot_sha256": hashlib.sha256(json.dumps(
            [s.model_dump(mode="json") for s in sorted(scopes, key=lambda s: s.scope_id)],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "applied": False}
    if args.apply:
        if status not in {"COMPLETED", "FAILED", "CANCELED", "TERMINATED"}:
            raise RuntimeError("The source workflow must be terminal before applying.")
        if status == "COMPLETED" and (await handle.result()).get("status") not in {"paused", "failed"}:
            raise RuntimeError("A successful final workflow must not be repeated.")
        try:
            await client.start_workflow(GenerateCourseQAWorkflow.run, inp, id=new_id, task_queue=args.queue)
        except WorkflowAlreadyStartedError:
            pass
        summary["applied"] = True
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

