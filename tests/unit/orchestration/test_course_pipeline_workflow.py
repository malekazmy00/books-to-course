"""CoursePipelineWorkflow: one book through the new line, each stage a child workflow (charter §4).

Fake child workflows stand in for the stages (same names), on Temporal's time-skipping test server.
The pipeline never asks a person: it stops only where a stage's own gate refuses its output, and
it says where and why.
"""
from __future__ import annotations

import uuid

import pytest
from temporalio import workflow
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from b2c_orchestration.course_pipeline import (CoursePipelineInput, CoursePipelineWorkflow, PIPELINE_QUERY,
                                               course_pipeline_workflow_id)

PARSED = {"stage": "ingest-parse-v2", "key": "p" * 64, "uri": "s3://artifacts/c1/ingest-parse-v2/x/",
          "sha256": "1" * 64, "created_at": "2026-10-31T00:00:00Z"}
OUTLINE = {"stage": "build-outline-v2", "key": "o" * 64, "uri": "s3://artifacts/c1/build-outline-v2/x/",
           "sha256": "2" * 64, "created_at": "2026-10-31T00:00:00Z"}
UNITS = {"stage": "translation-units", "sha256": "3" * 64}
APPROVED = {"stage": "book-termbase-approved", "sha256": "4" * 64}
CALLS: list[tuple[str, dict]] = []
SCRIPT: dict[str, dict] = {}


def _record(name, inp):
    data = inp if isinstance(inp, dict) else (inp.__dict__ if hasattr(inp, "__dict__") else {})
    CALLS.append((name, dict(data)))
    return SCRIPT.get(name, {})


@workflow.defn(name="IngestPDFV2Workflow", sandboxed=False)
class FakeIngest:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("ingest", inp) or {
            "status": "accepted", "parsed_book": {"ref": PARSED, "accepted": True},
            "outline": {"ref": OUTLINE, "stage_output_status": "accepted", "lesson_count": 3}}


@workflow.defn(name="TranslateBookWorkflow", sandboxed=False)
class FakeTranslate:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("translate", inp) or {
            "run_id": "a" * 64, "units_ref": UNITS, "assembly": {"artifact": {"stage": "translate-book"}},
            "termbase": {"approved_ref": APPROVED, "digest": "5" * 64},
            "translator_notes": {"artifact": {"stage": "translator-notes"}, "status": "accepted"}}


@workflow.defn(name="BookKnowledgeWorkflow", sandboxed=False)
class FakeKnowledge:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("knowledge", inp) or {
            "research": {"ref": {"stage": "knowledge-research"}, "stage_output_status": "accepted"},
            "concepts": {"ref": {"stage": "concept-layer"}, "stage_output_status": "accepted"}}


@workflow.defn(name="BuildFigureDescriptionsWorkflow", sandboxed=False)
class FakeFigures:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("figures", inp) or {
            "en": {"ref": {"stage": "figure-descriptions"}, "stage_output_status": "accepted"},
            "ar": {"ref": {"stage": "figure-descriptions-ar"}, "stage_output_status": "accepted"}}


@workflow.defn(name="BuildEpisodeScriptsWorkflow", sandboxed=False)
class FakeEpisodes:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("episodes", inp) or {"episodes": {f"{e:02d}": {"ar": {"stage_output_status": "accepted"}}
                                                         for e in inp["episodes"]}}


FAKES = [FakeIngest, FakeTranslate, FakeKnowledge, FakeFigures, FakeEpisodes]


def pipeline_input(**kw) -> CoursePipelineInput:
    data = dict(course_id="c1", upload={"book_id": "bk", "role": "spine", "title": "SAT"},
                source_ref={"stage": "source", "sha256": "9" * 64}, engine_profile="gemma4-26b",
                qe_profile="ladder-mqm")
    data.update(kw)
    return CoursePipelineInput(**data)


@pytest.fixture
async def env():
    from temporalio.testing import WorkflowEnvironment
    try:
        environment = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # pragma: no cover - depends on the test-server download
        pytest.skip(f"Temporal test server unavailable: {exc}")
    try:
        yield environment
    finally:
        await environment.shutdown()


async def run(env, inp):
    CALLS.clear()
    queue = f"cp-{uuid.uuid4().hex}"
    queues = dict(ingest_queue=queue, translate_queue=queue, companion_queue=queue)
    inp = inp.model_copy(update=queues)
    async with Worker(env.client, task_queue=queue, workflows=[CoursePipelineWorkflow, *FAKES]):
        handle = await env.client.start_workflow(CoursePipelineWorkflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)
        result = await handle.result()
        state = await handle.query(PIPELINE_QUERY)
    return result, state


async def test_stages_run_in_order_and_pass_their_outputs_on(env):
    SCRIPT.clear()
    result, state = await run(env, pipeline_input())
    assert [n for n, _ in CALLS] == ["ingest", "translate", "knowledge", "figures", "episodes"]
    calls = dict(CALLS)
    assert calls["translate"]["source_artifact_ref"] == PARSED
    assert calls["translate"]["qe_profile"] == "ladder-mqm" and calls["translate"]["termbase_review"] == "models"
    assert calls["knowledge"]["outline_ref"] == OUTLINE and calls["knowledge"]["termbase_ref"] == APPROVED
    assert calls["figures"]["units_ref"] == UNITS and calls["figures"]["termbase_ref"] == APPROVED
    assert calls["episodes"]["episodes"] == [0, 1, 2, 3] and calls["episodes"]["translation_run_id"] == "a" * 64
    assert calls["episodes"]["figures_ref"] == {"stage": "figure-descriptions"}
    assert result["status"] == "scripts_ready" and result["stopped_at"] is None
    assert state["phase"] == "done" and state["stages"]["episodes"]["accepted"] == 4


async def test_a_refused_ingest_stops_the_line_and_says_why(env):
    SCRIPT.clear()
    SCRIPT["ingest"] = {"status": "needs_review", "parsed_book": {"ref": PARSED, "accepted": False}, "outline": None}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS] == ["ingest"]
    assert result["status"] == "stopped" and result["stopped_at"] == "ingest"


async def test_an_outline_its_gates_did_not_accept_stops_before_translation(env):
    SCRIPT.clear()
    SCRIPT["ingest"] = {"status": "accepted", "parsed_book": {"ref": PARSED, "accepted": True},
                        "outline": {"ref": OUTLINE, "stage_output_status": "rejected", "lesson_count": 3}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS] == ["ingest"] and result["stopped_at"] == "outline"


async def test_figures_can_be_skipped_and_episodes_limited(env):
    SCRIPT.clear()
    result, _ = await run(env, pipeline_input(figures=False, episodes=[0, 1]))
    assert [n for n, _ in CALLS] == ["ingest", "translate", "knowledge", "episodes"]
    episodes = dict(CALLS)["episodes"]
    assert episodes["episodes"] == [0, 1] and episodes.get("figures_ref") is None


async def test_concepts_not_accepted_stop_before_episodes(env):
    SCRIPT.clear()
    SCRIPT["knowledge"] = {"research": {"ref": {"stage": "knowledge-research"}, "stage_output_status": "accepted"},
                           "concepts": {"ref": {"stage": "concept-layer"}, "stage_output_status": "rejected"}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS] == ["ingest", "translate", "knowledge"] and result["stopped_at"] == "knowledge"


def test_workflow_id_is_stable_per_course_and_source():
    a = course_pipeline_workflow_id(pipeline_input())
    assert a == course_pipeline_workflow_id(pipeline_input()) and a.startswith("b2c-course-pipeline-")
    assert a != course_pipeline_workflow_id(pipeline_input(course_id="c2"))
