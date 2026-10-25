"""Registry of stage outputs and their acceptance status (R1; audit 08 C1/C4, plan P2/P5).

One row per stored artifact in ``stage_outputs``: the artifact, its parent artifacts, the code
revision of the process that registered it, its config (stage_key inputs), the model/prompt
identities that produced it, the gates it must pass and its lifecycle status
(``candidate -> accepted | rejected``; ``quarantined`` for legacy outputs).

Two append-only tables keep the history; UPDATE/DELETE on them raise in the database:

* ``stage_output_gates``  every ``GateResult`` attached to an output (deduplicated by content,
  so a retried activity that reaches the same verdict does not add a second row);
* ``stage_output_events`` every status change, with the actor and the code revision.

Status changes are compare-and-set inside one transaction (``SELECT ... FOR UPDATE``), and the
new state is validated by the ``StageOutputRecord`` contract before it is written, so the
database never holds an ``accepted`` row whose required gates did not all pass.

Schema creation is idempotent, serialized by an advisory lock and done once per instance.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from b2c_contracts import STAGE_OUTPUT_TRANSITIONS, ArtifactRef, GateResult, StageOutputRecord

STATUSES = ("candidate", "accepted", "rejected", "quarantined")
INITIAL_STATUSES = ("candidate", "quarantined")

_DDL = (
    "SELECT pg_advisory_xact_lock(hashtext('b2c_stage_outputs_schema'))",
    """CREATE TABLE IF NOT EXISTS stage_outputs (
        course_id text NOT NULL,
        stage text NOT NULL,
        key text NOT NULL,
        uri text NOT NULL,
        sha256 text NOT NULL,
        artifact_created_at timestamptz NOT NULL,
        inputs_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        code_revision text NOT NULL,
        config_json jsonb NOT NULL DEFAULT '{}'::jsonb,
        identities_json jsonb NOT NULL DEFAULT '[]'::jsonb,
        required_gates jsonb NOT NULL DEFAULT '[]'::jsonb,
        status text NOT NULL CHECK (status IN ('candidate', 'accepted', 'rejected', 'quarantined')),
        status_reason text,
        status_changed_at timestamptz NOT NULL,
        status_changed_by text NOT NULL,
        registered_at timestamptz NOT NULL,
        PRIMARY KEY (course_id, stage, key)
    )""",
    "CREATE INDEX IF NOT EXISTS stage_outputs_by_status ON stage_outputs (course_id, stage, status)",
    """CREATE TABLE IF NOT EXISTS stage_output_gates (
        id bigserial PRIMARY KEY,
        course_id text NOT NULL,
        stage text NOT NULL,
        key text NOT NULL,
        gate_id text NOT NULL,
        gate_version text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('deterministic', 'semantic', 'human')),
        passed boolean NOT NULL,
        decider text NOT NULL CHECK (decider IN ('system', 'human')),
        decided_by text NOT NULL,
        decided_at timestamptz NOT NULL,
        result_digest text NOT NULL,
        result_json jsonb NOT NULL,
        recorded_at timestamptz NOT NULL,
        UNIQUE (course_id, stage, key, gate_id, result_digest),
        FOREIGN KEY (course_id, stage, key) REFERENCES stage_outputs (course_id, stage, key)
    )""",
    """CREATE TABLE IF NOT EXISTS stage_output_events (
        id bigserial PRIMARY KEY,
        course_id text NOT NULL,
        stage text NOT NULL,
        key text NOT NULL,
        from_status text,
        to_status text NOT NULL,
        reason text,
        actor text NOT NULL,
        code_revision text NOT NULL,
        at timestamptz NOT NULL,
        FOREIGN KEY (course_id, stage, key) REFERENCES stage_outputs (course_id, stage, key)
    )""",
    """CREATE OR REPLACE FUNCTION b2c_refuse_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
    END $$""",
    """CREATE OR REPLACE TRIGGER stage_output_gates_append_only BEFORE UPDATE OR DELETE ON stage_output_gates
        FOR EACH ROW EXECUTE FUNCTION b2c_refuse_history_change()""",
    """CREATE OR REPLACE TRIGGER stage_output_events_append_only BEFORE UPDATE OR DELETE ON stage_output_events
        FOR EACH ROW EXECUTE FUNCTION b2c_refuse_history_change()""",
)

_OUTPUT_COLUMNS = ("course_id, stage, key, uri, sha256, artifact_created_at, inputs_json, code_revision, "
                   "config_json, identities_json, required_gates, status, status_reason, status_changed_at, "
                   "status_changed_by")
_INSERT_OUTPUT = f"""INSERT INTO stage_outputs ({_OUTPUT_COLUMNS}, registered_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
    ON CONFLICT (course_id, stage, key) DO NOTHING"""
_SELECT_OUTPUT = f"SELECT {_OUTPUT_COLUMNS} FROM stage_outputs WHERE course_id = %s AND stage = %s AND key = %s"
_LOCK_OUTPUT = _SELECT_OUTPUT + " FOR UPDATE"
_LIST_OUTPUTS = f"""SELECT {_OUTPUT_COLUMNS} FROM stage_outputs
    WHERE (%s::text IS NULL OR course_id = %s) AND (%s::text IS NULL OR stage = %s)
      AND (%s::text IS NULL OR status = %s)
    ORDER BY course_id, stage, artifact_created_at DESC, key LIMIT %s OFFSET %s"""
_COUNT_OUTPUTS = """SELECT stage, status, count(*) FROM stage_outputs
    WHERE (%s::text IS NULL OR course_id = %s) GROUP BY stage, status ORDER BY stage, status"""
_UPDATE_STATUS = """UPDATE stage_outputs SET status = %s, status_reason = %s, status_changed_at = %s,
    status_changed_by = %s WHERE course_id = %s AND stage = %s AND key = %s AND status = %s"""
_INSERT_GATE = """INSERT INTO stage_output_gates (course_id, stage, key, gate_id, gate_version, kind, passed,
    decider, decided_by, decided_at, result_digest, result_json, recorded_at)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
    ON CONFLICT (course_id, stage, key, gate_id, result_digest) DO NOTHING"""
_SELECT_GATES = """SELECT result_json FROM stage_output_gates WHERE course_id = %s AND stage = %s AND key = %s
    ORDER BY decided_at, id"""
_INSERT_EVENT = """INSERT INTO stage_output_events (course_id, stage, key, from_status, to_status, reason, actor,
    code_revision, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""
_SELECT_EVENTS = """SELECT from_status, to_status, reason, actor, code_revision, at FROM stage_output_events
    WHERE course_id = %s AND stage = %s AND key = %s ORDER BY id"""


class TransitionRefused(ValueError):
    """A status change that the lifecycle or the record invariants do not allow."""


class UnknownStageOutput(LookupError):
    """No registry row for this artifact."""


@dataclass(frozen=True)
class StatusEvent:
    from_status: str | None
    to_status: str
    reason: str | None
    actor: str
    code_revision: str
    at: datetime


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def gate_result_digest(result: GateResult) -> str:
    """Content digest of a gate verdict, without its timestamp (dedupes retried activities)."""
    payload = result.model_dump(mode="json", exclude={"decided_at"})
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _status(status: str | None) -> str | None:
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    return status


class StageOutputRegistry:
    def __init__(self, dsn: str, *, connect: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.dsn = dsn
        self._connect_fn = connect
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    # ------------------------------------------------------------------ plumbing
    def _connect(self) -> Any:
        if self._connect_fn is not None:
            return self._connect_fn()
        import psycopg
        return psycopg.connect(self.dsn)  # one transaction per `with` block, committed on clean exit

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

    @staticmethod
    def _record(row: Sequence[Any], gates: Sequence[Any]) -> StageOutputRecord:
        (course_id, stage, key, uri, sha256, created_at, inputs, code_revision, config, identities, required,
         status, reason, changed_at, changed_by) = row
        return StageOutputRecord(
            course_id=course_id,
            artifact=ArtifactRef(stage=stage, key=key, uri=uri, sha256=sha256, created_at=_utc(created_at)),
            inputs=[ArtifactRef.model_validate(i) for i in _json(inputs) or []], code_revision=code_revision,
            config=_json(config) or {}, identities=_json(identities) or [], required_gates=_json(required) or [],
            gate_results=[GateResult.model_validate(_json(g[0])) for g in gates], status=status,
            status_reason=reason, status_changed_at=_utc(changed_at), status_changed_by=changed_by)

    def _load(self, conn: Any, course_id: str, stage: str, key: str, *, lock: bool = False) -> StageOutputRecord | None:
        row = conn.execute(_LOCK_OUTPUT if lock else _SELECT_OUTPUT, (course_id, stage, key)).fetchone()
        if row is None:
            return None
        gates = conn.execute(_SELECT_GATES, (course_id, stage, key)).fetchall()
        return self._record(row, gates)

    # ------------------------------------------------------------------ writes
    def register(self, record: StageOutputRecord) -> bool:
        """Insert a new output as ``candidate`` or ``quarantined``; an existing row is kept as is.

        Returns True when the row was created. Gate results carried by ``record`` are attached.
        """
        if record.status not in INITIAL_STATUSES:
            raise TransitionRefused(f"an output is registered as one of {INITIAL_STATUSES}, not {record.status}")
        self.ensure_schema()
        ref = record.artifact
        now = self._clock()
        with self._connect() as conn:
            created = conn.execute(_INSERT_OUTPUT, (
                record.course_id, ref.stage, ref.key, ref.uri, ref.sha256, ref.created_at,
                json.dumps([i.model_dump(mode="json") for i in record.inputs]), record.code_revision,
                json.dumps(record.config, ensure_ascii=False, sort_keys=True),
                json.dumps([i.model_dump(mode="json") for i in record.identities], ensure_ascii=False),
                json.dumps(list(record.required_gates)), record.status, record.status_reason,
                record.status_changed_at, record.status_changed_by, now)).rowcount > 0
            if created:
                conn.execute(_INSERT_EVENT, (record.course_id, ref.stage, ref.key, None, record.status,
                                             record.status_reason, record.status_changed_by, record.code_revision,
                                             now))
            for result in record.gate_results:
                self._insert_gate(conn, record.course_id, result, now)
        return created

    def _insert_gate(self, conn: Any, course_id: str, result: GateResult, now: datetime) -> bool:
        ref = result.artifact
        return conn.execute(_INSERT_GATE, (
            course_id, ref.stage, ref.key, result.gate_id, result.gate_version, result.kind, result.passed,
            result.decider, result.decided_by, result.decided_at, gate_result_digest(result),
            result.model_dump_json(), now)).rowcount > 0

    def add_gate_result(self, course_id: str, result: GateResult) -> bool:
        """Append a gate result; False when the same verdict was already recorded."""
        self.ensure_schema()
        ref = result.artifact
        with self._connect() as conn:
            current = self._load(conn, course_id, ref.stage, ref.key, lock=True)
            if current is None:
                raise UnknownStageOutput(f"{course_id}/{ref.stage}/{ref.key} is not registered")
            if (current.artifact.uri, current.artifact.sha256) != (ref.uri, ref.sha256):
                raise ValueError("the gate result names a different artifact than the registry row")
            return self._insert_gate(conn, course_id, result, self._clock())

    def transition(self, course_id: str, stage: str, key: str, to_status: str, *, actor: str,
                   code_revision: str, reason: str | None = None,
                   expected: str | None = None) -> StageOutputRecord:
        """Change the status if the lifecycle and the contract invariants allow it.

        ``expected`` (optional) makes the change conditional on the current status. A request for
        the status the row already has is a no-op that returns the row (idempotent retries).
        """
        _status(to_status)
        self.ensure_schema()
        with self._connect() as conn:
            current = self._load(conn, course_id, stage, key, lock=True)
            if current is None:
                raise UnknownStageOutput(f"{course_id}/{stage}/{key} is not registered")
            if current.status == to_status:
                return current
            if expected is not None and current.status != expected:
                raise TransitionRefused(f"status is {current.status}, expected {expected}")
            if to_status not in STAGE_OUTPUT_TRANSITIONS[current.status]:
                raise TransitionRefused(f"{current.status} -> {to_status} is not an allowed transition")
            now = self._clock()
            try:
                # rebuilt (not model_copy) so the contract invariants run on the new state
                updated = StageOutputRecord.model_validate({
                    **current.model_dump(mode="python"), "status": to_status, "status_reason": reason,
                    "status_changed_at": now, "status_changed_by": actor})
            except ValidationError as exc:
                raise TransitionRefused(f"{current.status} -> {to_status} refused: {exc.errors()[0]['msg']}") from exc
            changed = conn.execute(_UPDATE_STATUS, (to_status, reason, now, actor, course_id, stage, key,
                                                    current.status)).rowcount
            if changed != 1:
                raise TransitionRefused("the row changed concurrently; retry")
            conn.execute(_INSERT_EVENT, (course_id, stage, key, current.status, to_status, reason, actor,
                                         code_revision, now))
        return updated

    # ------------------------------------------------------------------ reads
    def get(self, course_id: str, stage: str, key: str) -> StageOutputRecord | None:
        self.ensure_schema()
        with self._connect() as conn:
            return self._load(conn, course_id, stage, key)

    def status_of(self, course_id: str, ref: ArtifactRef) -> str | None:
        """Status of exactly this artifact (uri and sha must match the registry row), else None."""
        record = self.get(course_id, ref.stage, ref.key)
        if record is None or (record.artifact.uri, record.artifact.sha256) != (ref.uri, ref.sha256):
            return None
        return record.status

    def list(self, *, course_id: str | None = None, stage: str | None = None, status: str | None = None,
             limit: int = 100, offset: int = 0) -> list[StageOutputRecord]:
        _status(status)
        if not 1 <= limit <= 1000 or offset < 0:
            raise ValueError("limit must be 1..1000 and offset >= 0")
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(_LIST_OUTPUTS, (course_id, course_id, stage, stage, status, status,
                                                limit, offset)).fetchall()
            return [self._record(r, conn.execute(_SELECT_GATES, (r[0], r[1], r[2])).fetchall()) for r in rows]

    def accepted(self, *, course_id: str, stage: str, limit: int = 100) -> list[StageOutputRecord]:
        """Only ``accepted`` outputs: the accessor downstream stages and serving read through."""
        return self.list(course_id=course_id, stage=stage, status="accepted", limit=limit)

    def counts(self, *, course_id: str | None = None) -> dict[str, dict[str, int]]:
        """{stage: {status: n}}."""
        self.ensure_schema()
        out: dict[str, dict[str, int]] = {}
        with self._connect() as conn:
            for stage, status, n in conn.execute(_COUNT_OUTPUTS, (course_id, course_id)).fetchall():
                out.setdefault(stage, {})[status] = int(n)
        return out

    def history(self, course_id: str, stage: str, key: str) -> list[StatusEvent]:
        self.ensure_schema()
        with self._connect() as conn:
            rows = conn.execute(_SELECT_EVENTS, (course_id, stage, key)).fetchall()
        return [StatusEvent(f, t, r, a, c, _utc(at)) for f, t, r, a, c, at in rows]


__all__ = ["INITIAL_STATUSES", "STATUSES", "StageOutputRegistry", "StatusEvent", "TransitionRefused",
           "UnknownStageOutput", "gate_result_digest"]
