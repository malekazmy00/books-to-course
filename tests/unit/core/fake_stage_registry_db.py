"""In-memory stand-in for the three Postgres tables of ``b2c_core.stage_registry``.

It dispatches on the module's own SQL constants, so a statement the registry issues that this
fake does not know fails loudly. The history tables only ever receive INSERTs here; any other
statement against them is an error (the database enforces the same with triggers).
"""
from __future__ import annotations

import json

import b2c_core.stage_registry as sr


class FakeCursor:
    def __init__(self, rows=(), rowcount=0):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeRegistryDB:
    def __init__(self):
        self.outputs: dict[tuple[str, str, str], dict] = {}
        self.gates: list[dict] = []
        self.events: list[dict] = []
        self.ddl = 0
        self.statements: list[str] = []
        self.fail_next_update = False

    def connect(self):
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeRegistryDB):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def _row(o):
        return (o["course_id"], o["stage"], o["key"], o["uri"], o["sha256"], o["artifact_created_at"],
                o["inputs_json"], o["code_revision"], o["config_json"], o["identities_json"], o["required_gates"],
                o["status"], o["status_reason"], o["status_changed_at"], o["status_changed_by"])

    def execute(self, sql, params=()):  # noqa: C901 - one branch per statement
        db = self.db
        db.statements.append(sql)
        if sql in sr._DDL:
            db.ddl += 1
            return FakeCursor()
        if sql == sr._INSERT_OUTPUT:
            (course, stage, key, uri, sha, created, inputs, rev, config, identities, required, status, reason,
             changed_at, changed_by, registered_at) = params
            if (course, stage, key) in db.outputs:
                return FakeCursor(rowcount=0)
            db.outputs[(course, stage, key)] = dict(
                course_id=course, stage=stage, key=key, uri=uri, sha256=sha, artifact_created_at=created,
                inputs_json=json.loads(inputs), code_revision=rev, config_json=json.loads(config),
                identities_json=json.loads(identities), required_gates=json.loads(required), status=status,
                status_reason=reason, status_changed_at=changed_at, status_changed_by=changed_by,
                registered_at=registered_at)
            return FakeCursor(rowcount=1)
        if sql in (sr._SELECT_OUTPUT, sr._LOCK_OUTPUT):
            o = db.outputs.get(tuple(params))
            return FakeCursor([self._row(o)] if o else [])
        if sql == sr._LIST_OUTPUTS:
            course, _, stage, _, status, _, limit, offset = params
            rows = [o for o in db.outputs.values() if (course is None or o["course_id"] == course)
                    and (stage is None or o["stage"] == stage) and (status is None or o["status"] == status)]
            rows.sort(key=lambda o: o["key"])
            rows.sort(key=lambda o: o["artifact_created_at"], reverse=True)
            rows.sort(key=lambda o: (o["course_id"], o["stage"]))
            return FakeCursor([self._row(o) for o in rows[offset:offset + limit]])
        if sql == sr._COUNT_OUTPUTS:
            course, _ = params
            counts: dict[tuple[str, str], int] = {}
            for o in db.outputs.values():
                if course is None or o["course_id"] == course:
                    counts[(o["stage"], o["status"])] = counts.get((o["stage"], o["status"]), 0) + 1
            return FakeCursor([(s, st, n) for (s, st), n in sorted(counts.items())])
        if sql == sr._UPDATE_STATUS:
            status, reason, at, actor, course, stage, key, expected = params
            o = db.outputs.get((course, stage, key))
            if db.fail_next_update:
                db.fail_next_update = False
                return FakeCursor(rowcount=0)
            if o is None or o["status"] != expected:
                return FakeCursor(rowcount=0)
            o.update(status=status, status_reason=reason, status_changed_at=at, status_changed_by=actor)
            return FakeCursor(rowcount=1)
        if sql == sr._INSERT_GATE:
            (course, stage, key, gate_id, version, kind, passed, decider, decided_by, decided_at, digest,
             result, recorded_at) = params
            assert (course, stage, key) in db.outputs, "foreign key"
            if any((g["course_id"], g["stage"], g["key"], g["gate_id"], g["result_digest"])
                   == (course, stage, key, gate_id, digest) for g in db.gates):
                return FakeCursor(rowcount=0)
            db.gates.append(dict(course_id=course, stage=stage, key=key, gate_id=gate_id, passed=passed,
                                 decided_at=decided_at, result_digest=digest, result_json=json.loads(result)))
            return FakeCursor(rowcount=1)
        if sql == sr._SELECT_GATES:
            course, stage, key = params
            rows = [g for g in db.gates if (g["course_id"], g["stage"], g["key"]) == (course, stage, key)]
            rows.sort(key=lambda g: g["decided_at"])
            return FakeCursor([(g["result_json"],) for g in rows])
        if sql == sr._INSERT_EVENT:
            course, stage, key, frm, to, reason, actor, rev, at = params
            assert (course, stage, key) in db.outputs, "foreign key"
            db.events.append(dict(course_id=course, stage=stage, key=key, from_status=frm, to_status=to,
                                  reason=reason, actor=actor, code_revision=rev, at=at))
            return FakeCursor(rowcount=1)
        if sql == sr._SELECT_EVENTS:
            rows = [e for e in db.events if (e["course_id"], e["stage"], e["key"]) == tuple(params)]
            return FakeCursor([(e["from_status"], e["to_status"], e["reason"], e["actor"], e["code_revision"],
                                e["at"]) for e in rows])
        raise AssertionError(f"unexpected SQL: {' '.join(sql.split())[:120]}")
