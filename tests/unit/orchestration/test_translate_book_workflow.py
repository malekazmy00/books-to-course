"""TranslateBookWorkflow (D-028 part D) on Temporal's time-skipping test server with fake activities.

Skips cleanly when the test server binary is unavailable offline.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from b2c_orchestration import translate_book as tb
from b2c_orchestration.translate_book import (AssembleBookTranslationWorkflow, AssembleTranslationInput, AssemblyResult,
                                              BatchOutcome, PreparedUnits, PrepareUnitsInput, TranslateBookInput,
                                              TranslateBookWorkflow, TranslateUnitBatchInput, translation_run_id)
from b2c_orchestration.translation import TranslateContentWorkflow

REF = {"stage": "ingest-parse-pdf", "key": "b" * 64, "uri": f"s3://artifacts/c1/ingest-parse-pdf/{'b' * 64}/",
       "sha256": "a" * 64, "created_at": "2026-10-24T12:00:00Z"}
UNITS_REF = {"stage": "translation-units", "key": "c" * 64, "uri": f"s3://artifacts/c1/translation-units/{'c' * 64}/",
             "sha256": "d" * 64, "created_at": "2026-10-24T12:00:00Z"}
IDS = [f"bk:u{i:05d}" for i in range(7)]


def book_input(**kw) -> TranslateBookInput:
    data = dict(course_id="c1", source_artifact_ref=REF, source_language="en", target_language="ar",
                engine_profile="hy-mt2", qe_profile=None, batch_size=3)
    data.update(kw)
    return TranslateBookInput(**data)


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


class Recorder:
    def __init__(self, fail_batch_attempts: int = 0, fatal: bool = False):
        self.calls: list[tuple[str, object]] = []
        self.fail_batch_attempts = fail_batch_attempts
        self.fatal = fatal

    def activities(self):
        @activity.defn(name="prepare_translation_units")
        async def prepare(inp: PrepareUnitsInput) -> PreparedUnits:
            self.calls.append(("prepare", inp))
            return PreparedUnits(units_ref=UNITS_REF, unit_ids=IDS, book_id="bk", cache_hit=False)

        @activity.defn(name="translate_unit_batch")
        async def batch(inp: TranslateUnitBatchInput) -> BatchOutcome:
            self.calls.append(("batch", inp))
            if self.fatal:
                raise ApplicationError("profile changed", type="TranslationProfileChanged", non_retryable=True)
            if self.fail_batch_attempts and activity.info().attempt <= self.fail_batch_attempts:
                raise RuntimeError("transient model server failure")
            return BatchOutcome(translated=len(inp.unit_ids), skipped=0, decisions={"needs_review": len(inp.unit_ids)})

        @activity.defn(name="assemble_translation")
        async def assemble(inp: AssembleTranslationInput) -> AssemblyResult:
            self.calls.append(("assemble", inp))
            return AssemblyResult(artifact={"stage": "translate-book"}, counts={"needs_review": 7}, total_blocks=3,
                                  complete_blocks=0, results_digest="e" * 64, cache_hit=False)
        return [prepare, batch, assemble]


async def run_workflow(env, recorder, inp):
    queue = f"tb-{uuid.uuid4().hex}"
    async with Worker(env.client, task_queue=queue, workflows=[TranslateBookWorkflow, AssembleBookTranslationWorkflow],
                      activities=recorder.activities()):
        return await env.client.execute_workflow(TranslateBookWorkflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)


async def test_workflow_prepares_translates_bounded_batches_then_assembles(env):
    recorder = Recorder()
    inp = book_input()
    result = await run_workflow(env, recorder, inp)
    kinds = [k for k, _ in recorder.calls]
    assert kinds == ["prepare", "batch", "batch", "batch", "assemble"]
    batches = [c for k, c in recorder.calls if k == "batch"]
    assert [b.unit_ids for b in batches] == [IDS[0:3], IDS[3:6], IDS[6:7]]
    run_id = translation_run_id(inp)
    assert all(b.run_id == run_id and b.units_ref == UNITS_REF and b.engine_profile == "hy-mt2"
               and b.qe_profile is None for b in batches)
    assert recorder.calls[0][1].run_id == run_id and recorder.calls[0][1].inp == inp
    assert result["run_id"] == run_id and result["translated"] == 7 and result["unit_count"] == 7
    assert result["assembly"]["counts"] == {"needs_review": 7}


async def test_transient_batch_failure_is_retried_with_the_same_batch(env):
    recorder = Recorder(fail_batch_attempts=1)
    result = await run_workflow(env, recorder, book_input(batch_size=10))
    batches = [c for k, c in recorder.calls if k == "batch"]
    assert len(batches) == 2 and batches[0] == batches[1]
    assert result["translated"] == 7


async def test_non_retryable_batch_failure_fails_the_run_without_assembly(env):
    recorder = Recorder(fatal=True)
    with pytest.raises(WorkflowFailureError):
        await run_workflow(env, recorder, book_input())
    assert [k for k, _ in recorder.calls] == ["prepare", "batch"]


async def test_reassembly_workflow_runs_only_the_assembly_activity(env):
    recorder = Recorder()
    queue = f"tb-{uuid.uuid4().hex}"
    inp = AssembleTranslationInput(run_id="f" * 64, course_id="c1", units_ref=UNITS_REF)
    async with Worker(env.client, task_queue=queue, workflows=[AssembleBookTranslationWorkflow],
                      activities=recorder.activities()):
        result = await env.client.execute_workflow(AssembleBookTranslationWorkflow.run, inp,
                                                   id=f"wf-{uuid.uuid4().hex}", task_queue=queue)
    assert [k for k, _ in recorder.calls] == ["assemble"] and result["run_id"] == "f" * 64


def test_activity_options_are_bounded_and_heartbeat():
    for options in (tb.PREPARE_OPTIONS, tb.BATCH_OPTIONS, tb.ASSEMBLE_OPTIONS):
        assert options["heartbeat_timeout"] <= timedelta(minutes=5)
        assert options["start_to_close_timeout"] <= timedelta(hours=3)
        assert options["schedule_to_close_timeout"] <= timedelta(hours=12)
        policy = options["retry_policy"]
        assert 1 < policy.maximum_attempts <= 5
        for deterministic in ("TranslationProfileChanged", "UnknownTranslationProfile", "UnknownTranslationUnit",
                              "InvalidTranslationSource", "LLMOutputTruncated", "CloudProviderNotAllowed"):
            assert deterministic in policy.non_retryable_error_types


def test_new_names_do_not_touch_the_paused_old_workflow():
    from temporalio import workflow as wf
    assert wf._Definition.from_class(TranslateBookWorkflow).name == "TranslateBookWorkflow"
    assert wf._Definition.from_class(TranslateContentWorkflow).name == "TranslateContentWorkflow"
    names = {fn.__temporal_activity_definition.name for fn in (
        tb.TranslateBookActivities.prepare, tb.TranslateBookActivities.translate_batch,
        tb.TranslateBookActivities.assemble)}
    assert names == {"prepare_translation_units", "translate_unit_batch", "assemble_translation"}
    assert not names & {"translate_content", "retranslate_segment"}
