"""CoursePipelineWorkflow: one book through the new line, each stage a child workflow (charter §4).

Fake child workflows stand in for the stages (same names), on Temporal's time-skipping test server.
The pipeline never asks a person: it stops only where a stage's own gate refuses its output, and
it says where and why.
"""
from __future__ import annotations

import uuid

import pytest
from temporalio import activity, workflow
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
        return _record("episodes", inp) or {"episodes": {
            f"{e:02d}": {"ar": {"stage_output_status": "accepted", "title": f"Episode {e}", "book_title": "SAT",
                                "ref": {"stage": "episode-script-ar", "key": f"{e:02d}"}}}
            for e in inp["episodes"]}}


@workflow.defn(name="GenerateSpeechV2Workflow", sandboxed=False)
class FakeSpeech:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        key = inp["episode_ref"]["key"]
        return _record(f"speech-{key}", inp) or {"status": "completed", "tracks": [
            {"track_id": f"t{key}", "artifact": {"stage": "speech-track", "key": key}, "status": "accepted"}]}


@workflow.defn(name="EncodeEpisodeMp3Workflow", sandboxed=False)
class FakeEncode:
    @workflow.run
    async def run(self, inp: dict) -> dict:
        return _record("mp3", inp) or {"episodes": [{"episode": e["episode"], "status": "accepted"}
                                                    for e in inp["episodes"]], "accepted": len(inp["episodes"])}


FAKES = [FakeIngest, FakeTranslate, FakeKnowledge, FakeFigures, FakeEpisodes, FakeSpeech, FakeEncode]


@activity.defn(name="language_research")
async def fake_language(inp: dict) -> dict:
    CALLS.append(("language", dict(inp)))
    return SCRIPT.get("language") or {"ref": {"stage": "language-research"}, "metrics": {"not_established": 2}}


@activity.defn(name="prepare_translation_units")
async def fake_prepare(inp: dict) -> dict:
    CALLS.append(("prepare", dict(inp)))
    return {"units_ref": UNITS, "unit_ids": ["bk:u00000"], "book_id": "bk", "cache_hit": False}


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
    queues = dict(ingest_queue=queue, translate_queue=queue, companion_queue=queue, audio_queue=queue)
    inp = inp.model_copy(update=queues)
    async with Worker(env.client, task_queue=queue, workflows=[CoursePipelineWorkflow, *FAKES],
                      activities=[fake_prepare, fake_language]):
        handle = await env.client.start_workflow(CoursePipelineWorkflow.run, inp, id=f"wf-{uuid.uuid4().hex}",
                                                 task_queue=queue)
        result = await handle.result()
        state = await handle.query(PIPELINE_QUERY)
    return result, state


async def test_stages_run_in_the_decided_order_and_pass_their_outputs_on(env):
    """D-034: knowledge research (EN) runs BEFORE translation, on the prepared units."""
    SCRIPT.clear()
    result, state = await run(env, pipeline_input())
    assert [n for n, _ in CALLS] == ["ingest", "prepare", "knowledge", "translate", "language", "figures", "episodes",
                                     "speech-00", "speech-01", "speech-02", "speech-03", "mp3"]
    assert dict(CALLS)["language"]["termbase_ref"] == APPROVED              # D-034: Arabic research after translation
    calls = dict(CALLS)
    assert calls["prepare"]["inp"]["source_artifact_ref"] == PARSED
    assert calls["knowledge"]["units_ref"] == UNITS and calls["knowledge"]["outline_ref"] == OUTLINE
    assert calls["knowledge"].get("termbase_ref") is None          # research comes before the termbase
    assert calls["translate"]["source_artifact_ref"] == PARSED
    assert calls["translate"]["qe_profile"] == "ladder-mqm" and calls["translate"]["termbase_review"] == "models"
    assert calls["translate"]["knowledge_ref"] == {"stage": "knowledge-research"}   # D-034: research informs the termbase
    assert calls["figures"]["units_ref"] == UNITS and calls["figures"]["termbase_ref"] == APPROVED
    assert calls["episodes"]["episodes"] == [0, 1, 2, 3] and calls["episodes"]["translation_run_id"] == "a" * 64
    assert calls["episodes"]["figures_ref"] == {"stage": "figure-descriptions"}
    assert calls["episodes"]["require_accepted_inputs"] is True
    assert result["status"] == "audio_ready" and result["findings"] == []
    assert result["language_research_ref"] == {"stage": "language-research"}
    assert state["phase"] == "done" and state["stages"]["episodes"]["accepted"] == 4


async def test_the_audio_comes_out_episode_by_episode(env):
    """D-036..D-038/D-042: one speech run per episode script, then one MP3 per episode, 00..n."""
    SCRIPT.clear()
    result, _ = await run(env, pipeline_input())
    calls = dict(CALLS)
    speech = calls["speech-02"]
    assert speech["episode_ref"]["key"] == "02" and speech.get("assembly_ref") is None
    assert speech["termbase_ref"] == APPROVED and speech["notes_ref"] == {"stage": "translator-notes"}
    episodes = calls["mp3"]["episodes"]
    assert [e["episode"] for e in episodes] == [0, 1, 2, 3] and {e["last_episode"] for e in episodes} == {3}
    assert episodes[2]["track_refs"] == [{"stage": "speech-track", "key": "02"}] and episodes[2]["title"] == "Episode 2"
    assert episodes[2]["book_title"] == "SAT"
    assert result["mp3"]["accepted"] == 4


