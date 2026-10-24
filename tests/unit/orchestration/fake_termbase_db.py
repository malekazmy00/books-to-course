"""In-memory stand-in for the two Postgres tables of ``termbase_store`` (D-028 principle 5).

Like ``fake_translation_db``, it dispatches on the module's own SQL constants, so a statement
the store issues that this fake does not know fails loudly.
"""
from __future__ import annotations

import json

import b2c_orchestration.termbase_store as tbs
from tests.unit.orchestration.fake_translation_db import FakeCursor


class FakeTermbaseDB:
    def __init__(self):
        self.entries: dict[tuple, dict] = {}
        self.builds: dict[tuple, dict] = {}
        self.ddl = 0
        self.statements: list[str] = []

    def connect(self):
        return FakeTermbaseConn(self)


class FakeTermbaseConn:
    def __init__(self, db: FakeTermbaseDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):  # noqa: C901 - one branch per statement
        db = self.db
        db.statements.append(sql)
        if sql in tbs._DDL:
            db.ddl += 1
            return FakeCursor()
        if sql == tbs._UPSERT_EXTRACTED:
            (course, book, sl, tl, key, source, kind, freq, candidate, proposals, extracted_at, at) = params
            pk = (course, book, sl, tl, key)
            row = db.entries.get(pk)
            if row is None:
                row = dict(course_id=course, book_id=book, source_language=sl, target_language=tl, source_key=key,
                           status="pending", target=None, forbidden_variants_json=[], allow_shared_target=False,
                           do_not_translate=False, reviewer=None, decided_at=None, rejection_reason=None)
                db.entries[pk] = row
            row.update(source=source, kind=kind, frequency=freq, candidate_json=json.loads(candidate),
                       proposals_json=json.loads(proposals), present=True, extracted_at=extracted_at, updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == tbs._MARK_ABSENT:
            at, course, book, sl, tl, keys = params
            n = 0
            for row in self._scope(course, book, sl, tl):
                if row["present"] and row["source_key"] not in keys:
                    row.update(present=False, updated_at=at)
                    n += 1
            return FakeCursor(rowcount=n)
        if sql == tbs._SELECT_SCOPE:
            return FakeCursor([self._row(r) for r in sorted(self._scope(*params), key=lambda r: r["source_key"])])
        if sql == tbs._SELECT_BOOK_APPROVED:
            book, sl, tl = params
            rows = [r for r in db.entries.values() if r["book_id"] == book and r["source_language"] == sl
                    and r["target_language"] == tl and r["status"] == "approved"]
            return FakeCursor([self._row(r) for r in sorted(rows, key=lambda r: (r["course_id"], r["source_key"]))])
        if sql == tbs._DECIDE:
            (status, target, forbidden, allow_shared, dnt, reviewer, decided_at, reason, at,
             course, book, sl, tl, key) = params
            row = db.entries.get((course, book, sl, tl, key))
            if row is None:
                return FakeCursor(rowcount=0)
            row.update(status=status, target=target, forbidden_variants_json=json.loads(forbidden),
                       allow_shared_target=allow_shared, do_not_translate=dnt, reviewer=reviewer,
                       decided_at=decided_at, rejection_reason=reason, updated_at=at)
            return FakeCursor(rowcount=1)
        if sql == tbs._BUILD_UPSERT:
            course, book, sl, tl, ref, sha, count, at = params
            db.builds[(course, book, sl, tl)] = dict(ref=json.loads(ref), sha=sha, count=count, at=at)
            return FakeCursor(rowcount=1)
        if sql == tbs._BUILD_GET:
            b = db.builds.get(tuple(params))
            return FakeCursor([] if b is None else [(b["ref"], b["sha"], b["count"], b["at"])])
        raise AssertionError(f"unexpected SQL: {' '.join(sql.split())[:120]}")

    def _scope(self, course, book, sl, tl):
        return [r for r in self.db.entries.values() if (r["course_id"], r["book_id"], r["source_language"],
                                                         r["target_language"]) == (course, book, sl, tl)]

    @staticmethod
    def _row(r):
        return (r["course_id"], r["source_key"], r["source"], r["kind"], r["frequency"], r["candidate_json"],
                r["proposals_json"], r["present"], r["status"], r["target"], r["forbidden_variants_json"],
                r["allow_shared_target"], r["do_not_translate"], r["reviewer"], r["decided_at"],
                r["rejection_reason"], r["extracted_at"], r["updated_at"])
