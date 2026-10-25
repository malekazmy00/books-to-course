"""Review state of the per-book termbase (D-028 principle 5, Gate G3) in Postgres.

The termbase itself is built by ``b2c_translation.termbase`` and stored as a versioned
artifact (stage ``book-termbase``). What a human decides about each entry lives here, one row
per normalized source form, scoped by ``(course_id, book_id, source_language, target_language)``:

* ``book_termbase_entries`` — extraction-owned columns (``kind``, ``frequency``, candidate,
  proposals, ``present``) and human-owned columns (``status``, ``target``, ``reviewer``,
  ``decided_at`` …). Re-extraction upserts only the extraction-owned columns, so approvals
  and rejections survive it (merge by ``source_key``); entries the new extraction no longer
  finds are kept with ``present = false``.
* ``book_termbase_builds`` — the latest extraction artifact of each scope.

The schema is created once per instance (same pattern as ``translation_store``).
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate, TermRecord
from b2c_translation.termbase.model import source_key

STATUSES = ("pending", "approved", "rejected")

_DDL = (
    """CREATE TABLE IF NOT EXISTS book_termbase_entries (
        course_id text NOT NULL,
        book_id text NOT NULL,
        source_language text NOT NULL,
        target_language text NOT NULL,
        source_key text NOT NULL,
        source text NOT NULL,
        kind text NOT NULL,
        frequency integer NOT NULL,
        candidate_json jsonb NOT NULL,
        proposals_json jsonb NOT NULL,
        present boolean NOT NULL,
        extracted_at timestamptz NOT NULL,
        status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
        target text,
        forbidden_variants_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        allow_shared_target boolean NOT NULL DEFAULT false,
        do_not_translate boolean NOT NULL DEFAULT false,
        reviewer text,
        decided_at timestamptz,
        rejection_reason text,
        updated_at timestamptz NOT NULL,
        PRIMARY KEY (course_id, book_id, source_language, target_language, source_key)
    )""",
    """CREATE INDEX IF NOT EXISTS book_termbase_entries_book_status
        ON book_termbase_entries (book_id, source_language, target_language, status)""",
    """CREATE TABLE IF NOT EXISTS book_termbase_builds (
        course_id text NOT NULL,
        book_id text NOT NULL,
        source_language text NOT NULL,
        target_language text NOT NULL,
        artifact_ref_json jsonb NOT NULL,
        termbase_sha256 text NOT NULL,
        entry_count integer NOT NULL,
        built_at timestamptz NOT NULL,
        PRIMARY KEY (course_id, book_id, source_language, target_language)
    )""",
    # v3 (D-039): NULL = locked (every row decided before v3), 'hint' = preferred rendering only.
    """ALTER TABLE book_termbase_entries ADD COLUMN IF NOT EXISTS tier text CHECK (tier IN ('hint'))""",
    # D-047: accepted spellings of an approved name (empty for every row decided before).
    """ALTER TABLE book_termbase_entries
        ADD COLUMN IF NOT EXISTS accepted_variants_json jsonb NOT NULL DEFAULT '[]'::jsonb""",
)

_SCOPE = "course_id = %s AND book_id = %s AND source_language = %s AND target_language = %s"

# Extraction never writes a human-owned column (status, target, reviewer, ...).
_UPSERT_EXTRACTED = """INSERT INTO book_termbase_entries
    (course_id, book_id, source_language, target_language, source_key, source, kind, frequency,
     candidate_json, proposals_json, present, extracted_at, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, true, %s, %s)
    ON CONFLICT (course_id, book_id, source_language, target_language, source_key) DO UPDATE SET
        source = EXCLUDED.source, kind = EXCLUDED.kind, frequency = EXCLUDED.frequency,
        candidate_json = EXCLUDED.candidate_json, proposals_json = EXCLUDED.proposals_json,
        present = true, extracted_at = EXCLUDED.extracted_at, updated_at = EXCLUDED.updated_at"""
_MARK_ABSENT = f"""UPDATE book_termbase_entries SET present = false, updated_at = %s
    WHERE {_SCOPE} AND present AND NOT (source_key = ANY(%s))"""
_COLUMNS = """course_id, source_key, source, kind, frequency, candidate_json, proposals_json, present, status,
    target, forbidden_variants_json, allow_shared_target, do_not_translate, reviewer, decided_at,
    rejection_reason, extracted_at, updated_at, tier, accepted_variants_json"""
_SELECT_SCOPE = f"SELECT {_COLUMNS} FROM book_termbase_entries WHERE {_SCOPE} ORDER BY source_key"
_SELECT_BOOK_APPROVED = f"""SELECT {_COLUMNS} FROM book_termbase_entries
    WHERE book_id = %s AND source_language = %s AND target_language = %s AND status = 'approved'
    ORDER BY course_id, source_key"""
_DECIDE = f"""UPDATE book_termbase_entries SET status = %s, target = %s, forbidden_variants_json = %s::jsonb,
    allow_shared_target = %s, do_not_translate = %s, reviewer = %s, decided_at = %s, rejection_reason = %s,
    updated_at = %s, tier = %s, accepted_variants_json = %s::jsonb WHERE {_SCOPE} AND source_key = %s"""
_BUILD_UPSERT = """INSERT INTO book_termbase_builds
    (course_id, book_id, source_language, target_language, artifact_ref_json, termbase_sha256, entry_count,
     built_at) VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)
    ON CONFLICT (course_id, book_id, source_language, target_language) DO UPDATE SET
        artifact_ref_json = EXCLUDED.artifact_ref_json, termbase_sha256 = EXCLUDED.termbase_sha256,
        entry_count = EXCLUDED.entry_count, built_at = EXCLUDED.built_at"""
_BUILD_GET = f"""SELECT artifact_ref_json, termbase_sha256, entry_count, built_at FROM book_termbase_builds
    WHERE {_SCOPE}"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class TermbaseScope:
    course_id: str
    book_id: str
    source_language: str
    target_language: str

    def params(self) -> tuple[str, str, str, str]:
        return (self.course_id, self.book_id, self.source_language, self.target_language)