async def test_a_track_its_gates_did_not_accept_is_still_encoded_and_recorded(env):
    SCRIPT.clear()
    SCRIPT["speech-01"] = {"status": "completed", "tracks": [
        {"track_id": "t01", "artifact": {"stage": "speech-track", "key": "01"}, "status": "rejected"}]}
    result, _ = await run(env, pipeline_input())
    mp3 = dict(CALLS)["mp3"]["episodes"]
    assert [e["episode"] for e in mp3] == [0, 1, 2, 3] and all(e["require_accepted_tracks"] is False for e in mp3)
    assert {"stage": "speech", "finding": "episode_01_track_t01_rejected"} in result["findings"]


async def test_speech_can_be_left_for_later(env):
    SCRIPT.clear()
    result, _ = await run(env, pipeline_input(speech=False))
    assert [n for n, _ in CALLS][-1] == "episodes" and result["status"] == "scripts_ready"


async def test_a_refused_parse_does_not_stop_the_book(env):
    """D-033/D-052: the ingest gate's findings travel on; the line continues."""
    SCRIPT.clear()
    SCRIPT["ingest"] = {"status": "accepted_with_findings", "findings": ["glued_token_rate"],
                        "parsed_book": {"ref": PARSED, "accepted": False},
                        "outline": {"ref": OUTLINE, "stage_output_status": "accepted", "lesson_count": 3}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS][-1] == "mp3" and result["status"] == "audio_ready"
    assert {"stage": "ingest", "finding": "glued_token_rate"} in result["findings"]


async def test_an_outline_its_gates_did_not_accept_is_used_and_recorded(env):
    SCRIPT.clear()
    SCRIPT["ingest"] = {"status": "accepted", "parsed_book": {"ref": PARSED, "accepted": True},
                        "outline": {"ref": OUTLINE, "stage_output_status": "rejected", "lesson_count": 3}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS][-1] == "mp3"
    assert {"stage": "outline", "finding": "outline_not_accepted"} in result["findings"]
    assert dict(CALLS)["episodes"]["require_accepted_inputs"] is False


async def test_figures_can_be_skipped_and_episodes_limited(env):
    SCRIPT.clear()
    result, _ = await run(env, pipeline_input(figures=False, episodes=[0, 1]))
    assert [n for n, _ in CALLS] == ["ingest", "prepare", "knowledge", "translate", "language", "episodes",
                                     "speech-00", "speech-01", "mp3"]
    episodes = dict(CALLS)["episodes"]
    assert episodes["episodes"] == [0, 1] and episodes.get("figures_ref") is None


async def test_concepts_not_accepted_do_not_withhold_the_episodes(env):
    SCRIPT.clear()
    SCRIPT["knowledge"] = {"research": {"ref": {"stage": "knowledge-research"}, "stage_output_status": "accepted"},
                           "concepts": {"ref": {"stage": "concept-layer"}, "stage_output_status": "rejected"}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS][-1] == "mp3" and result["status"] == "audio_ready"
    assert dict(CALLS)["episodes"]["require_accepted_inputs"] is False
    assert {"stage": "knowledge", "finding": "concepts_not_accepted"} in result["findings"]


async def test_translator_notes_not_accepted_are_recorded_and_the_line_goes_on(env):
    SCRIPT.clear()
    SCRIPT["translate"] = {"run_id": "a" * 64, "units_ref": UNITS, "termbase": {"approved_ref": APPROVED},
                           "translator_notes": {"artifact": {"stage": "translator-notes"}, "status": "rejected"}}
    result, _ = await run(env, pipeline_input())
    assert [n for n, _ in CALLS][-1] == "mp3"
    assert {"stage": "translate", "finding": "translator_notes_not_accepted"} in result["findings"]


def test_workflow_id_is_stable_per_course_and_source():
    a = course_pipeline_workflow_id(pipeline_input())
    assert a == course_pipeline_workflow_id(pipeline_input()) and a.startswith("b2c-course-pipeline-")
    assert a != course_pipeline_workflow_id(pipeline_input(course_id="c2"))



async def test_the_debate_is_off_unless_chosen(env):
    SCRIPT.clear()
    await run(env, pipeline_input())
    assert dict(CALLS)["episodes"]["debate_episodes"] == []


async def test_the_debate_follows_the_chosen_episodes_or_every_n(env):
    SCRIPT.clear()
    await run(env, pipeline_input(debate_episodes=[2]))
    assert dict(CALLS)["episodes"]["debate_episodes"] == [2]
    SCRIPT.clear()
    await run(env, pipeline_input(debate_every=2))
    assert dict(CALLS)["episodes"]["debate_episodes"] == [2]          # lessons 1..3, every second one
