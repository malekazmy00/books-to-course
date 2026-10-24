"""Translation memory in Postgres (D-028).

Exact match on a SHA-256 of the normalized source text (Unicode NFC, whitespace collapsed;
case and punctuation are significant) plus the language pair, optionally scoped to a book.
Only accepted engine output and human translations are stored. Human entries take
precedence and are never overwritten by engine output (enforced by the upsert's WHERE
guard); a later human correction replaces an earlier one.

Lookup returns a ``UnitTranslation`` with ``origin="memory"`` for the requesting unit:
a reused human decision is ``accepted``/``decided_by="human"``; a reused engine translation
is ``needs_review``/``decided_by="policy"`` so the pipeline re-runs QA on it.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from b2c_contracts import EngineIdentity, TranslationUnit, UnitTranslation

GLOBAL_SCOPE = ""

_DDL = (
    """CREATE TABLE IF NOT EXISTS translation_memory (
        source_hash text NOT NULL,
        source_language text NOT NULL,
        target_language text NOT NULL,
        scope text NOT NULL,
        origin text NOT NULL CHECK (origin IN ('engine', 'human')),
        source_text text NOT NULL,
        target_text text NOT NULL,
        engine jsonb,
        unit_id text NOT NULL,
        book_id text NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (source_hash, source_language, target_language, scope)
    )""",
)

_UPSERT = """INSERT INTO translation_memory
    (source_hash, source_language, target_language, scope, origin, source_text, target_text,
     engine, unit_id, book_id, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
    ON CONFLICT (source_hash, source_language, target_language, scope) DO UPDATE SET
        origin = EXCLUDED.origin, source_text = EXCLUDED.source_text,
        target_text = EXCLUDED.target_text, engine = EXCLUDED.engine,
        unit_id = EXCLUDED.unit_id, book_id = EXCLUDED.book_id, updated_at = EXCLUDED.updated_at
    WHERE translation_memory.origin <> 'human' OR EXCLUDED.origin = 'human'"""

_SELECT = """SELECT scope, origin, target_text, engine, updated_at FROM translation_memory
    WHERE source_hash = %s AND source_language = %s AND target_language = %s
      AND scope IN ('', %s)"""

_WS = re.compile(r"\s+")


def normalize_source(text: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip()


def source_hash(text: str) -> str:
    return hashlib.sha256(normalize_source(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryEntry:
    scope: str
    origin: str  # "engine" | "human"
    target_text: str
    engine: EngineIdentity | None
    updated_at: datetime


class TranslationMemory:
    def __init__(self, dsn: str, *, connect: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 source_language: str = "en", target_language: str = "ar") -> None:
        self.dsn = dsn
        self._connect_fn = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.source_language = source_language
        self.target_language = target_language
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        import psycopg
        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        """Create the table once per instance, in its own transaction (see b2c_api/srs.py)."""
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as conn:
                for statement in _DDL:
                    conn.execute(statement)
            self._schema_ready = True

    # ------------------------------------------------------------------ read
    def lookup_entry(self, unit: TranslationUnit, source_language: str,
                     target_language: str) -> MemoryEntry | None:
        self._ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(_SELECT, (source_hash(unit.source_text), source_language,
                                          target_language, unit.book_id)).fetchall()
        entries = []
        for scope, origin, target_text, engine, updated_at in rows:
            if isinstance(engine, str):
                engine = json.loads(engine)
            entries.append(MemoryEntry(scope=scope, origin=origin, target_text=target_text,
                                       engine=EngineIdentity.model_validate(engine) if engine else None,
                                       updated_at=updated_at.astimezone(timezone.utc)))
        if not entries:
            return None
        # human first; then the book's own scope; then the most recent
        return max(entries, key=lambda e: (e.origin == "human", e.scope != GLOBAL_SCOPE, e.updated_at))

    def lookup(self, unit: TranslationUnit, source_language: str,
               target_language: str) -> UnitTranslation | None:
        entry = self.lookup_entry(unit, source_language, target_language)
        if entry is None:
            return None
        human = entry.origin == "human"
        return UnitTranslation(unit_id=unit.unit_id, target_text=entry.target_text, origin="memory",
                               engine=entry.engine, decision="accepted" if human else "needs_review",
                               decided_by="human" if human else "policy", decided_at=entry.updated_at)

    # ------------------------------------------------------------------ write
    def store(self, unit: TranslationUnit, translation: UnitTranslation, *,
              source_language: str | None = None, target_language: str | None = None,
              book_scoped: bool = False) -> None:
        if translation.unit_id != unit.unit_id:
            raise ValueError("translation does not belong to this unit")
        if translation.origin not in ("engine", "human"):
            raise ValueError(f"only engine or human translations are stored, not {translation.origin!r}")
        if translation.decision != "accepted":
            raise ValueError("only accepted translations enter the translation memory")
        engine = (json.dumps(translation.engine.model_dump(mode="json"), ensure_ascii=False)
                  if translation.engine is not None else None)
        self._ensure_schema()
        with self._connect() as conn:
            conn.execute(_UPSERT, (
                source_hash(unit.source_text), source_language or self.source_language,
                target_language or self.target_language, unit.book_id if book_scoped else GLOBAL_SCOPE,
                translation.origin, unit.source_text, translation.target_text, engine,
                unit.unit_id, unit.book_id, self._clock()))
