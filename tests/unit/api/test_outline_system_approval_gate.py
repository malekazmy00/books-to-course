"""R0 containment (audit 02 P3): automatic system outline approval is not a production path.

``approve-system`` and ``/approve`` with ``review_type=system`` are refused with 409 unless the
operator explicitly opts in with ``B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL=true``. The human approval
route stays available.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from b2c_api.app import create_app
from b2c_contracts import ArtifactRef, Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_core import Settings

from tests.unit.api.test_book_upload import FakeStore, FakeTemporal, _resolved

WORKFLOW = "b2c-ingest-" + "1" * 64
ARABIC = re.compile(r"[؀-ۿ]")


def _outline() -> CourseOutline:
    return CourseOutline(course_id="course-1", spine_book_id="book-main", supplementary_book_ids=[],
        requires_review=True, modules=[Module(module_id="m1", title="Lesson 1", order=1, lessons=[
            Lesson(lesson_id="m1.l1", title="Lesson 1", order=1,
                   spine_span=LessonSpan(start_block="book-main:1:0", end_block="book-main:1:1", pages=(1, 1)),
                   provenance="pdf_outline", prior_lessons=[])])])


def _parsed() -> ParsedBook:
    return ParsedBook(book_id="book-main", title="Probability", role="spine", page_count=1, toc_source="pdf_outline",
        blocks=[Block(block_id="book-main:1:0", type="heading", level=1, text="Lesson 1",
                      refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Lesson 1"], ocr=False),
                Block(block_id="book-main:1:1", type="paragraph", text="A fair coin has two outcomes.",
                      refs=[PageRef(book_id="book-main", page_no=1)], section_path=["Lesson 1"], ocr=False)],
        parser={"name": "fixture", "version": "1", "options_hash": "e" * 64})


def _app(temporal: FakeTemporal):
    now = datetime.now(UTC)
    temporal.status_name = "COMPLETED"
    temporal.result_value = {
        "outline": {"ref": ArtifactRef(stage="build-outline", key="a" * 64, sha256="b" * 64, created_at=now,
                                       uri="s3://artifacts/course-1/build-outline/draft/").model_dump(mode="json")},
        "parsed_book": {"ref": ArtifactRef(stage="ingest-parse-pdf", key="c" * 64, sha256="d" * 64, created_at=now,
                                           uri="s3://artifacts/course-1/ingest-parse-pdf/p/").model_dump(mode="json")}}
    store = FakeStore()
    store.read = lambda ref, name: (_outline() if name == "course-outline.json" else _parsed()).model_dump_json().encode()
    return create_app(settings=Settings(), artifact_store=store, temporal_connect=lambda _: _resolved(temporal)), store


def _assert_refusal(response, store):
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert ARABIC.search(detail["message_ar"]), detail
    assert "system outline approval is disabled" in detail["message_en"]
    assert detail["setting"] == "B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL"
    assert store.put_calls == []


def test_system_outline_approval_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    assert Settings().allow_system_outline_approval is False


async def test_approve_system_route_refuses_by_default(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    temporal = FakeTemporal()
    app, store = _app(temporal)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{WORKFLOW}/outline/approve-system",
                                     json={"index_after_approval": False})
    _assert_refusal(response, store)
    assert temporal.starts == []


async def test_system_review_type_on_approve_route_refuses_by_default(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    temporal = FakeTemporal()
    app, store = _app(temporal)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{WORKFLOW}/outline/approve",
                                     json={"review_type": "system", "outline": _outline().model_dump(mode="json"),
                                           "index_after_approval": False})
    _assert_refusal(response, store)


async def test_human_approval_route_still_works_by_default(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    temporal = FakeTemporal()
    app, store = _app(temporal)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{WORKFLOW}/outline/approve",
                                     json={"review_type": "human", "reviewer_id": "owner",
                                           "outline": _outline().model_dump(mode="json"),
                                           "index_after_approval": False})
    assert response.status_code == 201, response.text
    assert response.json()["review_type"] == "human"


def test_android_operator_app_does_not_call_approve_system_automatically():
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    source = (root / "android/operator-app/app/src/main/java/com/book2course/operator/MainActivity.java").read_text(
        encoding="utf-8")
    assert "/outline/approve-system" not in source
