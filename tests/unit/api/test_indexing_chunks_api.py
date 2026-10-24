from __future__ import annotations

import httpx
import pytest
from b2c_api.routers import indexing as api_module
from b2c_api.app import create_app
from b2c_core import Settings

WORKFLOW_ID = "b2c-index-" + "1" * 64


class FakeChunk:
    chunk_id = "book-main:chunk:1"

    def model_dump(self, *, mode: str) -> dict:
        assert mode == "json"
        return {"chunk_id": "book-main:chunk:1", "book_id": "book-main",
                "role": "spine", "text": "A durable indexed passage.", "pages": [1]}


class FakeChunkStore:
    def __init__(self, connect):
        self.connect = connect

    def list_chunks(self, course_id: str, *, book_ids=None):
        assert course_id == "b2c-demo"
        assert book_ids == {"book-main"}
        return [FakeChunk()]


class FakeHandle:
    async def describe(self):
        return type("Description", (), {"status": type("Status", (), {"name": "COMPLETED"})()})()

    async def result(self):
        # Match the deployed worker result shape: no top-level artifact ref.
        return {"course_id": "b2c-demo", "chunk_count": 1,
                "books": {"book-main": {"chunk_count": 1, "chunk_ids": ["book-main:chunk:1"]}}}


class FakeTemporalClient:
    def get_workflow_handle(self, workflow_id: str):
        assert workflow_id == WORKFLOW_ID
        return FakeHandle()


@pytest.mark.asyncio
async def test_index_chunks_view_reads_persisted_chunks_from_course_store(monkeypatch) -> None:
    monkeypatch.setattr(api_module, "PostgresChunkStore", FakeChunkStore)

    async def temporal_connect(_settings):
        return FakeTemporalClient()

    app = create_app(settings=Settings(), temporal_connect=temporal_connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/indexings/{WORKFLOW_ID}/chunks")

    assert response.status_code == 200
    body = response.json()
    assert body["chunk_count"] == 1
    assert body["chunks"][0]["text"] == "A durable indexed passage."
