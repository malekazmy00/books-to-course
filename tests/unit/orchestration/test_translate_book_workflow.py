"""TranslateBookWorkflow (D-028 part D) on Temporal's time-skipping test server with fake activities.

Skips cleanly when the test server binary is unavailable offline.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from b2c_orchestration import translate_book as tb
from b2c_orchestration.translate_book import (AssembleBookTranslationWorkflow, AssembleTranslationInput, AssemblyResult,
                                              BatchOutcome, BuildTermbaseInput, CheckTermbaseInput, PreparedUnits,
                                              PrepareUnitsInput, ReviewTermbaseInput, TermbaseApproval, TermbaseBuild,
                                              TermbaseGate, TermbaseReview, TranslateBookInput, TranslateBookWorkflow,
                                              TranslateUnitBatchInput, TranslatorNotesResult, translation_run_id)
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


APPROVED_REF = {"stage": "book-termbase-approved", "key": "9" * 64,
                "uri": f"s3://artifacts/c1/book-termbase-approved/{'9' * 64}/", "sha256": "8" * 64,
                "created_at": "2026-10-24T12:00:00Z"}
DIGEST = "7" * 64


class Recorder:
    """Fake activities. ``gates`` scripts the pending count each check returns (the last repeats);
    without it the termbase gate is open at once."""

    def __init__(self, fail_batch_attempts: int = 0, fatal: bool = False, gates: list[int] | None = None):
        self.calls: list[tuple[str, object]] = []
        self.fail_batch_attempts = fail_batch_attempts
        self.fatal = fatal
        self.gates = list(gates or [0])

    def activities(self):
        @activity.defn(name="prepare_translation_units")
        async def prepare(inp: PrepareUnitsInput) -> PreparedUnits:
            self.calls.append(("prepare", inp))
            return PreparedUnits(units_ref=UNITS_REF, unit_ids=IDS, book_id="bk", cache_hit=False)

        @activity.defn(name="build_book_termbase")
        async def build(inp: BuildTermbaseInput) -> TermbaseBuild:
            self.calls.append(("build_termbase", inp))
            return TermbaseBuild(book_id="bk", artifact={"stage": "book-termbase"}, termbase_sha256="6" * 64,
                                 entry_count=3, extracted=3, absent=0, proposer=False)

        @activity.defn(name="check_book_termbase")
        async def check(inp: CheckTermbaseInput) -> TermbaseGate:
            self.calls.append(("check_termbase", inp))
            pending = self.gates.pop(0) if len(self.gates) > 1 else self.gates[0]
            counts = {"pending_blocking": pending, "approved": 2}
            if pending and inp.require_approved:
                return TermbaseGate(open=False, counts=counts)
            return TermbaseGate(open=True, counts=counts, snapshot=APPROVED_REF, digest=DIGEST, locked_terms=2)

        @activity.defn(name="review_book_termbase")
        async def review(inp: ReviewTermbaseInput) -> TermbaseReview:
            self.calls.append(("review_termbase", inp))
            return TermbaseReview(counts={"locked": 2, "hint": 1, "rejected": 0, "judge_unavailable": 0},
                                  artifact={"stage": "book-termbase-votes"}, judge_profile=inp.judge_profile)

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

        @activity.defn(name="build_translator_notes")
        async def notes(inp: AssembleTranslationInput) -> TranslatorNotesResult:
            self.calls.append(("notes", inp))
            return TranslatorNotesResult(artifact={"stage": "translator-notes"}, counts={"annotated": 7},
                                         results_digest="e" * 64, cache_hit=False, status="accepted")
        return [prepare, build, check, review, batch, assemble, notes]


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
    assert kinds == ["prepare", "build_termbase", "check_termbase", "batch", "batch", "batch", "assemble", "notes"]
    batches = [c for k, c in recorder.calls if k == "batch"]
    assert [b.unit_ids for b in batches] == [IDS[0:3], IDS[3:6], IDS[6:7]]
    run_id = translation_run_id(inp)
    assert all(b.run_id == run_id and b.units_ref == UNITS_REF and b.engine_profile == "hy-mt2"
               and b.qe_profile is None and b.termbase_ref == APPROVED_REF and b.termbase_digest == DIGEST
               for b in batches)
    assert recorder.calls[0][1].run_id == run_id and recorder.calls[0][1].inp == inp
    assert result["run_id"] == run_id and result["translated"] == 7 and result["unit_count"] == 7
    assert result["assembly"]["counts"] == {"needs_review": 7}
    # D-040: the notes step follows the assembly with the same run and units
    notes = recorder.calls[-1][1]
    assert notes.run_id == run_id and notes.units_ref == UNITS_REF and notes.course_id == "c1"
    assert result["translator_notes"]["counts"] == {"annotated": 7}
    assert result["translator_notes"]["status"] == "accepted"


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
    assert [k for k, _ in recorder.calls] == ["prepare", "build_termbase", "check_termbase", "batch"]


async def test_reassembly_workflow_runs_only_the_assembly_activity(env):
    recorder = Recorder()
    queue = f"tb-{uuid.uuid4().hex}"
    inp = AssembleTranslationInput(run_id="f" * 64, course_id="c1", units_ref=UNITS_REF)
    async with Worker(env.client, task_queue=queue, workflows=[AssembleBookTranslationWorkflow],
                      activities=recorder.activities()):
        result = await env.client.execute_workflow(AssembleBookTranslationWorkflow.run, inp,
                                                   id=f"wf-{uuid.uuid4().hex}", task_queue=queue)
    assert [k for k, _ in recorder.calls] == ["assemble", "notes"] and result["run_id"] == "f" * 64
    assert result["translator_notes"]["status"] == "accepted"


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


# ------------------------------------------------------------------ termbase gate (D-028 principle 5)
async def wait_for_phase(handle, phase, *, checks, recorder):
    import asyncio
    state = None
    for _ in range(400):
        state = await handle.query(tb.TERMBASE_QUERY)
        if state["phase"] == phase and state.get("checks", 0) >= checks:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {phase}: {state}")


async def test_translation_waits_for_the_signal_until_nothing_is_pending(env):
    recorder = Recorder(gates=[4, 1, 0])
    queue = f"tb-{uuid.uuid4().hex}"
    inp = book_input(termbase_min_frequency=2)
    async with Worker(env.client, task_queue=queue, workflows=[TranslateBookWorkflow],
                      activities=recorder.activities()):
        handle = await env.client.start_workflow(TranslateBookWorkflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)
        state = await wait_for_phase(handle, "awaiting_approval", checks=1, recorder=recorder)
        assert state["counts"]["pending_blocking"] == 4 and state["min_frequency"] == 2
        assert state["require_approved"] is True and state["signals"] == 0
        assert not any(k == "batch" for k, _ in recorder.calls)  # nothing translated before approval
        # "done" while something is still pending: checked again, still waiting
        await handle.signal(tb.TERMBASE_SIGNAL, TermbaseApproval(reviewer_id="aly"))
        state = await wait_for_phase(handle, "awaiting_approval", checks=2, recorder=recorder)
        assert state["counts"]["pending_blocking"] == 1 and state["last_reviewer"] == "aly"
        assert not any(k == "batch" for k, _ in recorder.calls)
        await handle.signal(tb.TERMBASE_SIGNAL, TermbaseApproval(reviewer_id="aly"))
        result = await handle.result()
        state = await handle.query(tb.TERMBASE_QUERY)
    kinds = [k for k, _ in recorder.calls]
    assert kinds == ["prepare", "build_termbase", "check_termbase", "check_termbase", "check_termbase",
                     "batch", "batch", "batch", "assemble", "notes"]
    check = recorder.calls[2][1]
    assert check.min_frequency == 2 and check.require_approved and check.book_id == "bk"
    assert state["phase"] == "approved" and state["digest"] == DIGEST and state["approved_ref"] == APPROVED_REF
    assert result["termbase"] == {"digest": DIGEST, "approved_ref": APPROVED_REF, "locked_terms": 2, "entities": 0}


async def test_without_the_requirement_translation_starts_with_approved_entries_only(env):
    recorder = Recorder(gates=[5])
    result = await run_workflow(env, recorder, book_input(require_approved_termbase=False))
    kinds = [k for k, _ in recorder.calls]
    assert kinds[:3] == ["prepare", "build_termbase", "check_termbase"] and kinds[-2:] == ["assemble", "notes"]
    assert recorder.calls[2][1].require_approved is False
    batches = [c for k, c in recorder.calls if k == "batch"]
    assert batches and all(b.termbase_digest == DIGEST for b in batches)
    assert result["termbase"]["digest"] == DIGEST


async def test_build_receives_the_prepared_units_and_profile(env):
    recorder = Recorder()
    inp = book_input()
    await run_workflow(env, recorder, inp)
    build = recorder.calls[1][1]
    assert build.units_ref == UNITS_REF and build.engine_profile == "hy-mt2" and build.course_id == "c1"
    assert build.run_id == translation_run_id(inp)


def test_termbase_options_are_not_part_of_the_run_id():
    base = translation_run_id(book_input())
    assert translation_run_id(book_input(require_approved_termbase=False, termbase_min_frequency=3)) == base
    assert book_input().require_approved_termbase is True and book_input().termbase_min_frequency == 1


# The TranslateBookWorkflow command sequence before the termbase step (no patch marker).
@workflow.defn(name="TranslateBookWorkflow", sandboxed=False)
class PrePatchWorkflow:
    @workflow.run
    async def run(self, inp: TranslateBookInput) -> dict:
        run_id = translation_run_id(inp)
        prepared = await workflow.execute_activity(
            "prepare_translation_units", PrepareUnitsInput(run_id=run_id, inp=inp), result_type=PreparedUnits,
            **tb.PREPARE_OPTIONS)
        ids = prepared.unit_ids
        for start in range(0, len(ids), inp.batch_size):
            await workflow.execute_activity(
                "translate_unit_batch",
                TranslateUnitBatchInput(run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref,
                                        unit_ids=ids[start:start + inp.batch_size],
                                        engine_profile=inp.engine_profile, qe_profile=inp.qe_profile,
                                        profile_fingerprint=inp.profile_fingerprint,
                                        source_language=inp.source_language,
                                        target_language=inp.target_language),
                result_type=BatchOutcome, **tb.BATCH_OPTIONS)
        await workflow.execute_activity("assemble_translation", AssembleTranslationInput(
            run_id=run_id, course_id=inp.course_id, units_ref=prepared.units_ref),
            result_type=AssemblyResult, **tb.ASSEMBLE_OPTIONS)
        return {"run_id": run_id}


async def test_histories_from_before_the_termbase_step_still_replay(env):
    """A history recorded without the termbase step (pre-patch) must replay on the new code."""
    from temporalio.worker import Replayer

    recorder = Recorder()
    queue = f"tb-{uuid.uuid4().hex}"
    async with Worker(env.client, task_queue=queue, workflows=[PrePatchWorkflow], activities=recorder.activities()):
        handle = await env.client.start_workflow(PrePatchWorkflow.run, book_input(), id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)
        await handle.result()
    assert not any(k == "build_termbase" for k, _ in recorder.calls)
    history = await handle.fetch_history()
    await Replayer(workflows=[TranslateBookWorkflow],
                   data_converter=pydantic_data_converter).replay_workflow(history)


# ------------------------------------------------------------------ termbase review by model vote (D-033)
async def test_with_a_judge_the_termbase_is_reviewed_by_models_and_translation_starts(env):
    recorder = Recorder(gates=[3, 0])
    result = await run_workflow(env, recorder, book_input(qe_profile="deepseek-mqm", termbase_min_frequency=2))
    kinds = [k for k, _ in recorder.calls]
    assert kinds[:5] == ["prepare", "build_termbase", "check_termbase", "review_termbase", "check_termbase"]
    assert kinds[-2:] == ["assemble", "notes"] and "batch" in kinds
    review = recorder.calls[3][1]
    assert review.judge_profile == "deepseek-mqm" and review.min_frequency == 2 and review.book_id == "bk"
    assert result["termbase"]["digest"] == DIGEST


async def test_model_review_runs_once_then_falls_back_to_the_signal(env):
    recorder = Recorder(gates=[3, 1, 0])
    queue = f"tb-{uuid.uuid4().hex}"
    async with Worker(env.client, task_queue=queue, workflows=[TranslateBookWorkflow],
                      activities=recorder.activities()):
        handle = await env.client.start_workflow(TranslateBookWorkflow.run, book_input(qe_profile="deepseek-mqm"),
                                                 id=f"wf-{uuid.uuid4().hex}", task_queue=queue)
        state = await wait_for_phase(handle, "awaiting_approval", checks=2, recorder=recorder)
        assert state["model_review"]["locked"] == 2
        await handle.signal(tb.TERMBASE_SIGNAL, TermbaseApproval(reviewer_id="aly"))
        await handle.result()
    assert [k for k, _ in recorder.calls].count("review_termbase") == 1


async def test_signal_mode_or_no_judge_keeps_waiting_for_the_signal(env):
    for inp in (book_input(qe_profile="deepseek-mqm", termbase_review="signal"), book_input(qe_profile=None)):
        recorder = Recorder(gates=[2, 0])
        queue = f"tb-{uuid.uuid4().hex}"
        async with Worker(env.client, task_queue=queue, workflows=[TranslateBookWorkflow],
                          activities=recorder.activities()):
            handle = await env.client.start_workflow(TranslateBookWorkflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                     task_queue=queue)
            await wait_for_phase(handle, "awaiting_approval", checks=1, recorder=recorder)
            await handle.signal(tb.TERMBASE_SIGNAL, TermbaseApproval(reviewer_id="aly"))
            await handle.result()
        assert "review_termbase" not in [k for k, _ in recorder.calls]


def test_model_review_is_the_default_and_not_part_of_the_run_id():
    assert book_input().termbase_review == "models"
    assert translation_run_id(book_input(termbase_review="signal")) == translation_run_id(book_input())
