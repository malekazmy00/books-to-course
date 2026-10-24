"""Client error details never echo raw exception text or input values."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from b2c_api.app import create_app
from b2c_contracts import ArtifactRef, CourseOutline, Lesson, LessonSpan, Module
from b2c_core import Settings

from tests.unit.api.test_audio_workflow_identity import COMMITTED, InputStore, payload
from tests.unit.api.test_book_upload import FakeStore, FakeTemporal, _resolved, post_upload
from tests.unit.api.test_client_ref_verification import Temporal
from tests.unit.api.test_outline_supplementary_order import SPINE_WF, book, parsed_ref

SECRET = "SECRET-INPUT-VALUE"


@pytest.mark.asyncio
async def test_upload_storage_rejection_is_generic():
    store = FakeStore()

    def reject(*args, **kwargs):
        raise ValueError(f"unsafe course_id: {SECRET!r}")
    store.put_stream = reject
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(FakeTemporal()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client)
    assert response.status_code == 422
    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_audio_lineage_rejection_is_generic(monkeypatch):
    import b2c_api.routers.audio as module

    def reject(*args):
        raise ValueError(f"lineage mismatch for {SECRET}")
    monkeypatch.setattr(module, "validate_mp3_input_lineage", reject)

    async def connect(_):
        return Temporal()
    app = create_app(settings=Settings(), artifact_store=InputStore(COMMITTED), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/c/audio", json=payload())
    assert response.status_code == 422
    assert SECRET not in response.text


@pytest.mark.asyncio
async def test_system_review_validation_error_keeps_locations_only(monkeypatch):
    import b2c_api.routers.ingestion as module

    try:
        CourseOutline.model_validate({"course_id": SECRET, "spine_book_id": 7})
    except ValidationError as exc:
        error = exc

    def reject(*args):
        raise error
    monkeypatch.setattr(module, "review_outline", reject)
    spine = parsed_ref("c")
    draft_ref = ArtifactRef(stage="build-outline", key="a" * 64,
                            uri="s3://artifacts/course-1/build-outline/" + "a" * 64 + "/",
                            sha256="b" * 64, created_at=datetime.now(UTC))
    draft = CourseOutline(course_id="course-1", spine_book_id="book-main", supplementary_book_ids=[],
                          requires_review=True, modules=[Module(module_id="m1", title="Lesson 1", order=1, lessons=[
                              Lesson(lesson_id="m1.l1", title="Lesson 1", order=1,
                                     spine_span=LessonSpan(start_block="book-main:1:0", end_block="book-main:1:1",
                                                           pages=(1, 1)),
                                     provenance="pdf_outline", prior_lessons=[])])])
    store = FakeStore()
    store.read = lambda ref, name: (draft.model_dump_json() if name == "course-outline.json"
                                    else book("book-main", "spine").model_dump_json()).encode()
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    temporal.result_value = {"outline": {"ref": draft_ref.model_dump(mode="json")},
                             "parsed_book": {"ref": spine.model_dump(mode="json")}}
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=lambda _: _resolved(temporal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{SPINE_WF}/outline/approve",
                                     json={"review_type": "system", "outline": draft.model_dump(mode="json")})
    assert response.status_code == 422, response.text
    assert SECRET not in response.text
    detail = response.json()["detail"]
    assert detail["message"] == "system structural review rejected"
    assert {"loc", "type"} <= set(detail["errors"][0])
