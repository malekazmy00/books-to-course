"""Durable per-unit translation results for the rebuilt translation path (D-028 part D).

Audit 08 §2.2 found the old path's real state in overwritable JSON under ``_system/``. Here
every unit result is one row in Postgres, written as soon as the unit is decided, keyed by
``(run_id, unit_id)``:

* ``translation_unit_results`` — the current ``UnitTranslation`` of each unit plus the
  drafts that led to it (levers, issues, QE). The upsert's ``WHERE`` guard makes a human
  decision final with respect to policy results: only another human decision replaces it.
* ``translation_runs`` — one row per run: the start input, the units artifact once
  prepared, and the most recent assembly (an older snapshot never replaces a newer one).

The schema is created once per instance, in its own transaction (same pattern as
``b2c_translation.memory`` and ``b2c_api/srs.py``).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from b2c_contracts import UnitTranslation

DECISIONS = ("accepted", "needs_review", "rejected", "dropped")
# A unit with one of these decisions (or any human decision) is not translated again.
FINAL_DECISIONS = ("accepted", "dropped", "rejected")
RUN_ID = re.compile(r"[0-9a-f]{64}")
_UNIT_SEQ = re.compile(r":u(\d{1,9})$")

_DDL = (
    """CREATE TABLE IF NOT EXISTS translation_unit_results (
        run_id text NOT NULL,
        unit_id text NOT NULL,
        book_id text NOT NULL,
        seq integer NOT NULL,
        decision text NOT NULL CHECK (decision IN ('accepted', 'needs_review', 'rejected', 'dropped')),
        origin text NOT NULL CHECK (origin IN ('engine', 'rule', 'memory', 'human')),
        decided_by text NOT NULL CHECK (decided_by IN ('policy', 'human')),
        translation_json jsonb NOT NULL,
        drafts_json jsonb NOT NULL,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (run_id, unit_id)
    )""",
    """CREATE INDEX IF NOT EXISTS translation_unit_results_decision
        ON translation_unit_results (run_id, decision, seq)""",
    """CREATE TABLE IF NOT EXISTS translation_runs (
        run_id text PRIMARY KEY,
        course_id text NOT NULL,
        workflow_id text NOT NULL,
        book_id text,
        input_json jsonb NOT NULL,
        units_ref_json jsonb,
        unit_count integer,
        assembly_json jsonb,
        assembly_snapshot_at timestamptz,
        created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL
    )""",
)

_UPSERT = """INSERT INTO translation_unit_results
    (run_id, unit_id, book_id, seq, decision, origin, decided_by, translation_json, drafts_json, updated_at,
     termbase_digest)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
    ON CONFLICT (run_id, unit_id) DO UPDATE SET
        book_id = EXCLUDED.book_id, seq = EXCLUDED.seq, decision = EXCLUDED.decision,
        origin = EXCLUDED.origin, decided_by = EXCLUDED.decided_by,
        translation_json = EXCLUDED.translation_json, drafts_json = EXCLUDED.drafts_json,
        updated_at = EXCLUDED.updated_at, termbase_digest = EXCLUDED.termbase_digest
    WHERE translation_unit_results.decided_by <> 'human' OR EXCLUDED.decided_by = 'human'"""

_COLUMNS = "run_id, unit_id, book_id, seq, translation_json, drafts_json, updated_at"
_SELECT_ONE = f"SELECT {_COLUMNS} FROM translation_unit_results WHERE run_id = %s AND unit_id = %s"
_SELECT_RUN = f"SELECT {_COLUMNS} FROM translation_unit_results WHERE run_id = %s ORDER BY seq"
_SELECT_MANY = (f"SELECT {_COLUMNS} FROM translation_unit_results WHERE run_id = %s AND unit_id = ANY(%s) "
                "ORDER BY seq")
_SELECT_BY_DECISION = (f"SELECT {_COLUMNS} FROM translation_unit_results WHERE run_id = %s "
                       "AND decision = ANY(%s) ORDER BY seq LIMIT %s OFFSET %s")
_COUNT_BY_DECISION = "SELECT count(*) FROM translation_unit_results WHERE run_id = %s AND decision = ANY(%s)"
_COUNTS = "SELECT decision, count(*) FROM translation_unit_results WHERE run_id = %s GROUP BY decision"
# A policy result only counts as final for the approved termbase it was produced with: when
# the locked terms change, the unit is translated again (human decisions always stay).
_FINAL = """SELECT unit_id FROM translation_unit_results WHERE run_id = %s AND unit_id = ANY(%s)
    AND (decided_by = 'human' OR (decision IN ('accepted', 'dropped', 'rejected') AND termbase_digest = %s))"""
_REVISION_ROWS = """SELECT unit_id, decision, decided_by, updated_at FROM translation_unit_results
    WHERE run_id = %s ORDER BY unit_id"""

_RUN_REGISTER = """INSERT INTO translation_runs (run_id, course_id, workflow_id, input_json, created_at, updated_at)
    VALUES (%s, %s, %s, %s::jsonb, %s, %s) ON CONFLICT (run_id) DO NOTHING"""
_RUN_SET_UNITS = """UPDATE translation_runs SET units_ref_json = %s::jsonb, unit_count = %s, book_id = %s,
    updated_at = %s WHERE run_id = %s"""
_RUN_SET_ASSEMBLY = """UPDATE translation_runs SET assembly_json = %s::jsonb, assembly_snapshot_at = %s,
    updated_at = %s WHERE run_id = %s AND (assembly_snapshot_at IS NULL OR assembly_snapshot_at <= %s)"""
_RUN_GET = """SELECT run_id, course_id, workflow_id, book_id, input_json, units_ref_json, unit_count,
    assembly_json, created_at, updated_at FROM translation_runs WHERE run_id = %s"""


def unit_seq(unit_id: str) -> int:
    """Sequence number of a contract unit id (``f"{book_id}:u{seq:05d}"``)."""
    match = _UNIT_SEQ.search(unit_id)
    if match is None:
        raise ValueError(f"not a translation unit id: {unit_id!r}")
    return int(match.group(1))


def _run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        raise ValueError("run_id must be 64 lowercase hex characters")
    return run_id


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class StoredUnitResult:
    run_id: str
    unit_id: str
    book_id: str
    seq: int
    translation: UnitTranslation
    drafts: list[dict[str, Any]]
    updated_at: datetime

    @property
    def decision(self) -> str:
        return self.translation.decision

    @property
    def origin(self) -> str:
        return self.translation.origin


@dataclass(frozen=True)
class TranslationRun:
    run_id: str
    course_id: str
    workflow_id: str
    book_id: str | None
    input_json: dict[str, Any]
    units_ref: dict[str, Any] | None
    unit_count: int | None
    assembly: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class TranslationResultStore:
    def __init__(self, dsn: str, *, connect: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.dsn = dsn
        self._connect_fn = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ------------------------------------------------------------------ plumbing
    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn()
        import psycopg
        return psycopg.connect(self.dsn)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as conn:
                for statement in _DDL:
                    conn.execute(statement)
            self._schema_ready = True

    def _rows(self, sql: str, params: Sequence[Any]) -> list[tuple]:
        self.ensure_schema()
        with self._connect() as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    def _write(self, sql: str, params: Sequence[Any]) -> int:
        self.ensure_schema()
        with self._connect() as conn:
            return conn.execute(sql, tuple(params)).rowcount

    @staticmethod
    def _result(row: tuple) -> StoredUnitResult:
        run_id, unit_id, book_id, seq, translation, drafts, updated_at = row
        return StoredUnitResult(run_id=run_id, unit_id=unit_id, book_id=book_id, seq=int(seq),
                                translation=UnitTranslation.model_validate(_json(translation)),
                                drafts=list(_json(drafts) or []), updated_at=_utc(updated_at))

    # ------------------------------------------------------------------ unit results
    def upsert(self, run_id: str, book_id: str, translation: UnitTranslation,
               drafts: Iterable[dict[str, Any]] = (), *, termbase_digest: str = "") -> bool:
        """Insert or replace one unit result. Returns False when a human result was kept.

        ``termbase_digest`` names the approved termbase the result was produced with."""
        _run_id(run_id)
        if not translation.unit_id.startswith(f"{book_id}:"):
            raise ValueError(f"unit {translation.unit_id!r} does not belong to book {book_id!r}")
        payload = json.dumps(translation.model_dump(mode="json"), ensure_ascii=False)
        drafts_payload = json.dumps(list(drafts), ensure_ascii=False)
        written = self._write(_UPSERT, (
            run_id, translation.unit_id, book_id, unit_seq(translation.unit_id), translation.decision,
            translation.origin, translation.decided_by, payload, drafts_payload, self._clock(), termbase_digest))
        return written > 0

    def fetch(self, run_id: str, unit_id: str) -> StoredUnitResult | None:
        rows = self._rows(_SELECT_ONE, (_run_id(run_id), unit_id))
        return self._result(rows[0]) if rows else None

    def fetch_many(self, run_id: str, unit_ids: Sequence[str]) -> dict[str, StoredUnitResult]:
        if not unit_ids:
            return {}
        return {r.unit_id: r for r in map(self._result, self._rows(_SELECT_MANY, (_run_id(run_id), list(unit_ids))))}

    def fetch_run(self, run_id: str) -> list[StoredUnitResult]:
        return [self._result(r) for r in self._rows(_SELECT_RUN, (_run_id(run_id),))]

    def list_by_decision(self, run_id: str, decisions: Sequence[str], *, limit: int = 50,
                         offset: int = 0) -> list[StoredUnitResult]:
        decisions = self._decisions(decisions)
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset >= 0")
        rows = self._rows(_SELECT_BY_DECISION, (_run_id(run_id), decisions, limit, offset))
        return [self._result(r) for r in rows]

    def count_by_decision(self, run_id: str, decisions: Sequence[str]) -> int:
        rows = self._rows(_COUNT_BY_DECISION, (_run_id(run_id), self._decisions(decisions)))
        return int(rows[0][0]) if rows else 0

    def counts(self, run_id: str) -> dict[str, int]:
        return {decision: int(n) for decision, n in self._rows(_COUNTS, (_run_id(run_id),))}

    def final_unit_ids(self, run_id: str, unit_ids: Sequence[str], *, termbase_digest: str = "") -> set[str]:
        """Units not to translate again: human decisions, and final policy results produced
        with the same approved termbase."""
        if not unit_ids:
            return set()
        return {r[0] for r in self._rows(_FINAL, (_run_id(run_id), list(unit_ids), termbase_digest))}

    def revision(self, run_id: str) -> str:
        """Digest of the run's current results (changes whenever any unit result changes)."""
        digest = hashlib.sha256()
        for unit_id, decision, decided_by, updated_at in self._rows(_REVISION_ROWS, (_run_id(run_id),)):
            digest.update(f"{unit_id}\0{decision}\0{decided_by}\0{_utc(updated_at).isoformat()}\n".encode())
        return digest.hexdigest()

    @staticmethod
    def _decisions(decisions: Sequence[str]) -> list[str]:
        out = list(dict.fromkeys(decisions))
        if not out or any(d not in DECISIONS for d in out):
            raise ValueError(f"decisions must be a non-empty subset of {DECISIONS}")
        return out

    # ------------------------------------------------------------------ runs
    def register_run(self, run_id: str, *, course_id: str, workflow_id: str,
                     input_json: dict[str, Any]) -> bool:
        now = self._clock()
        return self._write(_RUN_REGISTER, (_run_id(run_id), course_id, workflow_id,
                                           json.dumps(input_json, ensure_ascii=False), now, now)) > 0

    def set_units(self, run_id: str, *, units_ref: dict[str, Any], unit_count: int, book_id: str) -> bool:
        return self._write(_RUN_SET_UNITS, (json.dumps(units_ref), unit_count, book_id, self._clock(),
                                            _run_id(run_id))) > 0

    def set_assembly(self, run_id: str, assembly: dict[str, Any], *, snapshot_at: datetime) -> bool:
        """Record the latest assembly; refused when a newer snapshot is already recorded."""
        return self._write(_RUN_SET_ASSEMBLY, (json.dumps(assembly, ensure_ascii=False), snapshot_at,
                                               self._clock(), _run_id(run_id), snapshot_at)) > 0

    def get_run(self, run_id: str) -> TranslationRun | None:
        rows = self._rows(_RUN_GET, (_run_id(run_id),))
        if not rows:
            return None
        (rid, course_id, workflow_id, book_id, inp, units_ref, unit_count, assembly, created, updated) = rows[0]
        return TranslationRun(run_id=rid, course_id=course_id, workflow_id=workflow_id, book_id=book_id,
                              input_json=_json(inp), units_ref=_json(units_ref),
                              unit_count=None if unit_count is None else int(unit_count),
                              assembly=_json(assembly), created_at=_utc(created), updated_at=_utc(updated))


__all__ = ["DECISIONS", "FINAL_DECISIONS", "RUN_ID", "StoredUnitResult", "TranslationResultStore",
           "TranslationRun", "unit_seq"]
