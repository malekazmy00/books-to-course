import json

import pytest
from b2c_contracts.models import StructuredChunk
from b2c_indexing.postgres_store import PostgresChunkStore


def chunk(chunk_id="book-a:chunk:1", book_id="book-a", role="spine", text="evidence"):
    return StructuredChunk(chunk_id=chunk_id, book_id=book_id, role=role,
        section_path=["unit 1"], block_ids=[f"{book_id}:1:1"], pages=[1],
        text=text, token_count=2, embedding_model="unassigned")


class RecordingCursor:
    def __init__(self, conn): self.conn = conn; self.rowcount = 2
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=None): self.conn.calls.append((sql, params))
    def executemany(self, sql, rows): self.conn.calls.append((sql, list(rows)))
    def fetchall(self): return self.conn.rows


class RecordingConnection:
    def __init__(self): self.calls = []; self.rows = []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return RecordingCursor(self)


def store_with_recording_conn():
    conn = RecordingConnection()
    return PostgresChunkStore(lambda: conn), conn


def test_upsert_serializes_payload_and_uses_idempotent_conflict_update():
    store, conn = store_with_recording_conn()
    item = chunk()
    assert store.upsert_batch("course-1", [item], embeddings={item.chunk_id: [0.2, 0.8]}) == 1
    sql, rows = conn.calls[0]
    assert "ON CONFLICT (course_id, book_id, role, chunk_id) DO UPDATE" in sql
    row = rows[0]
    assert row[:4] == ("course-1", "book-a", "spine", item.chunk_id)
    assert json.loads(row[4])["text"] == "evidence"
    assert json.loads(row[5]) == [0.2, 0.8]


def test_replace_book_deletes_only_same_course_and_book_before_insert():
    store, conn = store_with_recording_conn()
    store.replace_book("course-1", "book-a", [chunk()])
    assert "WHERE course_id = %s AND book_id = %s" in conn.calls[0][0]
    assert conn.calls[0][1] == ("course-1", "book-a")
    assert conn.calls[1][1][0][:2] == ("course-1", "book-a")


def test_rejects_cross_book_reindex_and_duplicate_ids():
    store, _ = store_with_recording_conn()
    with pytest.raises(ValueError, match="exactly the requested book"):
        store.replace_book("c", "book-a", [chunk(book_id="book-b")])
    with pytest.raises(ValueError, match="duplicate"):
        store.upsert_batch("c", [chunk(), chunk()])


def test_delete_role_is_scoped_and_invalid_role_is_rejected():
    store, conn = store_with_recording_conn()
    assert store.delete_book("c", "book-a", role="supplementary") == 2
    assert "course_id = %s AND book_id = %s AND role = %s" in conn.calls[0][0]
    assert conn.calls[0][1] == ("c", "book-a", "supplementary")
    with pytest.raises(ValueError, match="role"):
        store.delete_book("c", "book-a", role="admin")


def test_list_requires_course_and_applies_role_and_book_filters():
    store, conn = store_with_recording_conn()
    conn.rows = [(chunk().model_dump(mode="json"),)]
    assert store.list_chunks("c", book_ids={"book-a"}, roles={"spine"}) == [chunk()]
    sql, params = conn.calls[0]
    assert "course_id = %s" in sql and "book_id = ANY(%s)" in sql and "role = ANY(%s)" in sql
    assert params == ("c", ["book-a"], ["spine"])
    assert store.list_chunks("c", roles=set()) == []
    assert len(conn.calls) == 1
