"""Where the operator API reads from: Postgres (read-only), the artifact store and Temporal.

The database also serves the running translation, so every query here is:

* on a read-only connection (``default_transaction_read_only=on``) with a ``statement_timeout``;
* scoped by an indexed key (``stage_outputs (course_id, ...)``, ``translation_unit_results
  (run_id, decision, seq)``, ``book_termbase_entries (book_id, ..., status)``), never a full-table read;
* aggregated in the database (``GROUP BY``) or bounded (``LIMIT``).

No schema is created or altered here (the stores' ``ensure_schema`` is deliberately not called).
The router caches every result for 15-30 s on top of this.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from b2c_contracts import ArtifactRef, GateResult, StageOutputRecord
from starlette.concurrency import run_in_threadpool

from b2c_api.deps import log

STATEMENT_TIMEOUT_MS = 5000
TEMPORAL_TIMEOUT_S = 3.0
MAX_OUTPUTS = 2000
MAX_GATES = 10000


@dataclass(frozen=True)
class RunCounts:
    run_id: str
    workflow_id: str
    book_id: str | None
    unit_count: int | None
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int]
    decided_last_hour: int
    units_ref: dict[str, Any] | None = None
    last_decided_at: datetime | None = None  # newest unit decision within the last hour


@dataclass(frozen=True)
class BookTermbase:
    pending: int
    approved: int
    rejected: int
    built_at: datetime | None
    entry_count: int | None
    last_decided_at: datetime | None


@dataclass(frozen=True)
class TermRejection:
    source: str
    reason: str
    decided_at: datetime | None


@dataclass(frozen=True)
class UnitRow:
    unit_id: str
    seq: int
    decision: str
    target_text: str | None
    issues: list[dict[str, Any]]
    updated_at: datetime


@dataclass(frozen=True)
class WorkflowState:
    workflow_id: str
    status: str  # Temporal status name
    started_at: datetime | None = None
    closed_at: datetime | None = None
    current_activity: str | None = None
    activity_attempt: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class OperatorReader:
    """Interface of the data the operator router needs (async; implementations may block in threads)."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def course_ids(self) -> list[str]: raise NotImplementedError
    async def books(self, course_id: str) -> list[dict[str, Any]]: raise NotImplementedError
    async def stage_outputs(self, course_id: str) -> list[StageOutputRecord]: raise NotImplementedError
    async def translation_runs(self, course_id: str) -> list[RunCounts]: raise NotImplementedError
    async def termbase(self, course_id: str, book_ids: Sequence[str]) -> BookTermbase | None:
        raise NotImplementedError
    async def termbase_rejections(self, course_id: str, book_ids: Sequence[str], limit: int) -> list[TermRejection]:
        raise NotImplementedError
    async def recent_units(self, run_id: str, limit: int) -> list[UnitRow]: raise NotImplementedError
    async def units_by_decision(self, run_id: str, decisions: Sequence[str], limit: int) -> list[UnitRow]:
        raise NotImplementedError
    async def unit_sources(self, course_id: str, units_ref: dict[str, Any] | None,
                           unit_ids: Sequence[str]) -> dict[str, str]: raise NotImplementedError
    async def workflow(self, workflow_id: str) -> WorkflowState | None: raise NotImplementedError
    async def episode_total(self, course_id: str, outline_record: StageOutputRecord) -> int | None:
        raise NotImplementedError


# ----------------------------------------------------------------------------- SQL
_OUTPUTS = """SELECT course_id, stage, key, uri, sha256, artifact_created_at, inputs_json, code_revision,
    config_json, required_gates, status, status_reason, status_changed_at, status_changed_by
    FROM stage_outputs WHERE course_id = %s ORDER BY artifact_created_at DESC LIMIT %s"""
_GATES = """SELECT stage, key, result_json FROM stage_output_gates WHERE course_id = %s
    ORDER BY decided_at, id LIMIT %s"""
_RUNS = """SELECT run_id, workflow_id, book_id, unit_count, created_at, updated_at, units_ref_json
    FROM translation_runs WHERE course_id = %s ORDER BY created_at DESC LIMIT 200"""
_RUN_COUNTS = """SELECT run_id, decision, count(*) FROM translation_unit_results WHERE run_id = ANY(%s)
    GROUP BY run_id, decision"""
_RUN_RECENT = """SELECT run_id, count(*), max(updated_at) FROM translation_unit_results WHERE run_id = ANY(%s)
    AND updated_at >= now() - interval '1 hour' GROUP BY run_id"""
_TERMBASE_COUNTS = """SELECT status, count(*), max(decided_at) FROM book_termbase_entries
    WHERE book_id = ANY(%s) AND course_id = %s AND present GROUP BY status"""
_TERMBASE_BUILD = """SELECT built_at, entry_count FROM book_termbase_builds WHERE course_id = %s AND book_id = ANY(%s)
    ORDER BY built_at DESC LIMIT 1"""