@dataclass(frozen=True)
class StoredTermEntry:
    course_id: str
    source_key: str
    source: str
    kind: str
    frequency: int
    candidate: TermCandidate
    proposals: list[Proposal]
    present: bool
    status: str
    target: str | None
    forbidden_variants: list[str]
    allow_shared_target: bool
    do_not_translate: bool
    reviewer: str | None
    decided_at: datetime | None
    rejection_reason: str | None
    extracted_at: datetime
    updated_at: datetime
    tier: str | None = None  # v3: None = locked, "hint"
    accepted_variants: list[str] = field(default_factory=list)  # D-047

    def record(self) -> TermRecord:
        approved = self.status == "approved"
        rejected = self.status == "rejected"
        return TermRecord(source=self.source, kind=self.kind, candidate=self.candidate, proposals=self.proposals,
                          status=self.status, target=self.target if approved else None,
                          do_not_translate=self.do_not_translate, forbidden_variants=list(self.forbidden_variants),
                          allow_shared_target=self.allow_shared_target,
                          approved_by=self.reviewer if approved else None,
                          approved_at=self.decided_at if approved else None,
                          rejected_by=self.reviewer if rejected else None,
                          rejected_at=self.decided_at if rejected else None,
                          rejection_reason=self.rejection_reason if rejected else None,
                          tier="hint" if approved and self.tier == "hint" else None,
                          accepted_variants=list(self.accepted_variants) if approved else [])

    def as_json(self) -> dict[str, Any]:
        return {"source": self.source, "source_key": self.source_key, "kind": self.kind,
                "frequency": self.frequency, "sections": self.candidate.sections, "present": self.present,
                "status": self.status, "tier": self.tier, "target": self.target, "reviewer": self.reviewer,
                "decided_at": None if self.decided_at is None else self.decided_at.isoformat(),
                "rejection_reason": self.rejection_reason, "allow_shared_target": self.allow_shared_target,
                "forbidden_variants": list(self.forbidden_variants),
                "accepted_variants": list(self.accepted_variants),
                "definition_context": self.candidate.definition_context,
                "expansion": self.candidate.expansion, "unit_ids": list(self.candidate.unit_ids),
                "signals": list(self.candidate.signals),
                "termhood": self.candidate.termhood, "termhood_reason": self.candidate.termhood_reason,
                "proposals": [p.model_dump(mode="json") for p in self.proposals],
                "updated_at": self.updated_at.isoformat()}


@dataclass(frozen=True)
class TermbaseBuildRecord:
    artifact_ref: dict[str, Any]
    termbase_sha256: str
    entry_count: int
    built_at: datetime


@dataclass(frozen=True)
class MergeOutcome:
    extracted: int
    absent: int


