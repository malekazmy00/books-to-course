"""Durable, provider-neutral storage for already-built structured chunks.

This adapter stores chunk payloads and optional caller-produced vectors. It does
not select or call an embedding model and deliberately does not create pgvector
indexes: vector dimensions and retrieval strategy are evaluation decisions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from typing import Any

from b2c_contracts.models import StructuredChunk

ConnectionFactory = Callable[[], Any]

_DDL = """
CREATE TABLE IF NOT EXISTS b2c_indexed_chunks (
    course_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('spine', 'supplementary')),
    chunk_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    embedding JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (course_id, book_id, role, chunk_id)
)
"""
_INDEX = """CREATE INDEX IF NOT EXISTS b2c_indexed_chunks_scope_idx
ON b2c_indexed_chunks (course_id, role, book_id)"""


class PostgresChunkStore:
    """Persist chunks under explicit course/book/role scope.

     is injected so callers own credentials, pooling, and lifecycle.
    Each public mutation uses one database transaction. Repeating an upsert is
    idempotent;  atomically replaces all roles for one course/book
    and is the intended full reindex operation.
    """

    def __init__(self, connect: ConnectionFactory):
        self._connect = connect

    def ensure_schema(self) -> None:
        """Create this adapter's table and scope index if absent."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
                cur.execute(_INDEX)

    def upsert_batch(self, course_id: str, chunks: Sequence[StructuredChunk], *,
                     embeddings: dict[str, Sequence[float]] | None = None) -> int:
        """Insert/update a batch; existing rows outside the batch are retained."""
        if not course_id.strip():
            raise ValueError("course_id must be non-empty")
        _validate_batch(chunks, embeddings)
        rows = _rows(course_id, chunks, embeddings)
        if not rows:
            return 0
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(_UPSERT, rows)
        return len(rows)

    def replace_book(self, course_id: str, book_id: str, chunks: Sequence[StructuredChunk], *,
                     embeddings: dict[str, Sequence[float]] | None = None) -> int:
        """Atomically replace a book's indexed state, removing stale chunks."""
        if not course_id.strip() or not book_id.strip():
            raise ValueError("course_id and book_id must be non-empty")
        if any(chunk.book_id != book_id for chunk in chunks):
            raise ValueError("replace_book accepts chunks for exactly the requested book")
        _validate_batch(chunks, embeddings)
        rows = _rows(course_id, chunks, embeddings)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM b2c_indexed_chunks WHERE course_id = %s AND book_id = %s",
                            (course_id, book_id))
                if rows:
                    cur.executemany(_UPSERT, rows)
        return len(rows)

    def delete_book(self, course_id: str, book_id: str, *, role: str | None = None) -> int:
        """Delete one book in one course, optionally limiting deletion by role."""
        if not course_id.strip() or not book_id.strip():
            raise ValueError("course_id and book_id must be non-empty")
        if role not in (None, "spine", "supplementary"):
            raise ValueError("role must be spine, supplementary, or None")
        sql = "DELETE FROM b2c_indexed_chunks WHERE course_id = %s AND book_id = %s"
        params: tuple[str, ...] = (course_id, book_id)
        if role is not None:
            sql += " AND role = %s"
            params += (role,)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return max(0, cur.rowcount)

    def list_chunks(self, course_id: str, *, book_ids: set[str] | None = None,
                    roles: set[str] | None = None) -> list[StructuredChunk]:
        """Read chunks only within an explicit course and optional scope filters."""
        if not course_id.strip():
            raise ValueError("course_id must be non-empty")
        if roles is not None and not roles <= {"spine", "supplementary"}:
            raise ValueError("roles may contain only spine and supplementary")
        clauses = ["course_id = %s"]
        params: list[Any] = [course_id]
        if book_ids is not None:
            if not book_ids:
                return []
            clauses.append("book_id = ANY(%s)")
            params.append(sorted(book_ids))
        if roles is not None:
            if not roles:
                return []
            clauses.append("role = ANY(%s)")
            params.append(sorted(roles))
        sql = "SELECT payload FROM b2c_indexed_chunks WHERE " + " AND ".join(clauses)
        sql += " ORDER BY book_id, role, chunk_id"
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                values = [row[0] for row in cur.fetchall()]
        return [StructuredChunk.model_validate(value) for value in values]


_UPSERT = """INSERT INTO b2c_indexed_chunks
(course_id, book_id, role, chunk_id, payload, embedding)
VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
ON CONFLICT (course_id, book_id, role, chunk_id) DO UPDATE SET
    payload = EXCLUDED.payload, embedding = COALESCE(EXCLUDED.embedding, b2c_indexed_chunks.embedding), updated_at = now()"""


def _rows(course_id: str, chunks: Sequence[StructuredChunk],
          embeddings: dict[str, Sequence[float]] | None) -> list[tuple[Any, ...]]:
    return [
        (course_id, chunk.book_id, chunk.role, chunk.chunk_id,
         json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False),
         json.dumps(list(embeddings[chunk.chunk_id])) if embeddings and chunk.chunk_id in embeddings else None)
        for chunk in chunks
    ]


def _validate_batch(chunks: Sequence[StructuredChunk],
                    embeddings: dict[str, Sequence[float]] | None) -> None:
    ids = [chunk.chunk_id for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("batch contains duplicate chunk_id values")
    if embeddings is not None and not set(embeddings) <= set(ids):
        raise ValueError("embedding map contains chunk IDs outside the batch")
    for chunk_id, vector in (embeddings or {}).items():
        if not vector or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise ValueError(f"embedding for {chunk_id} must be a non-empty numeric vector")