_TERMBASE_REJECTED = """SELECT source, rejection_reason, decided_at FROM book_termbase_entries
    WHERE book_id = ANY(%s) AND course_id = %s AND status = 'rejected' AND present
    ORDER BY decided_at DESC NULLS LAST LIMIT %s"""
_UNIT_COLUMNS = ("t.unit_id, t.seq, t.decision, t.translation_json->>'target_text', "
                 "COALESCE(t.translation_json->'issues', '[]'::jsonb), t.updated_at")
# Pick the newest rows by their keys first, then read only those rows' JSON.
_UNITS_RECENT = f"""WITH latest AS (SELECT unit_id FROM translation_unit_results WHERE run_id = %s
    ORDER BY updated_at DESC LIMIT %s)
    SELECT {_UNIT_COLUMNS} FROM translation_unit_results t JOIN latest USING (unit_id)
    WHERE t.run_id = %s ORDER BY t.updated_at DESC"""
_UNITS_BY_DECISION = f"""SELECT {_UNIT_COLUMNS} FROM translation_unit_results t
    WHERE t.run_id = %s AND t.decision = ANY(%s) ORDER BY t.seq LIMIT %s"""


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


class PostgresOperatorReader(OperatorReader):
    """Production reader: Postgres (read-only), the artifact store, the book library and Temporal."""

    def __init__(self, ctx: Any, *, connect: Callable[..., Any] | None = None) -> None:
        self.ctx = ctx
        self._connect_fn = connect
        self._units_cache: Any = None

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> Any:
        options = f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}"
        if self._connect_fn is not None:
            return self._connect_fn(conninfo=self.ctx.settings.pg_dsn, options=options, autocommit=True)
        import psycopg
        return psycopg.connect(self.ctx.settings.pg_dsn, options=options, autocommit=True)

    def _rows_sync(self, sql: str, params: Sequence[Any]) -> list[tuple]:
        with self._connect() as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    async def _rows(self, sql: str, params: Sequence[Any]) -> list[tuple]:
        return await run_in_threadpool(self._rows_sync, sql, params)

    # ------------------------------------------------------------------ library
    async def course_ids(self) -> list[str]:
        store = self.ctx.get_store()
        bucket = self.ctx.settings.s3_bucket

        def list_ids() -> list[str]:
            found: set[str] = set()
            args: dict[str, Any] = {"Bucket": bucket, "Delimiter": "/"}
            while True:
                page = store.s3.list_objects_v2(**args)
                found.update(e["Prefix"].rstrip("/") for e in page.get("CommonPrefixes", []))
                token = page.get("NextContinuationToken")
                if not token:
                    return sorted(c for c in found if not c.startswith("_"))
                args["ContinuationToken"] = token
        return await run_in_threadpool(list_ids)

    async def books(self, course_id: str) -> list[dict[str, Any]]:
        return list((await self.ctx.late("list_course_books")(course_id)).get("books", []))

    # ------------------------------------------------------------------ registry
    async def stage_outputs(self, course_id: str) -> list[StageOutputRecord]:
        def read() -> list[StageOutputRecord]:
            with self._connect() as conn:
                rows = list(conn.execute(_OUTPUTS, (course_id, MAX_OUTPUTS)).fetchall())
                gates: dict[tuple[str, str], list[GateResult]] = {}
                if rows:
                    for stage, key, result in conn.execute(_GATES, (course_id, MAX_GATES)).fetchall():
                        try:
                            gates.setdefault((stage, key), []).append(GateResult.model_validate(_json(result)))
                        except Exception:  # an unreadable gate row is skipped, never fatal
                            log.warning("skipping an unreadable gate result", extra={"stage": stage})
            out = []
            for (cid, stage, key, uri, sha, created, inputs, revision, config, required, status, reason,
                 changed_at, changed_by) in rows:
                try:
                    out.append(StageOutputRecord(
                        course_id=cid, artifact=ArtifactRef(stage=stage, key=key, uri=uri, sha256=sha,
                                                            created_at=_utc(created)),
                        inputs=[ArtifactRef.model_validate(i) for i in _json(inputs) or []], code_revision=revision,
                        config=_json(config) or {}, required_gates=list(_json(required) or []),
                        gate_results=gates.get((stage, key), []), status=status, status_reason=reason,
                        status_changed_at=_utc(changed_at), status_changed_by=changed_by))
                except Exception:
                    log.warning("skipping an unreadable stage output row", extra={"stage": stage})
            return out
        return await run_in_threadpool(read)

    # ------------------------------------------------------------------ translation
    async def translation_runs(self, course_id: str) -> list[RunCounts]:
        def read() -> list[RunCounts]:
            with self._connect() as conn:
                runs = list(conn.execute(_RUNS, (course_id,)).fetchall())
                ids = [r[0] for r in runs]
                counts: dict[str, dict[str, int]] = {}
                recent: dict[str, tuple[int, datetime | None]] = {}
                if ids:
                    for run_id, decision, n in conn.execute(_RUN_COUNTS, (ids,)).fetchall():
                        counts.setdefault(run_id, {})[decision] = int(n)
                    recent = {run_id: (int(n), _utc(at)) for run_id, n, at in conn.execute(_RUN_RECENT, (ids,)).fetchall()}
            return [RunCounts(run_id=r[0], workflow_id=r[1], book_id=r[2],
                              unit_count=None if r[3] is None else int(r[3]), created_at=_utc(r[4]),
                              updated_at=_utc(r[5]), counts=counts.get(r[0], {}),
                              decided_last_hour=recent.get(r[0], (0, None))[0], units_ref=_json(r[6]),
                              last_decided_at=recent.get(r[0], (0, None))[1])
                    for r in runs]
        return await run_in_threadpool(read)

    @staticmethod
    def _unit(row: tuple) -> UnitRow:
        unit_id, seq, decision, target, issues, updated = row
        return UnitRow(unit_id=unit_id, seq=int(seq), decision=decision, target_text=target,
                       issues=list(_json(issues) or []), updated_at=_utc(updated))

    async def recent_units(self, run_id: str, limit: int) -> list[UnitRow]:
        return [self._unit(r) for r in await self._rows(_UNITS_RECENT, (run_id, limit, run_id))]

    async def units_by_decision(self, run_id: str, decisions: Sequence[str], limit: int) -> list[UnitRow]:
        return [self._unit(r) for r in await self._rows(_UNITS_BY_DECISION, (run_id, list(decisions), limit))]

    async def unit_sources(self, course_id: str, units_ref: dict[str, Any] | None,
                           unit_ids: Sequence[str]) -> dict[str, str]:
        if not units_ref or not unit_ids:
            return {}
        from b2c_orchestration.translate_book import UNITS_STAGE, UnitsCache
        ref = ArtifactRef.model_validate(units_ref)
        bucket = self.ctx.settings.s3_bucket
        if ref.stage != UNITS_STAGE or not ref.uri.startswith(f"s3://{bucket}/{course_id}/"):
            return {}
        if self._units_cache is None:
            self._units_cache = UnitsCache(size=2)
        units = await run_in_threadpool(self._units_cache.get, self.ctx.get_store(), ref)
        wanted = set(unit_ids)
        return {u.unit_id: u.source_text for u in units if u.unit_id in wanted}

    # ------------------------------------------------------------------ termbase
    async def termbase(self, course_id: str, book_ids: Sequence[str]) -> BookTermbase | None:
        def read() -> BookTermbase | None:
            with self._connect() as conn:
                counts = list(conn.execute(_TERMBASE_COUNTS, (list(book_ids), course_id)).fetchall())
                build = conn.execute(_TERMBASE_BUILD, (course_id, list(book_ids))).fetchall()
            if not counts and not build:
                return None
            by_status = {status: int(n) for status, n, _ in counts}
            decided = [_utc(at) for _, _, at in counts if at is not None]
            return BookTermbase(pending=by_status.get("pending", 0), approved=by_status.get("approved", 0),
                                rejected=by_status.get("rejected", 0),
                                built_at=_utc(build[0][0]) if build else None,
                                entry_count=int(build[0][1]) if build else None,
                                last_decided_at=max(decided) if decided else None)
        return await run_in_threadpool(read)

    async def termbase_rejections(self, course_id: str, book_ids: Sequence[str], limit: int) -> list[TermRejection]:
        rows = await self._rows(_TERMBASE_REJECTED, (list(book_ids), course_id, limit))
        return [TermRejection(source=s, reason=r or "", decided_at=_utc(at)) for s, r, at in rows]

    # ------------------------------------------------------------------ outline / Temporal
    async def episode_total(self, course_id: str, outline_record: StageOutputRecord) -> int | None:
        from b2c_api.learner import mapping
        try:
            raw = await run_in_threadpool(self.ctx.get_store().read_json, outline_record.artifact,
                                          mapping.OUTLINE_FILE)
            return len(mapping.episodes_from_outline(raw))
        except Exception:
            log.warning("could not count the outline's episodes")
            return None

    async def workflow(self, workflow_id: str) -> WorkflowState | None:
        if not workflow_id:
            return None
        from temporalio.service import RPCError, RPCStatusCode
        client = await self.ctx.get_temporal_client()
        try:
            desc = await asyncio.wait_for(client.get_workflow_handle(workflow_id).describe(), TEMPORAL_TIMEOUT_S)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return None
            raise
        activity = attempt = None
        pending = list(getattr(getattr(desc, "raw_description", None), "pending_activities", []) or [])
        if pending:
            activity = pending[0].activity_type.name or None
            attempt = int(pending[0].attempt) or None
        status = getattr(getattr(desc, "status", None), "name", None) or "UNKNOWN"
        return WorkflowState(workflow_id=workflow_id, status=status, started_at=_utc(getattr(desc, "start_time", None)),
                             closed_at=_utc(getattr(desc, "close_time", None)), current_activity=activity,
                             activity_attempt=attempt)


__all__ = ["BookTermbase", "OperatorReader", "PostgresOperatorReader", "RunCounts", "TermRejection", "UnitRow",
           "WorkflowState"]