class TermbaseStore:
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
    def _entry(row: tuple) -> StoredTermEntry:
        (course_id, key, source, kind, frequency, candidate, proposals, present, status, target, forbidden,
         allow_shared, dnt, reviewer, decided_at, reason, extracted_at, updated_at, tier, accepted) = row
        return StoredTermEntry(
            course_id=course_id, source_key=key, source=source, kind=kind, frequency=int(frequency),
            candidate=TermCandidate.model_validate(_json(candidate)),
            proposals=[Proposal.model_validate(p) for p in _json(proposals) or []], present=bool(present),
            status=status, target=target, forbidden_variants=list(_json(forbidden) or []),
            allow_shared_target=bool(allow_shared), do_not_translate=bool(dnt), reviewer=reviewer,
            decided_at=_utc(decided_at), rejection_reason=reason, extracted_at=_utc(extracted_at),
            updated_at=_utc(updated_at), tier=tier, accepted_variants=list(_json(accepted) or []))

    # ------------------------------------------------------------------ extraction
    def merge_extraction(self, scope: TermbaseScope, termbase: BookTermbase) -> MergeOutcome:
        """Upsert the extraction-owned columns of every entry; keep every human decision."""
        if termbase.book_id != scope.book_id:
            raise ValueError("the termbase belongs to another book")
        now = self._clock()
        keys = []
        for record in termbase.entries:
            key = source_key(record.source)
            keys.append(key)
            self._write(_UPSERT_EXTRACTED, (
                *scope.params(), key, record.source, record.kind, record.candidate.frequency,
                json.dumps(record.candidate.model_dump(mode="json"), ensure_ascii=False),
                json.dumps([p.model_dump(mode="json") for p in record.proposals], ensure_ascii=False), now, now))
        absent = self._write(_MARK_ABSENT, (now, *scope.params(), keys))
        return MergeOutcome(extracted=len(keys), absent=absent)

    def set_build(self, scope: TermbaseScope, *, artifact_ref: dict[str, Any], termbase_sha256: str,
                  entry_count: int) -> None:
        self._write(_BUILD_UPSERT, (*scope.params(), json.dumps(artifact_ref), termbase_sha256, entry_count,
                                    self._clock()))

    def get_build(self, scope: TermbaseScope) -> TermbaseBuildRecord | None:
        rows = self._rows(_BUILD_GET, scope.params())
        if not rows:
            return None
        ref, sha, count, at = rows[0]
        return TermbaseBuildRecord(artifact_ref=_json(ref), termbase_sha256=sha, entry_count=int(count),
                                   built_at=_utc(at))

    # ------------------------------------------------------------------ reads
    def entries(self, scope: TermbaseScope) -> list[StoredTermEntry]:
        return [self._entry(r) for r in self._rows(_SELECT_SCOPE, scope.params())]

    def load(self, scope: TermbaseScope, *, extractor_version: str | None = None) -> BookTermbase:
        """The scope as a ``BookTermbase`` (all entries, with their human decisions)."""
        return BookTermbase(book_id=scope.book_id, source_language=scope.source_language,
                            target_language=scope.target_language, extractor_version=extractor_version,
                            entries=[e.record() for e in self.entries(scope)])

    def approved_for_book(self, book_id: str, source_language: str, target_language: str) -> list[StoredTermEntry]:
        """Approved entries of the same book in every course (the 'prior termbase' source)."""
        return [self._entry(r) for r in self._rows(_SELECT_BOOK_APPROVED,
                                                    (book_id, source_language, target_language))]

    # ------------------------------------------------------------------ human decisions
    def write_decisions(self, scope: TermbaseScope, records: Iterable[TermRecord]) -> int:
        """Persist the human-owned fields of records already validated by ``BookTermbase``."""
        written = 0
        now = self._clock()
        for r in records:
            if r.status == "approved":
                reviewer, at, reason = r.approved_by, r.approved_at, None
            elif r.status == "rejected":
                reviewer, at, reason = r.rejected_by, r.rejected_at, r.rejection_reason
            else:
                raise ValueError("only approved or rejected records are written")
            if not reviewer or at is None:
                raise ValueError("a decision needs a reviewer and a time")
            written += self._write(_DECIDE, (
                r.status, r.target if r.status == "approved" else None,
                json.dumps(list(r.forbidden_variants), ensure_ascii=False), r.allow_shared_target,
                r.do_not_translate, reviewer, at, reason, now, r.tier if r.status == "approved" else None,
                json.dumps(list(r.accepted_variants) if r.status == "approved" else [], ensure_ascii=False),
                *scope.params(), source_key(r.source)))
        return written


__all__ = ["MergeOutcome", "STATUSES", "StoredTermEntry", "TermbaseBuildRecord", "TermbaseScope",
           "TermbaseStore"]
