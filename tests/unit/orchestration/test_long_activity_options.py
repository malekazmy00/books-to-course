"""Long activities must heartbeat, have bounded retries, and stay replay-safe."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

import b2c_orchestration.audio as audio_mod
import b2c_orchestration.heartbeat as heartbeat_mod
import b2c_orchestration.indexing as indexing_mod
import b2c_orchestration.ingest as ingest_mod
import b2c_orchestration.qa as qa_mod
from b2c_orchestration.audio import GenerateMP3Input
from b2c_orchestration.indexing import IndexApprovedBookInput
from b2c_orchestration.ingest import IngestPDFInput
from b2c_orchestration.qa import GenerateCourseQAInput


def _capture_checkpointed(monkeypatch, mod, calls):
    async def execute(owner, name, inp, **options):
        calls.append((name, options))
        if name == "ingest_parse_pdf":
            return {"ref": {}}
        return {}
    monkeypatch.setattr(mod, "execute_checkpointed", execute)


def _patched(monkeypatch, value, seen):
    def patched(patch_id):
        seen.append(patch_id)
        return value
    monkeypatch.setattr(ingest_mod.workflow, "patched", patched)


def _ingest_input():
    return IngestPDFInput(course_id="c", upload={"role": "spine"}, source_ref={},
                          supplementary_book_ids=[])


@pytest.mark.parametrize("patched", [True, False])
def test_ingest_parse_heartbeat_is_patched(monkeypatch, patched):
    calls, seen = [], []
    _capture_checkpointed(monkeypatch, ingest_mod, calls)
    _patched(monkeypatch, patched, seen)
    asyncio.run(ingest_mod.IngestPDFWorkflow().run(_ingest_input()))
    parse = dict(calls)["ingest_parse_pdf"]
    assert "ingest-parse-heartbeat-v1" in seen
    if patched:
        assert parse["heartbeat_timeout"] == timedelta(minutes=5)
    else:
        assert "heartbeat_timeout" not in parse
    assert parse["start_to_close_timeout"] == timedelta(hours=24)


@pytest.mark.parametrize("patched", [True, False])
def test_qa_heartbeat_timeout_is_patched(monkeypatch, patched):
    captured, seen = {}, []

    async def execute_activity(name, inp, **options):
        captured.update(options)
        return {}
    monkeypatch.setattr(qa_mod.workflow, "execute_activity", execute_activity)
    _patched(monkeypatch, patched, seen)
    inp = GenerateCourseQAInput(course_id="c", approved_outline_ref={}, index_result={})
    asyncio.run(qa_mod.GenerateCourseQAWorkflow().run(inp))
    assert "qa-heartbeat-timeout-v1" in seen
    if patched:
        assert captured["heartbeat_timeout"] == timedelta(minutes=5)
    else:
        assert "heartbeat_timeout" not in captured
    assert captured["retry_policy"].maximum_attempts == 3


@pytest.mark.parametrize("patched", [True, False])
def test_index_heartbeat_and_retry_are_patched(monkeypatch, patched):
    calls, seen = [], []
    _capture_checkpointed(monkeypatch, indexing_mod, calls)
    _patched(monkeypatch, patched, seen)
    inp = IndexApprovedBookInput(course_id="c", parsed_ref={}, approved_outline_ref={})
    asyncio.run(indexing_mod.IndexApprovedBookWorkflow().run(inp))
    options = dict(calls)["index_approved_book"]
    assert {"index-heartbeat-timeout-v1", "index-bounded-retry-v1"} <= set(seen)
    if patched:
        assert options["heartbeat_timeout"] == timedelta(minutes=5)
        assert 1 < options["retry_policy"].maximum_attempts <= 5
    else:
        assert "heartbeat_timeout" not in options
        assert "retry_policy" not in options


@pytest.mark.parametrize("patched", [True, False])
def test_mp3_heartbeat_and_retry_are_patched(monkeypatch, patched):
    calls, seen = [], []
    _capture_checkpointed(monkeypatch, audio_mod, calls)
    _patched(monkeypatch, patched, seen)
    inp = GenerateMP3Input(course_id="c", source_artifact_ref={}, source_kind="book", language="ar")
    asyncio.run(audio_mod.GenerateMP3Workflow().run(inp))
    options = dict(calls)["generate_mp3"]
    assert {"mp3-heartbeat-timeout-v1", "mp3-bounded-retry-v1"} <= set(seen)
    if patched:
        assert options["heartbeat_timeout"] == timedelta(minutes=5)
        assert 1 < options["retry_policy"].maximum_attempts <= 3
    else:
        assert "heartbeat_timeout" not in options
        assert "retry_policy" not in options


def _fake_activity_context(monkeypatch):
    received = []
    monkeypatch.setattr(heartbeat_mod.activity, "info", lambda: SimpleNamespace(workflow_id="wf-1"))
    monkeypatch.setattr(heartbeat_mod.activity, "heartbeat", lambda detail: received.append(detail))
    monkeypatch.setattr(heartbeat_mod, "heartbeat_interval_seconds", lambda *a, **k: 0.01)
    return received


def _slow(*_args, **_kwargs):
    time.sleep(0.08)
    return {"ok": True}


@pytest.mark.parametrize("owner,public,private,kind", [
    (ingest_mod.IngestPDFActivities(store=None), "parse_pdf", "_parse_pdf", "ingest_parse_liveness"),
    (ingest_mod.OutlineActivities(store=None), "build", "_build", "outline_liveness"),
    (indexing_mod.IndexActivities(artifact_store=None, chunk_store=None), "index_approved_book",
     "_index_approved_book", "index_liveness"),
    (qa_mod.CourseQAActivities(artifact_store=None, chunk_store=None, gateway=None), "generate",
     "_generate", "qa_liveness"),
    (audio_mod.MP3Activities(artifact_store=None), "generate", "_generate", "mp3_liveness"),
])
def test_long_activities_heartbeat_during_blocking_work(monkeypatch, owner, public, private, kind):
    received = _fake_activity_context(monkeypatch)
    monkeypatch.setattr(owner, private, _slow)
    assert getattr(owner, public)(object()) == {"ok": True}
    count = len(received)
    assert count >= 2
    assert all(detail == {"workflow_id": "wf-1", "kind": kind} for detail in received)
    time.sleep(0.05)
    assert len(received) == count, "the pump must stop with the activity"


def test_heartbeat_helper_is_noop_outside_activity():
    with heartbeat_mod.activity_heartbeats("x"):
        pass
