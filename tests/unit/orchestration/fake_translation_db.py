"""In-memory stand-in for the two Postgres tables of ``translation_store`` (D-028 part D).

It dispatches on the module's own SQL constants, so any statement the store issues that this
fake does not know fails loudly instead of being silently ignored.
"""
from __future__ import annotations

import json
from datetime import datetime

import b2c_orchestration.translation_store as ts


class FakeCursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeTranslationDB:
    def __init__(self):
        self.results: dict[tuple[str, str], dict] = {}
        self.runs: dict[str, dict] = {}
        self.ddl = 0
        self.connections = 0
        self.statements: list[str] = []

    def connect(self):
        self.connections += 1
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeTranslationDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):  # noqa: C901 - one branch per statement
        db = self.db
        db.statements.append(sql)
        if sql in ts._DDL:
            db.ddl += 1
            return FakeCursor()
        if sql == ts._UPSERT:
            (run_id, unit_id, book_id, seq, decision, origin, decided_by, translation, drafts, at) = params
            old = db.results.get((run_id, unit_id))
            if old is not None and old["decided_by"] == "human" and decided_by != "human":
                return FakeCursor(rowcount=0)  # the ON CONFLICT ... WHERE guard
            db.results[(run_id, unit_id)] = dict(
                run_id=run_id, unit_id=unit_id, book_id=book_id, seq=seq, decision=decision, origin=origin,
                decided_by=decided_by, translation_json=json.loads(translation), drafts_json=json.loads(drafts),
                updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == ts._SELECT_ONE:
            run_id, unit_id = params
            row = db.results.get((run_id, unit_id))
            return FakeCursor([self._row(row)] if row else [])
        if sql == ts._SELECT_RUN:
            (run_id,) = params
            return FakeCursor([self._row(r) for r in self._sorted(run_id)])
        if sql == ts._SELECT_MANY:
            run_id, ids = params
            return FakeCursor([self._row(r) for r in self._sorted(run_id) if r["unit_id"] in ids])
        if sql == ts._SELECT_BY_DECISION:
            run_id, decisions, limit, offset = params
            rows = [r for r in self._sorted(run_id) if r["decision"] in decisions]
            return FakeCursor([self._row(r) for r in rows[offset:offset + limit]])
        if sql == ts._COUNT_BY_DECISION:
            run_id, decisions = params
            return FakeCursor([(sum(1 for r in self._sorted(run_id) if r["decision"] in decisions),)])
        if sql == ts._COUNTS:
            (run_id,) = params
            counts: dict[str, int] = {}
            for r in self._sorted(run_id):
                counts[r["decision"]] = counts.get(r["decision"], 0) + 1
            return FakeCursor(sorted(counts.items()))
        if sql == ts._FINAL:
            run_id, ids = params
            return FakeCursor([(r["unit_id"],) for r in self._sorted(run_id) if r["unit_id"] in ids
                               and (r["decision"] in ts.FINAL_DECISIONS or r["decided_by"] == "human")])
        if sql == ts._REVISION_ROWS:
            (run_id,) = params
            return FakeCursor([(r["unit_id"], r["decision"], r["decided_by"], r["updated_at"])
                               for r in sorted(self._sorted(run_id), key=lambda r: r["unit_id"])])
        if sql == ts._RUN_REGISTER:
            run_id, course_id, workflow_id, inp, at, _at = params
            if run_id in db.runs:
                return FakeCursor(rowcount=0)
            db.runs[run_id] = dict(run_id=run_id, course_id=course_id, workflow_id=workflow_id,
                                   input_json=json.loads(inp), units_ref_json=None, unit_count=None,
                                   book_id=None, assembly_json=None, assembly_snapshot_at=None,
                                   created_at=at, updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == ts._RUN_SET_UNITS:
            ref, count, book_id, at, run_id = params
            run = db.runs.get(run_id)
            if run is None:
                return FakeCursor(rowcount=0)
            run.update(units_ref_json=json.loads(ref), unit_count=count, book_id=book_id, updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == ts._RUN_SET_ASSEMBLY:
            assembly, snapshot, at, run_id, snapshot_guard = params
            run = db.runs.get(run_id)
            if run is None or (run["assembly_snapshot_at"] is not None
                               and run["assembly_snapshot_at"] > snapshot_guard):
                return FakeCursor(rowcount=0)
            run.update(assembly_json=json.loads(assembly), assembly_snapshot_at=snapshot, updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == ts._RUN_GET:
            (run_id,) = params
            r = db.runs.get(run_id)
            if r is None:
                return FakeCursor()
            return FakeCursor([(r["run_id"], r["course_id"], r["workflow_id"], r["book_id"], r["input_json"],
                                r["units_ref_json"], r["unit_count"], r["assembly_json"], r["created_at"],
                                r["updated_at"])])
        raise AssertionError(f"unexpected SQL: {' '.join(sql.split())[:120]}")

    def _sorted(self, run_id):
        return sorted((r for (rid, _), r in self.db.results.items() if rid == run_id), key=lambda r: r["seq"])

    @staticmethod
    def _row(r):
        at: datetime = r["updated_at"]
        return (r["run_id"], r["unit_id"], r["book_id"], r["seq"], r["translation_json"], r["drafts_json"], at)
