"""Client-supplied ArtifactRefs must match a committed manifest, not just a prefix."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from b2c_api.app import create_app
from b2c_contracts import ArtifactRef
from b2c_core import Settings


def ref(stage, key, sha="a" * 64, course="c"):
    return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/{course}/{stage}/{key}/",
                       sha256=sha, created_at=datetime.now(UTC))


class Store:
    bucket = "artifacts"

    def __init__(self, committed=()):
        self.s3 = self
        self.committed = {(r.stage, r.key): r for r in committed}

    def exists(self, course_id, stage, key):
        found = self.committed.get((stage, key))
        return found if found is not None and found.uri.startswith(f"s3://artifacts/{course_id}/") else None

    def get_object(self, **kw):
        raise KeyError(kw["Key"])

    def put_object(self, **kw):
        pass

    def read(self, *args):
        raise ValueError("no fixture")


class Temporal:
    def __init__(self):
        self.started = []

    def get_workflow_handle(self, _):
        async def describe():
            return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"))

        async def result():
            return {"course_id": "c", "outline_sha256": "b" * 64}
        return SimpleNamespace(describe=describe, result=result)

    async def start_workflow(self, *args, **kwargs):
        self.started.append((args, kwargs))


async def post(store, path, payload):
    temporal = Temporal()

    async def connect(_):
        return temporal
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, json=payload)
    return response, temporal


PARSED = ref("ingest-parse-pdf", "1" * 64)
OUTLINE = ref("approve-outline", "2" * 64, sha="b" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize("committed", [(), (PARSED.model_copy(update={"sha256": "f" * 64}),)])
async def test_translation_rejects_uncommitted_or_forged_source(committed):
    response, temporal = await post(Store(committed), "/v1/courses/c/translations", {
        "source_artifact_ref": PARSED.model_dump(mode="json"),
        "source_language": "en", "target_language": "ar"})
    assert response.status_code == 422, response.text
    assert temporal.started == []


@pytest.mark.asyncio
async def test_translation_accepts_committed_source():
    response, temporal = await post(Store([PARSED]), "/v1/courses/c/translations", {
        "source_artifact_ref": PARSED.model_dump(mode="json"),
        "source_language": "en", "target_language": "ar"})
    assert response.status_code == 202, response.text
    assert len(temporal.started) == 1


@pytest.mark.asyncio
async def test_qa_generation_rejects_uncommitted_outline():
    response, temporal = await post(Store(), "/v1/courses/c/qa/generate", {
        "approved_outline_ref": OUTLINE.model_dump(mode="json"),
        "index_workflow_id": "b2c-index-" + "1" * 64})
    assert response.status_code == 422, response.text
    assert "not a committed artifact" in response.json()["detail"]
    assert temporal.started == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_artifact_ref", "approved_outline_ref", "source_book_ref",
                                   "source_qa_artifact_ref", "source_book_translation_ref"])
async def test_audio_rejects_any_uncommitted_reference(field):
    qa = ref("generate-course-qa", "3" * 64)
    translation = ref("translate-content", "4" * 64)
    refs = {"source_artifact_ref": qa, "approved_outline_ref": OUTLINE, "source_book_ref": PARSED,
            "source_qa_artifact_ref": qa, "source_book_translation_ref": translation}
    committed = [value for name, value in refs.items() if name != field and value is not refs[field]]
    payload = {name: value.model_dump(mode="json") for name, value in refs.items()}
    payload.update({"source_kind": "course", "language": "ar"})
    response, temporal = await post(Store(committed), "/v1/courses/c/audio", payload)
    assert response.status_code == 422, response.text
    assert "not a committed artifact" in response.json()["detail"]
    assert temporal.started == []
