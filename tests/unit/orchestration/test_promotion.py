"""Promotion API (R1): candidate -> gates -> accepted | rejected, and the accepted-only accessor."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import ArtifactRef, GateResult
from b2c_core.stage_registry import StageOutputRegistry, TransitionRefused

from b2c_orchestration.promotion import (TRANSLATION_ASSEMBLY_POLICY, GatePolicy, NotAccepted, PromotionService,
                                         evaluate_translation_assembly)
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


def ref(key="b" * 64, stage="translate-book") -> ArtifactRef:
    return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/c1/{stage}/{key}/", sha256="c" * 64,
                       created_at=NOW)


@pytest.fixture
def db():
    return FakeRegistryDB()


def service(db, revision=SHA):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision=revision, clock=lambda: NOW)


def gate(passed=True, gate_id="g.one", kind="deterministic", **kw):
    decider = "human" if kind == "human" else "system"
    return GateResult(stage="translate-book", artifact=ref(), gate_id=gate_id, gate_version="v1", kind=kind,
                      passed=passed, decider=decider, decided_by="reviewer-1" if decider == "human" else "policy",
                      decided_at=NOW, **kw)


def test_policy_version_is_derived_from_its_definition():
    a = GatePolicy(gate_id="g.one", kind="deterministic", definition={"max_pending": 0})
    b = GatePolicy(gate_id="g.one", kind="deterministic", definition={"max_pending": 1})
    assert a.version == GatePolicy(gate_id="g.one", kind="deterministic", definition={"max_pending": 0}).version
    assert a.version != b.version and a.version.startswith("sha256:")


def test_translation_assembly_policy():
    ok = {"accepted": 3, "dropped": 1, "needs_review": 0, "rejected": 0, "pending": 0}
    assert evaluate_translation_assembly(ok, total_units=4)[0]
    for bad in ({**ok, "needs_review": 1}, {**ok, "rejected": 1}, {**ok, "pending": 1}):
        assert not evaluate_translation_assembly(bad, total_units=4 + 1)[0], bad
    assert not evaluate_translation_assembly(ok, total_units=5)[0]  # a unit with no result at all
    assert not evaluate_translation_assembly({"accepted": 0, "dropped": 0}, total_units=0)[0]
    passed, metrics = evaluate_translation_assembly(ok, total_units=4)
    assert metrics["total_units"] == 4 and metrics["accepted"] == 3
    assert TRANSLATION_ASSEMBLY_POLICY.kind == "deterministic"


def test_candidate_is_accepted_only_after_its_required_gate_passes(db):
    svc = service(db)
    rec = svc.record_candidate("c1", ref(), inputs=[ref("d" * 64, "translation-units")], required_gates=["g.one"])
    assert rec.status == "candidate" and rec.code_revision == SHA
    assert svc.settle("c1", ref()).status == "candidate"  # nothing decided yet
    with pytest.raises(TransitionRefused):
        svc.promote("c1", ref())
    svc.attach_gate("c1", gate())
    assert svc.settle("c1", ref()).status == "accepted"
    assert [r.artifact.key for r in svc.accepted_only("c1", "translate-book")] == ["b" * 64]
    assert svc.require_accepted("c1", ref()).status == "accepted"


def test_failed_required_gate_rejects(db):
    svc = service(db)
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    svc.attach_gate("c1", gate(passed=False))
    out = svc.settle("c1", ref())
    assert out.status == "rejected" and "g.one" in out.status_reason
    with pytest.raises(NotAccepted):
        svc.require_accepted("c1", ref())


def test_human_gate_keeps_the_output_waiting(db):
    svc = service(db)
    svc.record_candidate("c1", ref(), required_gates=["g.one", "g.human"])
    svc.attach_gate("c1", gate())
    assert svc.settle("c1", ref()).status == "candidate"
    svc.attach_gate("c1", gate(gate_id="g.human", kind="human"))
    assert svc.settle("c1", ref()).status == "accepted"


def test_unknown_revision_never_auto_accepts(db):
    svc = service(db, revision="unknown")
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    svc.attach_gate("c1", gate())
    assert svc.settle("c1", ref()).status == "candidate"


def test_record_candidate_requires_gates_and_is_idempotent(db):
    svc = service(db)
    with pytest.raises(ValueError):
        svc.record_candidate("c1", ref(), required_gates=[])
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    assert len(db.events) == 1


def test_require_accepted_refuses_candidates_quarantined_and_unknown(db):
    svc = service(db)
    with pytest.raises(NotAccepted):
        svc.require_accepted("c1", ref())
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    with pytest.raises(NotAccepted):
        svc.require_accepted("c1", ref())


def test_reject_records_reason(db):
    svc = service(db)
    svc.record_candidate("c1", ref(), required_gates=["g.one"])
    out = svc.reject("c1", ref(), reason="reviewer found omissions", actor="reviewer-1")
    assert out.status == "rejected" and out.status_changed_by == "reviewer-1"
