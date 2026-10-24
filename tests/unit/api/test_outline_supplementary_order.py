"""Supplementary book selection is a set: request order must not matter."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from b2c_api.app import create_app
from b2c_contracts import ArtifactRef, Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_core import Settings

from tests.unit.api.test_book_upload import FakeStore

SPINE_WF = "b2c-ingest-" + "1" * 64
SUPPLEMENTS = {"book-b1": "b2c-ingest-" + "2" * 64, "book-b2": "b2c-ingest-" + "3" * 64}


def parsed_ref(key):
    return ArtifactRef(stage="ingest-parse-pdf", key=key * 64,
                       uri=f"s3://artifacts/course-1/ingest-parse-pdf/{key * 64}/",
                       sha256=key * 64, created_at=datetime.now(UTC))


def book(book_id, role):
    return ParsedBook(book_id=book_id, title=book_id, role=role, page_count=1, toc_source="pdf_outline",
                      blocks=[Block(block_id=f"{book_id}:1:0", type="heading", level=1, text="Lesson 1",
                                    refs=[PageRef(book_id=book_id, page_no=1)], section_path=["Lesson 1"], ocr=False),
                              Block(block_id=f"{book_id}:1:1", type="paragraph", text="A fair coin has two outcomes.",
                                    refs=[PageRef(book_id=book_id, page_no=1)], section_path=["Lesson 1"], ocr=False)],
                      parser={"name": "fixture", "version": "1", "options_hash": "e" * 64})


@pytest.mark.asyncio
async def test_system_approval_accepts_supplements_in_any_order():
    draft_ref = ArtifactRef(stage="build-outline", key="a" * 64,
                            uri="s3://artifacts/course-1/build-outline/" + "a" * 64 + "/",
                            sha256="b" * 64, created_at=datetime.now(UTC))
    refs = {SPINE_WF: parsed_ref("c"), SUPPLEMENTS["book-b1"]: parsed_ref("d"), SUPPLEMENTS["book-b2"]: parsed_ref("e")}
    books = {"c" * 64: book("book-main", "spine"), "d" * 64: book("book-b1", "supplementary"),
             "e" * 64: book("book-b2", "supplementary")}
    draft = CourseOutline(course_id="course-1", spine_book_id="book-main",
                          supplementary_book_ids=["book-b1", "book-b2"], requires_review=True,
                          modules=[Module(module_id="m1", title="Lesson 1", order=1, lessons=[
                              Lesson(lesson_id="m1.l1", title="Lesson 1", order=1,
                                     spine_span=LessonSpan(start_block="book-main:1:0", end_block="book-main:1:1",
                                                           pages=(1, 1)),
                                     provenance="pdf_outline", prior_lessons=[])])])
    store = FakeStore()
    store.read = lambda ref, name: (draft.model_dump_json() if name == "course-outline.json"
                                    else books[ref.key].model_dump_json()).encode()

    class Temporal:
        def get_workflow_handle(self, workflow_id):
            async def describe():
                return SimpleNamespace(status=SimpleNamespace(name="COMPLETED"), start_time=None, close_time=None)

            async def result():
                value = {"parsed_book": {"ref": refs[workflow_id].model_dump(mode="json")}}
                if workflow_id == SPINE_WF:
                    value["outline"] = {"ref": draft_ref.model_dump(mode="json")}
                return value
            return SimpleNamespace(describe=describe, result=result)

        async def start_workflow(self, *args, **kwargs):
            return None

    async def connect(_):
        return Temporal()
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    request = {"supplementary_workflow_ids": {"book-b2": SUPPLEMENTS["book-b2"], "book-b1": SUPPLEMENTS["book-b1"]},
               "index_after_approval": False}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/v1/ingestions/{SPINE_WF}/outline/approve-system", json=request)
    assert response.status_code == 201, response.text
    approved = json.loads(store.put_calls[-1][3]["approved-outline.json"])
    assert set(approved["supplementary_parsed_refs"]) == {"book-b1", "book-b2"}
    assert approved["outline"]["supplementary_book_ids"] == ["book-b1", "book-b2"]
