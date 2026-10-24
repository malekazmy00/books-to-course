import hashlib
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from temporalio.service import RPCError, RPCStatusCode

from b2c_api.routers import indexing as api_module
from b2c_api.app import create_app
from b2c_api.ingestion_recovery import recover_index
from b2c_contracts import ArtifactRef, CourseOutline, Lesson, LessonSpan, Module, StructuredChunk
from b2c_core import Settings


class Store:
    bucket = "artifacts"

    def __init__(self):
        self.s3 = self
        outline = CourseOutline(course_id="course", spine_book_id="book",
            supplementary_book_ids=[], requires_review=False,
            modules=[Module(module_id="m", title="Module", order=1,
                lessons=[Lesson(lesson_id="lesson", title="Lesson", order=1,
                    spine_span=LessonSpan(start_block="b1", end_block="b1", pages=(1, 1)),
                    provenance="headings", prior_lessons=[])])])
        self.outline = outline.model_dump(mode="json")
        self.approved = self.ref("approve-outline", "a")
        self.parsed = self.ref("ingest-parse-pdf", "b")
        self.snapshot = self.ref("index-approved-book", "c")
        identity = {"course_id": "course", "spine_book_id": "book",
            "outline": self.outline, "target_tokens": 500, "overlap_ratio": 0.12}
        self.wid = "b2c-index-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        self.record = {"course_id": "course", "book_id": "book", "stages": {
            "outline": {"approved_outline_ref": self.approved.model_dump(mode="json")},
            "index": {"workflow_id": self.wid}}}
        self.envelope = {"outline": self.outline, "spine_parsed_ref": self.parsed.model_dump(mode="json")}
        self.chunks = [StructuredChunk(chunk_id="chunk1", book_id="book", role="spine",
            section_path=["Module", "Lesson"], block_ids=["b1"], pages=[1],
            text="Durable indexed source passage.", token_count=5, embedding_model="unassigned")]

    def ref(self, stage, letter):
        return ArtifactRef(stage=stage, key=letter * 64,
            uri=f"s3://artifacts/course/{stage}/{letter * 64}/",
            sha256=letter * 64, created_at=datetime.now(UTC))

    def get_paginator(self, _):
        return self

    def paginate(self, **kwargs):
        return [{"Contents": [{"Key": "course/book-progress/book.json"}]}]

    def get_object(self, **kwargs):
        return {"Body": io.BytesIO(json.dumps(self.record).encode())}

    def exists(self, cid, stage, key):
        return {"approve-outline": self.approved, "ingest-parse-pdf": self.parsed,
            "index-approved-book": self.snapshot}[stage]

    def read(self, ref, name):
        return json.dumps(self.envelope if name == "approved-outline.json" else
            [chunk.model_dump(mode="json") for chunk in self.chunks]).encode()


class Chunks:
    def __init__(self, values=None):
        self.values = values or []

    def list_chunks(self, cid, **kwargs):
        return self.values


async def test_exact_committed_index_recovers_after_retention():
    store = Store()
    result = await recover_index(store, Chunks(store.chunks), store.wid)
    assert result["course_id"] == "course"
    assert result["books"]["book"]["artifact_ref"]["key"] == "c" * 64


async def test_changed_parameters_cannot_recover_another_execution():
    store = Store()
    store.record["stages"]["index"]["index_parameters"] = {
        "target_tokens": 100, "overlap_ratio": 0.12, "embedding_model": "unassigned"}
    assert await recover_index(store, Chunks(), store.wid) is None


async def test_index_mismatch_does_not_claim_committed_success():
    class WrongChunk:
        chunk_id = "foreign"

        def model_dump(self, **kwargs):
            return {"chunk_id": self.chunk_id}

    store = Store()
    assert await recover_index(store, Chunks([WrongChunk()]), store.wid) is None


async def test_status_and_rag_view_recover_expired_history(monkeypatch):
    store = Store()

    class Temporal:
        def get_workflow_handle(self, workflow_id):
            async def describe():
                raise RPCError("history expired", RPCStatusCode.NOT_FOUND, b"")
            return SimpleNamespace(describe=describe)

    async def connect(_):
        return Temporal()

    monkeypatch.setattr(api_module, "PostgresChunkStore", lambda _: Chunks(store.chunks))
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get(f"/v1/indexings/{store.wid}")
        view = await client.get(f"/v1/indexings/{store.wid}/chunks")
    assert status.status_code == view.status_code == 200
    assert status.json()["completion_scope"] == "persisted_index"
    assert status.json()["workflow_history_available"] is False
    assert view.json()["chunks"][0]["text"] == "Durable indexed source passage."


async def test_qa_preflight_accepts_durable_index_without_starting_workflow(monkeypatch):
    store = Store()

    class Temporal:
        def get_workflow_handle(self, workflow_id):
            async def describe():
                raise RPCError("history expired", RPCStatusCode.NOT_FOUND, b"")
            return SimpleNamespace(describe=describe)

        async def start_workflow(self, *args, **kwargs):
            raise AssertionError("invalid duplicate lessons must not start work")

    async def connect(_):
        return Temporal()

    monkeypatch.setattr(api_module, "PostgresChunkStore", lambda _: Chunks(store.chunks))
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/courses/course/qa/generate", json={
            "approved_outline_ref": store.approved.model_dump(mode="json"),
            "index_workflow_id": store.wid, "retrieval_provider_id": "lexical",
            "lesson_ids": ["lesson", "lesson"]})
    # Reaching this later input guard proves the recovered index passed
    # exact course/approved-outline checks, without running an inference job.
    assert response.status_code == 422
    assert response.json()["detail"] == "lesson_ids must not contain duplicates"
