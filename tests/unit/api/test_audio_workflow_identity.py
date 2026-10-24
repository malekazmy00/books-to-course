"""The MP3 workflow ID covers the QA source while legacy runs stay reachable."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from b2c_api.app import create_app
from b2c_core import Settings
from b2c_orchestration.audio import GenerateMP3Input

from tests.unit.api.test_client_ref_verification import OUTLINE, PARSED, Store, Temporal, ref

QA_A = ref("generate-course-qa", "3" * 64, sha="c" * 64)
QA_B = ref("generate-course-qa", "4" * 64, sha="d" * 64)


class InputStore(Store):
    def __init__(self, committed=(), objects=None):
        super().__init__(committed)
        self.objects = dict(objects or {})

    def get_object(self, **kw):
        from io import BytesIO
        if kw["Key"] not in self.objects:
            raise KeyError(kw["Key"])
        return {"Body": BytesIO(self.objects[kw["Key"]])}


def legacy_id(payload):
    """The identity formula used before the QA ref became part of it."""
    digest = hashlib.sha256((
        "c" + payload["source_artifact_ref"]["sha256"] + payload["source_kind"] + payload["language"]
        + "full_book" + json.dumps([[], False, True, True], separators=(",", ":"))
        + payload["approved_outline_ref"]["sha256"] + payload["source_book_ref"]["sha256"]).encode()).hexdigest()
    return "b2c-mp3-" + digest


def payload(qa=None):
    body = {"source_artifact_ref": QA_A.model_dump(mode="json"), "source_kind": "course", "language": "ar",
            "approved_outline_ref": OUTLINE.model_dump(mode="json"),
            "source_book_ref": PARSED.model_dump(mode="json")}
    if qa is not None:
        body["source_qa_artifact_ref"] = qa.model_dump(mode="json")
    return body


async def start(store, body, monkeypatch):
    import b2c_api.routers.audio as module
    monkeypatch.setattr(module, "validate_mp3_input_lineage", lambda *args: None)
    temporal = Temporal()

    async def connect(_):
        return temporal
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/c/audio", json=body)
    assert response.status_code == 202, response.text
    return response.json()["workflow_id"]


COMMITTED = [PARSED, OUTLINE, QA_A, QA_B]


@pytest.mark.asyncio
async def test_qa_source_changes_audio_identity(monkeypatch):
    first = await start(InputStore(COMMITTED), payload(QA_A), monkeypatch)
    second = await start(InputStore(COMMITTED), payload(QA_B), monkeypatch)
    assert first != second


@pytest.mark.asyncio
async def test_requests_without_qa_source_keep_their_existing_id(monkeypatch):
    body = payload()
    assert await start(InputStore(COMMITTED), body, monkeypatch) == legacy_id(body)


@pytest.mark.asyncio
async def test_existing_run_for_the_same_qa_source_is_reused(monkeypatch):
    body = payload(QA_A)
    old = legacy_id(body)
    saved = GenerateMP3Input(course_id="c", source_artifact_ref=body["source_artifact_ref"],
                             source_kind="course", language="ar",
                             approved_outline_ref=body["approved_outline_ref"],
                             source_book_ref=body["source_book_ref"],
                             source_qa_artifact_ref=body["source_qa_artifact_ref"])
    store = InputStore(COMMITTED, {f"_system/audio-inputs/{old}.json": saved.model_dump_json().encode()})
    assert await start(store, body, monkeypatch) == old
    # The same legacy run is not reused for a different QA source.
    assert await start(store, payload(QA_B), monkeypatch) != old
