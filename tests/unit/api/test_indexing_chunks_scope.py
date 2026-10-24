"""The RAG chunk view shows only the chunks of the requested index run."""
from __future__ import annotations

import httpx
import pytest

from b2c_api import app as api_module
from b2c_api.app import create_app
from b2c_core import Settings

from tests.unit.api.test_indexing_chunks_api import WORKFLOW_ID, FakeTemporalClient


class Chunk:
    def __init__(self, chunk_id, book_id):
        self.chunk_id, self.book_id = chunk_id, book_id

    def model_dump(self, *, mode):
        return {"chunk_id": self.chunk_id, "book_id": self.book_id, "text": self.chunk_id}


@pytest.mark.asyncio
async def test_chunks_from_other_runs_and_books_are_excluded(monkeypatch):
    queries = []

    class Store:
        def __init__(self, connect):
            pass

        def list_chunks(self, course_id, *, book_ids=None, roles=None):
            queries.append((course_id, book_ids))
            # A re-index with other parameters left a stale chunk in the same book.
            return [Chunk("book-main:chunk:1", "book-main"), Chunk("book-main:chunk:old", "book-main")]

    monkeypatch.setattr(api_module, "PostgresChunkStore", Store)

    async def connect(_):
        return FakeTemporalClient()
    app = create_app(settings=Settings(), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/v1/indexings/{WORKFLOW_ID}/chunks")
    assert response.status_code == 200, response.text
    assert [c["chunk_id"] for c in response.json()["chunks"]] == ["book-main:chunk:1"]
    assert response.json()["chunk_count"] == 1
    assert queries == [("b2c-demo", {"book-main"})]
