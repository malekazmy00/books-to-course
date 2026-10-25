"""GenerateSpeechV2Workflow (R7) on Temporal's time-skipping test server with fake activities.

Skips cleanly when the test server binary is unavailable offline.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from b2c_speech.plan import PlanLimits, SpeechPlan, TrackPlan
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from b2c_orchestration import speech_v2 as sv
from b2c_orchestration.audio import GenerateMP3Workflow
from b2c_orchestration.speech_v2 import (AssembleSpeechTrackInput, GenerateSpeechV2Input, GenerateSpeechV2Workflow,
                                         SegmentBatchResult, SegmentStatus, SpeechPrepared, SpeechTrackResult,
                                         SynthesizeSegmentsInput)

ASSEMBLY = {"stage": "translate-book", "key": "b" * 64, "uri": f"s3://artifacts/c1/translate-book/{'b' * 64}/",
            "sha256": "a" * 64, "created_at": "2026-10-25T12:00:00Z"}
TEXT = {"stage": "speech-text", "key": "c" * 64, "uri": f"s3://artifacts/c1/speech-text/{'c' * 64}/",
        "sha256": "d" * 64, "created_at": "2026-10-25T12:00:00Z"}
AUDIO = {"stage": "speech-segment-audio", "key": "e" * 64, "uri": "s3://artifacts/c1/speech-segment-audio/e/",
         "sha256": "f" * 64, "created_at": "2026-10-25T12:00:00Z"}


def plan():
    tracks = [TrackPlan(track_id="bk:t000", segment_ids=["s0", "s1", "s2"], batches=[["s0", "s1"], ["s2"]],
                        spoken_chars=30, estimated_seconds=3.0),
              TrackPlan(track_id="bk:t001", segment_ids=["s3"], batches=[["s3"]], spoken_chars=10,
                        estimated_seconds=1.0)]
    return SpeechPlan(book_id="bk", limits=PlanLimits(), tracks=tracks, total_segments=4, estimated_seconds=4.0)


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
    def __init__(self, text_status="accepted", fail_first_batch=False, fatal=False):
        self.calls = []
        self.text_status, self.fail_first_batch, self.fatal = text_status, fail_first_batch, fatal

    def activities(self):
        @activity.defn(name="speech_v2_prepare")
        async def prepare(inp: GenerateSpeechV2Input) -> SpeechPrepared:
            self.calls.append(("prepare", inp))
            if self.fatal:
                raise ApplicationError("not accepted", type="NotAcceptedTranslation", non_retryable=True)
            return SpeechPrepared(speech_text_ref=TEXT, status=self.text_status, gate_passed=True, gate_metrics={},
                                  plan=plan(), cache_hit=False)

        @activity.defn(name="speech_v2_synthesize_segments")
        async def synthesize(inp: SynthesizeSegmentsInput) -> SegmentBatchResult:
            self.calls.append(("synthesize", inp))
            if self.fail_first_batch and activity.info().attempt == 1:
                raise RuntimeError("TTS service restarting")
            return SegmentBatchResult(track_id=inp.track_id, provider={}, segments=[
                SegmentStatus(segment_id=sid, status="failed_qa" if sid == "s1" else "ok", audio_ref=AUDIO,
                              attempts=1, seed=inp.seed, duration_s=1.0, chars=10, cache_hit=False)
                for sid in inp.segment_ids])

        @activity.defn(name="speech_v2_assemble_track")
        async def assemble(inp: AssembleSpeechTrackInput) -> SpeechTrackResult:
            self.calls.append(("assemble", inp))
            failed = [s.segment_id for s in inp.segments if s.status != "ok"]
            return SpeechTrackResult(track_id=inp.track.track_id, artifact=None,
                                     status="rejected" if failed else "accepted", gates={}, failed_segments=failed)
        return [prepare, synthesize, assemble]


async def run(env, recorder, **kw):
    queue = f"sv-{uuid.uuid4().hex}"
    inp = GenerateSpeechV2Input(course_id="c1", assembly_ref=ASSEMBLY, activity_task_queue=queue, **kw)
    async with Worker(env.client, task_queue=queue, workflows=[GenerateSpeechV2Workflow],
                      activities=recorder.activities()):
        return await env.client.execute_workflow(GenerateSpeechV2Workflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)


async def test_workflow_prepares_synthesizes_bounded_batches_and_assembles_each_track(env):
    recorder = Recorder()
    result = await run(env, recorder)
    assert [k for k, _ in recorder.calls] == ["prepare", "synthesize", "synthesize", "assemble", "synthesize",
                                              "assemble"]
    batches = [c.segment_ids for k, c in recorder.calls if k == "synthesize"]
    assert batches == [["s0", "s1"], ["s2"], ["s3"]]
    assert [t["status"] for t in result["tracks"]] == ["rejected", "accepted"]
    assert result["accepted_tracks"] == 1
    assert result["segment_status"] == {"s0": "ok", "s1": "failed_qa", "s2": "ok", "s3": "ok"}


async def test_rejected_speech_text_stops_before_any_synthesis(env):
    recorder = Recorder(text_status="rejected")
    result = await run(env, recorder)
    assert [k for k, _ in recorder.calls] == ["prepare"] and result["status"] == "speech_text_rejected"


async def test_not_accepted_translation_fails_the_run(env):
    recorder = Recorder(fatal=True)
    with pytest.raises(WorkflowFailureError):
        await run(env, recorder)
    assert [k for k, _ in recorder.calls] == ["prepare"]


async def test_transient_batch_failure_retries_the_same_batch(env):
    recorder = Recorder(fail_first_batch=True)
    await run(env, recorder)
    batches = [c.segment_ids for k, c in recorder.calls if k == "synthesize"]
    assert batches[0] == batches[1] == ["s0", "s1"]


async def test_one_track_can_be_rendered_alone(env):
    recorder = Recorder()
    result = await run(env, recorder, track_ids=["bk:t001"])
    assert [c.segment_ids for k, c in recorder.calls if k == "synthesize"] == [["s3"]]
    assert [t["track_id"] for t in result["tracks"]] == ["bk:t001"]


def test_activity_options_are_bounded_and_deterministic_failures_do_not_retry():
    for options in (sv.PREPARE_OPTIONS, sv.SYNTHESIZE_OPTIONS, sv.ASSEMBLE_OPTIONS):
        assert options["heartbeat_timeout"] <= timedelta(minutes=5)
        assert options["start_to_close_timeout"] <= timedelta(hours=2)
        assert 1 < options["retry_policy"].maximum_attempts <= 5
        for deterministic in ("NotAcceptedTranslation", "NotSpeechReady", "TranslationChanged", "InvalidSpeechInput"):
            assert deterministic in options["retry_policy"].non_retryable_error_types


def test_new_names_leave_the_running_audio_workflow_alone():
    from temporalio import workflow as wf
    assert wf._Definition.from_class(GenerateSpeechV2Workflow).name == "GenerateSpeechV2Workflow"
    assert wf._Definition.from_class(GenerateMP3Workflow).name == "GenerateMP3Workflow"
    names = {fn.__temporal_activity_definition.name for fn in (
        sv.SpeechV2Activities.prepare, sv.SpeechV2Activities.synthesize, sv.SpeechV2Activities.assemble)}
    assert names == {"speech_v2_prepare", "speech_v2_synthesize_segments", "speech_v2_assemble_track"}
    assert "generate_mp3" not in names
