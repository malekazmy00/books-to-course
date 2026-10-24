"""Durable learner SRS persistence for Phase 9."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from b2c_contracts import ReviewLogEntry
from b2c_pedagogy.srs import review_card


_SRS_DDL = (
    """CREATE TABLE IF NOT EXISTS learner_srs_cards (
        user_id text NOT NULL,
        course_id text NOT NULL,
        question_id text NOT NULL,
        card_json text NOT NULL,
        due_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (user_id, course_id, question_id)
    )""",
    """CREATE TABLE IF NOT EXISTS learner_review_history (
        review_id bigserial PRIMARY KEY,
        user_id text NOT NULL,
        course_id text NOT NULL,
        question_id text NOT NULL,
        card_id text NOT NULL,
        rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 4),
        reviewed_at timestamptz NOT NULL,
        due_at timestamptz NOT NULL,
        entry_json jsonb NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS learner_srs_due_idx
       ON learner_srs_cards (user_id, course_id, due_at)""",
    """CREATE INDEX IF NOT EXISTS learner_review_history_user_idx
       ON learner_review_history (user_id, course_id, reviewed_at DESC)""",
)


class PostgresSRSStore:
    """Serializes each learner/question update and stores FSRS state plus ReviewLogEntry."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        """Create the tables once per store, in a transaction of their own.

        The flag is set only after that transaction commits, so a rolled-back
        request transaction can never leave the store believing DDL ran.
        """
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as conn:
                for statement in _SRS_DDL:
                    conn.execute(statement)
            self._schema_ready = True

    def review(
        self, *, user_id: str, course_id: str, question_id: str,
        rating: int, reviewed_at: datetime,
    ) -> tuple[ReviewLogEntry, datetime]:
        if reviewed_at.tzinfo is None or reviewed_at.utcoffset() != timezone.utc.utcoffset(reviewed_at):
            raise ValueError("reviewed_at must be timezone-aware UTC")
        self._ensure_schema()
        with self._connect() as conn:
            lock_name = json.dumps([user_id, course_id, question_id], ensure_ascii=True, separators=(",", ":"))
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_name,))
            row = conn.execute(
                "SELECT card_json FROM learner_srs_cards "
                "WHERE user_id=%s AND course_id=%s AND question_id=%s FOR UPDATE",
                (user_id, course_id, question_id),
            ).fetchone()
            card_json, entry, due_at = review_card(
                user_id=user_id, rating=rating, reviewed_at=reviewed_at,
                card_json=row[0] if row else None,
            )
            conn.execute(
                "INSERT INTO learner_srs_cards "
                "(user_id,course_id,question_id,card_json,due_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (user_id,course_id,question_id) DO UPDATE SET "
                "card_json=EXCLUDED.card_json,due_at=EXCLUDED.due_at,updated_at=EXCLUDED.updated_at",
                (user_id, course_id, question_id, card_json, due_at, reviewed_at),
            )
            conn.execute(
                "INSERT INTO learner_review_history "
                "(user_id,course_id,question_id,card_id,rating,reviewed_at,due_at,entry_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (user_id, course_id, question_id, entry.card_id, rating, reviewed_at, due_at,
                 entry.model_dump_json()),
            )
            return entry, due_at

    def due(self, *, user_id: str, course_id: str, now: datetime, limit: int = 50) -> list[dict[str, Any]]:
        if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
            raise ValueError("now must be timezone-aware UTC")
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT question_id,due_at FROM learner_srs_cards "
                "WHERE user_id=%s AND course_id=%s AND due_at<=%s "
                "ORDER BY due_at,question_id LIMIT %s",
                (user_id, course_id, now, limit),
            ).fetchall()
        return [{"question_id": row[0], "due_at": row[1].astimezone(timezone.utc)} for row in rows]

    def history(self, *, user_id: str, course_id: str, limit: int = 100) -> list[ReviewLogEntry]:
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT entry_json FROM learner_review_history "
                "WHERE user_id=%s AND course_id=%s ORDER BY reviewed_at DESC,review_id DESC LIMIT %s",
                (user_id, course_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            value = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            result.append(ReviewLogEntry.model_validate(value))
        return result

