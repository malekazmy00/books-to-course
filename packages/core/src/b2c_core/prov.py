"""Provenance recorder: W3C PROV relations in Postgres (docs/02-CONTRACTS.md §1.2, §3).

Tables (all created by `migrate()`, idempotent):
- artifacts          one row per stored artifact (ArtifactRef)
- prov_activity      an activity run: stage, agent (model@sha), prompt_version, config
- prov_used          activity --used--> entity
- prov_generated     entity --wasGeneratedBy--> activity
- prov_derived       entity --wasDerivedFrom--> entity
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import psycopg

from b2c_contracts import ArtifactRef

DDL = """
CREATE TABLE IF NOT EXISTS artifacts (
    course_id  text NOT NULL,
    stage      text NOT NULL,
    key        text NOT NULL,
    uri        text NOT NULL,
    sha256     text NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (course_id, stage, key)
);
CREATE TABLE IF NOT EXISTS prov_activity (
    activity_id    text PRIMARY KEY,
    stage          text NOT NULL,
    stage_version  text NOT NULL,
    agent          text,
    prompt_version text,
    config         jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at     timestamptz NOT NULL,
    ended_at       timestamptz
);
CREATE TABLE IF NOT EXISTS prov_used (
    activity_id text NOT NULL REFERENCES prov_activity(activity_id),
    entity      text NOT NULL,
    role        text NOT NULL DEFAULT 'input',
    PRIMARY KEY (activity_id, entity, role)
);
CREATE TABLE IF NOT EXISTS prov_generated (
    entity      text PRIMARY KEY,
    activity_id text NOT NULL REFERENCES prov_activity(activity_id)
);
CREATE TABLE IF NOT EXISTS prov_derived (
    entity       text NOT NULL,
    derived_from text NOT NULL,
    PRIMARY KEY (entity, derived_from)
);
"""


class ProvRecorder:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True)

    def migrate(self) -> None:
        with self._conn() as c:
            c.execute(DDL)

    # ---------------------------------------------------------------- artifacts
    def record_artifact(self, course_id: str, ref: ArtifactRef) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO artifacts (course_id, stage, key, uri, sha256, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (course_id, ref.stage, ref.key, ref.uri, ref.sha256, ref.created_at),
            )

    def get_artifact(self, course_id: str, stage: str, key: str) -> ArtifactRef | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT stage, key, uri, sha256, created_at FROM artifacts "
                "WHERE course_id=%s AND stage=%s AND key=%s",
                (course_id, stage, key),
            ).fetchone()
        if row is None:
            return None
        return ArtifactRef(stage=row[0], key=row[1], uri=row[2], sha256=row[3],
                           created_at=row[4].astimezone(timezone.utc))

    # ---------------------------------------------------------------- PROV
    def start_activity(self, *, stage: str, stage_version: str, agent: str | None = None,
                       prompt_version: str | None = None, config: dict[str, Any] | None = None,
                       activity_id: str | None = None) -> str:
        act = activity_id or f"act:{stage}:{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                "INSERT INTO prov_activity (activity_id, stage, stage_version, agent, prompt_version,"
                " config, started_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (act, stage, stage_version, agent, prompt_version, json.dumps(config or {}),
                 datetime.now(timezone.utc)),
            )
        return act

    def end_activity(self, activity_id: str) -> None:
        # Idempotent: re-recording a cache hit keeps the original end time.
        with self._conn() as c:
            c.execute("UPDATE prov_activity SET ended_at=%s WHERE activity_id=%s AND ended_at IS NULL",
                      (datetime.now(timezone.utc), activity_id))

    def record_activity(self, *, stage: str, stage_version: str, agent: str | None = None,
                        prompt_version: str | None = None, config: dict[str, Any] | None = None,
                        activity_id: str | None = None, used: Sequence[str] = (),
                        derived: Sequence[tuple[str, str]] = (),
                        generated: Sequence[str] = ()) -> str:
        """Record a complete activity and its relations in one transaction.

        Every statement is idempotent (ON CONFLICT DO NOTHING / ended_at IS
        NULL), so callers record on every run, including cache hits.
        """
        act = activity_id or f"act:{stage}:{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        with psycopg.connect(self.dsn) as c:  # commits on clean exit
            c.execute(
                "INSERT INTO prov_activity (activity_id, stage, stage_version, agent, prompt_version,"
                " config, started_at) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (act, stage, stage_version, agent, prompt_version, json.dumps(config or {}), now),
            )
            for entity in used:
                c.execute("INSERT INTO prov_used VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                          (act, entity, "input"))
            for entity, source in derived:
                c.execute("INSERT INTO prov_derived VALUES (%s,%s) ON CONFLICT DO NOTHING",
                          (entity, source))
            for entity in generated:
                c.execute("INSERT INTO prov_generated VALUES (%s,%s) ON CONFLICT DO NOTHING",
                          (entity, act))
            c.execute("UPDATE prov_activity SET ended_at=%s WHERE activity_id=%s AND ended_at IS NULL",
                      (now, act))
        return act

    def used(self, activity_id: str, entity: str, role: str = "input") -> None:
        with self._conn() as c:
            c.execute("INSERT INTO prov_used VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                      (activity_id, entity, role))

    def generated(self, entity: str, activity_id: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO prov_generated VALUES (%s,%s) ON CONFLICT DO NOTHING",
                      (entity, activity_id))

    def derived(self, entity: str, derived_from: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO prov_derived VALUES (%s,%s) ON CONFLICT DO NOTHING",
                      (entity, derived_from))

    def lineage(self, entity: str) -> dict[str, Any]:
        """One-hop PROV view of an entity, in the shape of docs/02 §3 'Provenance'."""
        with self._conn() as c:
            gen = c.execute(
                "SELECT a.activity_id, a.agent, a.prompt_version FROM prov_generated g "
                "JOIN prov_activity a USING (activity_id) WHERE g.entity=%s",
                (entity,),
            ).fetchone()
            derived = [r[0] for r in c.execute(
                "SELECT derived_from FROM prov_derived WHERE entity=%s ORDER BY derived_from",
                (entity,),
            )]
            used = []
            if gen:
                used = [{"entity": r[0], "role": r[1]} for r in c.execute(
                    "SELECT entity, role FROM prov_used WHERE activity_id=%s ORDER BY entity",
                    (gen[0],),
                )]
        return {
            "entity": entity,
            "wasGeneratedBy": (
                {"activity": gen[0], "agent": gen[1], "prompt_version": gen[2]} if gen else None
            ),
            "wasDerivedFrom": derived,
            "used": used,
        }
