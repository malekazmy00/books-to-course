"""StageOutputRegistry (R1): lifecycle, gate attachment, append-only history — with a fake DB."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import ArtifactRef, GateResult, ProducerIdentity, StageOutputRecord

import b2c_core.stage_registry as sr
from b2c_core.stage_registry import StageOutputRegistry, TransitionRefused, UnknownStageOutput
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40
KEY = "b" * 64


def ref(key: str = KEY, stage: str = "translate-book", course: str = "c1") -> ArtifactRef:
    return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/{course}/{stage}/{key}/", sha256="c" * 64,
                       created_at=NOW)


PARENT = ref("d" * 64, stage="translation-units")


def gate(passed=True, artifact=None, at=NOW, **kw) -> GateResult:
    base = dict(stage="translate-book", artifact=artifact or ref(), gate_id="translate-book.unit-decisions",
                gate_version="v1", kind="deterministic", passed=passed, metrics={"pending": 0 if passed else 3},
                decider="system", decided_by="policy:test", decided_at=at)
    base.update(kw)
    return GateResult(**base)


def candidate(**kw) -> StageOutputRecord:
    base = dict(course_id="c1", artifact=ref(), inputs=[PARENT], code_revision=SHA, config={"run_id": "r"},
                identities=[ProducerIdentity(role="engine", model_id="m", prompt_version="p@1")],
                required_gates=["translate-book.unit-decisions"], status="candidate", status_changed_at=NOW,
                status_changed_by="worker")
    base.update(kw)
    return StageOutputRecord(**base)


@pytest.fixture
def db():
    return FakeRegistryDB()


@pytest.fixture
def registry(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def test_schema_is_created_once_and_includes_append_only_triggers(registry, db):
    registry.get("c1", "translate-book", KEY)
    registry.get("c1", "translate-book", KEY)
    assert db.ddl == len(sr._DDL)
    ddl = " ".join(sr._DDL)
    assert "stage_output_gates_append_only" in ddl and "stage_output_events_append_only" in ddl
    assert "BEFORE UPDATE OR DELETE" in ddl and "pg_advisory_xact_lock" in sr._DDL[0]


def test_register_is_idempotent_and_records_lineage_and_revision(registry, db):
    assert registry.register(candidate())
    assert not registry.register(candidate(config={"other": 1}))  # first registration wins
    got = registry.get("c1", "translate-book", KEY)
    assert got.status == "candidate" and got.code_revision == SHA and got.config == {"run_id": "r"}
    assert got.inputs == [PARENT] and got.identities[0].model_id == "m"
    assert [(e.from_status, e.to_status) for e in registry.history("c1", "translate-book", KEY)] == [
        (None, "candidate")]


def test_register_refuses_accepted_or_rejected_rows(registry):
    with pytest.raises(TransitionRefused):
        registry.register(candidate(status="accepted", gate_results=[gate()]))


def test_accept_needs_the_required_gate_passed(registry):
    registry.register(candidate())
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="promoter", code_revision=SHA)
    registry.add_gate_result("c1", gate())
    out = registry.transition("c1", "translate-book", KEY, "accepted", actor="promoter", code_revision=SHA)
    assert out.status == "accepted"
    assert registry.get("c1", "translate-book", KEY).status == "accepted"
    assert [e.to_status for e in registry.history("c1", "translate-book", KEY)] == ["candidate", "accepted"]


def test_failed_gate_blocks_acceptance(registry):
    registry.register(candidate(gate_results=[gate(passed=False)]))
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    out = registry.transition("c1", "translate-book", KEY, "rejected", actor="p", code_revision=SHA,
                              reason="gate failed")
    assert out.status == "rejected"
    with pytest.raises(TransitionRefused):  # rejected is final
        registry.transition("c1", "translate-book", KEY, "candidate", actor="p", code_revision=SHA)


def test_unknown_or_dirty_revision_cannot_be_accepted(registry):
    registry.register(candidate(code_revision=SHA + "-dirty", gate_results=[gate()]))
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)


def test_gate_results_are_deduplicated_by_content(registry, db):
    registry.register(candidate())
    assert registry.add_gate_result("c1", gate(at=NOW))
    assert not registry.add_gate_result("c1", gate(at=NOW + timedelta(minutes=1)))  # retried activity
    assert registry.add_gate_result("c1", gate(passed=False))
    assert len(db.gates) == 2


def test_gate_result_for_an_unregistered_or_different_artifact_is_refused(registry):
    with pytest.raises(UnknownStageOutput):
        registry.add_gate_result("c1", gate())
    registry.register(candidate())
    other = ArtifactRef(stage="translate-book", key=KEY, uri=f"s3://artifacts/c1/translate-book/{KEY}/",
                        sha256="9" * 64, created_at=NOW)
    with pytest.raises(ValueError):
        registry.add_gate_result("c1", gate(artifact=other))


def test_transition_to_the_current_status_is_a_no_op(registry):
    registry.register(candidate(gate_results=[gate()]))
    registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    again = registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    assert again.status == "accepted"
    assert len(registry.history("c1", "translate-book", KEY)) == 2


def test_expected_status_and_concurrent_change_are_refused(registry, db):
    registry.register(candidate(gate_results=[gate()]))
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA,
                            expected="quarantined")
    db.fail_next_update = True
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    assert registry.get("c1", "translate-book", KEY).status == "candidate"


def test_quarantined_output_is_regated_through_candidate(registry):
    registry.register(candidate(status="quarantined", status_reason="legacy pre-gate", code_revision="unknown",
                                inputs=[], identities=[], required_gates=[]))
    with pytest.raises(TransitionRefused):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    assert registry.transition("c1", "translate-book", KEY, "candidate", actor="p",
                               code_revision=SHA).status == "candidate"


def test_unknown_output_transition(registry):
    with pytest.raises(UnknownStageOutput):
        registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)


def test_accepted_accessor_lists_only_accepted_and_status_of_checks_identity(registry):
    registry.register(candidate(gate_results=[gate()]))
    second = ref("e" * 64)
    registry.register(candidate(artifact=second))
    registry.transition("c1", "translate-book", KEY, "accepted", actor="p", code_revision=SHA)
    assert [r.artifact.key for r in registry.accepted(course_id="c1", stage="translate-book")] == [KEY]
    assert registry.status_of("c1", ref()) == "accepted"
    assert registry.status_of("c1", second) == "candidate"
    forged = ArtifactRef(stage="translate-book", key=KEY, uri=ref().uri, sha256="0" * 64, created_at=NOW)
    assert registry.status_of("c1", forged) is None
    assert registry.counts(course_id="c1") == {"translate-book": {"accepted": 1, "candidate": 1}}


def test_list_validates_arguments(registry):
    with pytest.raises(ValueError):
        registry.list(status="approved")
    with pytest.raises(ValueError):
        registry.list(limit=0)
