"""Histories recorded by pre-hardening workflow code must replay on current code.

Runs the original (unpatched) IngestPDFWorkflow and IndexApprovedBookWorkflow
definitions in Temporal's time-skipping test server, then replays their
histories with the current definitions. Skipped when the test server binary
is unavailable (offline CI).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from b2c_orchestration.controls import execute_checkpointed
from b2c_orchestration.indexing import IndexApprovedBookInput, IndexApprovedBookWorkflow
from b2c_orchestration.ingest import BuildOutlineInput, IngestPDFInput, IngestPDFWorkflow
from b2c_orchestration.retry import DEFAULT_RETRY


@workflow.defn(name="IngestPDFWorkflow")
class LegacyIngestPDFWorkflow:
    """Verbatim command sequence of the pre-hardening ingest workflow."""

    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: IngestPDFInput) -> dict[str, Any]:
        parsed = await execute_checkpointed(
            self, "ingest_parse_pdf", inp, task_queue="heavy", retry_policy=DEFAULT_RETRY,
            start_to_close_timeout=timedelta(hours=24))
        if inp.upload.get("role") != "spine":
            return {"parsed_book": parsed, "outline": None}
        outline = await execute_checkpointed(
            self, "outline_from_parsed_book",
            BuildOutlineInput(inp.course_id, parsed["ref"], inp.supplementary_book_ids, inp.cache_bust),
            task_queue="light", retry_policy=DEFAULT_RETRY, start_to_close_timeout=timedelta(minutes=5))
        return {"parsed_book": parsed, "outline": outline}


@workflow.defn(name="IndexApprovedBookWorkflow")
class LegacyIndexApprovedBookWorkflow:
    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self, inp: IndexApprovedBookInput) -> dict[str, Any]:
        return await execute_checkpointed(
            self, "index_approved_book", inp, task_queue=inp.activity_task_queue,
            start_to_close_timeout=timedelta(minutes=30))


@activity.defn(name="ingest_parse_pdf")
def fake_parse(inp: IngestPDFInput) -> dict[str, Any]:
    return {"ref": {"stage": "ingest-parse-pdf"}, "cache_hit": True}


@activity.defn(name="outline_from_parsed_book")
def fake_outline(inp: BuildOutlineInput) -> dict[str, Any]:
    return {"ref": {"stage": "build-outline"}, "cache_hit": True}


@activity.defn(name="index_approved_book")
def fake_index(inp: IndexApprovedBookInput) -> dict[str, Any]:
    return {"course_id": inp.course_id, "chunk_count": 0}


async def _record(env, workflow_cls, run_arg) -> WorkflowHistory:
    from concurrent.futures import ThreadPoolExecutor
    acts = [fake_parse, fake_outline, fake_index]
    executor = ThreadPoolExecutor(max_workers=4)
    workers = [Worker(env.client, task_queue=queue, workflows=[workflow_cls] if queue == "wf" else [],
                      activities=acts, activity_executor=executor,
                      workflow_runner=UnsandboxedWorkflowRunner())
               for queue in ("wf", "heavy", "light")]
    try:
        async with workers[0], workers[1], workers[2]:
            handle = await env.client.start_workflow(
                workflow_cls.run, run_arg, id=f"replay-{uuid.uuid4()}", task_queue="wf")
            await handle.result()
            return await handle.fetch_history()
    finally:
        executor.shutdown(wait=False)


@pytest.fixture
async def env():
    from temporalio.testing import WorkflowEnvironment
    try:
        environment = await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter)
    except Exception as exc:  # pragma: no cover - depends on test-server download
        pytest.skip(f"Temporal test server unavailable: {exc}")
    try:
        yield environment
    finally:
        await environment.shutdown()


def _ingest_input() -> IngestPDFInput:
    return IngestPDFInput(course_id="c", upload={"role": "spine"}, source_ref={},
                          supplementary_book_ids=[])


def _index_input() -> IndexApprovedBookInput:
    return IndexApprovedBookInput(course_id="c", parsed_ref={}, approved_outline_ref={})


@pytest.mark.parametrize("legacy,current,make_input", [
    (LegacyIngestPDFWorkflow, IngestPDFWorkflow, _ingest_input),
    (LegacyIndexApprovedBookWorkflow, IndexApprovedBookWorkflow, _index_input),
])
async def test_pre_hardening_history_replays_on_current_workflow(env, legacy, current, make_input):
    history = await _record(env, legacy, make_input())
    replayer = Replayer(workflows=[current], data_converter=pydantic_data_converter,
                        workflow_runner=UnsandboxedWorkflowRunner())
    await replayer.replay_workflow(history)


@pytest.mark.parametrize("current,make_input", [
    (IngestPDFWorkflow, _ingest_input),
    (IndexApprovedBookWorkflow, _index_input),
])
async def test_patched_history_replays_and_records_markers(env, current, make_input):
    history = await _record(env, current, make_input())
    markers = [event for event in history.events if event.HasField("marker_recorded_event_attributes")]
    assert markers, "patched workflows must record version markers on new runs"
    replayer = Replayer(workflows=[current], data_converter=pydantic_data_converter,
                        workflow_runner=UnsandboxedWorkflowRunner())
    await replayer.replay_workflow(history)
