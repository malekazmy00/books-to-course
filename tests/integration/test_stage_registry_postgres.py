"""StageOutputRegistry against a real Postgres (R1): DDL, lifecycle, dedupe, append-only triggers."""
import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from b2c_contracts import ArtifactRef, GateResult, StageOutputRecord
from b2c_core import Settings
from b2c_core.stage_registry import StageOutputRegistry, TransitionRefused

pytestmark = pytest.mark.integration
NOW = datetime.now(timezone.utc)
SHA = "a" * 40


def test_lifecycle_dedupe_and_append_only_history():
    dsn = os.environ.get("B2C_TEST_PG_DSN") or Settings().pg_dsn  # override: a throwaway local Postgres
    registry = StageOutputRegistry(dsn)
    StageOutputRegistry(dsn).ensure_schema()  # idempotent across instances
    course = f"itest-{uuid.uuid4().hex[:8]}"
    key = uuid.uuid4().hex * 2
    ref = ArtifactRef(stage="translate-book", key=key, uri=f"s3://artifacts/{course}/translate-book/{key}/",
                      sha256="c" * 64, created_at=NOW)
    record = StageOutputRecord(course_id=course, artifact=ref, code_revision=SHA, config={"k": 1},
                               required_gates=["g.one"], status="candidate", status_changed_at=NOW,
                               status_changed_by="itest")
    assert registry.register(record)
    assert not registry.register(record)
    with pytest.raises(TransitionRefused):
        registry.transition(course, ref.stage, key, "accepted", actor="itest", code_revision=SHA)
    gate = GateResult(stage=ref.stage, artifact=ref, gate_id="g.one", gate_version="v1", kind="deterministic",
                      passed=True, metrics={"n": 1}, decider="system", decided_by="policy:itest", decided_at=NOW)
    assert registry.add_gate_result(course, gate)
    assert not registry.add_gate_result(course, gate)
    assert registry.transition(course, ref.stage, key, "accepted", actor="itest", code_revision=SHA).status == "accepted"
    assert registry.status_of(course, ref) == "accepted"
    assert [r.artifact.key for r in registry.accepted(course_id=course, stage=ref.stage)] == [key]
    assert registry.counts(course_id=course) == {"translate-book": {"accepted": 1}}
    assert [(e.from_status, e.to_status) for e in registry.history(course, ref.stage, key)] == [
        (None, "candidate"), ("candidate", "accepted")]
    with psycopg.connect(dsn) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("UPDATE stage_output_events SET actor = 'x' WHERE course_id = %s", (course,))
    with psycopg.connect(dsn) as conn, pytest.raises(psycopg.errors.RaiseException):
        conn.execute("DELETE FROM stage_output_gates WHERE course_id = %s", (course,))
