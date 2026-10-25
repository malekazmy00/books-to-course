"""Stage acceptance contracts (R1, audit 08 C1): GateResult + the stage-output status lifecycle.

Every test that builds an invalid object is a negative control that MUST raise.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40
KEY = "b" * 64
REF = c.ArtifactRef(stage="translate-book", key=KEY, uri=f"s3://artifacts/c1/translate-book/{KEY}/",
                    sha256="c" * 64, created_at=NOW)
PARENT = c.ArtifactRef(stage="translation-units", key="d" * 64, uri=f"s3://artifacts/c1/translation-units/{'d' * 64}/",
                       sha256="e" * 64, created_at=NOW)


def gate(**kw) -> c.GateResult:
    base = dict(stage="translate-book", artifact=REF, gate_id="translate-book.unit-decisions", gate_version="v1",
                kind="deterministic", passed=True, metrics={"pending": 0}, evidence_ref=REF.uri,
                decider="system", decided_by="policy:translate-book.unit-decisions", decided_at=NOW)
    base.update(kw)
    return c.GateResult(**base)


def record(**kw) -> c.StageOutputRecord:
    base = dict(course_id="c1", artifact=REF, inputs=[PARENT], code_revision=SHA, config={"run_id": "r"},
                identities=[c.ProducerIdentity(role="engine", model_id="m", revision="r1", prompt_version="p@1")],
                required_gates=["translate-book.unit-decisions"], gate_results=[gate()], status="candidate",
                status_changed_at=NOW, status_changed_by="system:test")
    base.update(kw)
    return c.StageOutputRecord(**base)


def test_schema_version_is_bumped_for_the_acceptance_contracts():
    assert c.SCHEMA_VERSION != "1.2.1"


# ------------------------------------------------------------------ GateResult
def test_gate_result_roundtrips():
    g = gate()
    assert c.GateResult.model_validate_json(g.model_dump_json()) == g


def test_gate_result_stage_must_match_its_artifact():
    with pytest.raises(ValidationError):
        gate(stage="translate-content")


def test_gate_result_requires_utc():
    with pytest.raises(ValidationError):
        gate(decided_at=datetime(2026, 10, 25, 12))


def test_human_gate_needs_a_human_decision():
    with pytest.raises(ValidationError):
        gate(kind="human", decider="system", decided_by="policy:x")
    assert gate(kind="human", decider="human", decided_by="reviewer-1").passed


def test_machine_gates_are_not_decided_by_a_human():
    with pytest.raises(ValidationError):
        gate(kind="semantic", decider="human", decided_by="reviewer-1")


def test_gate_id_and_version_are_required():
    with pytest.raises(ValidationError):
        gate(gate_id="")
    with pytest.raises(ValidationError):
        gate(gate_version="")


# ------------------------------------------------------------------ StageOutputRecord
def test_candidate_record_roundtrips():
    r = record()
    assert c.StageOutputRecord.model_validate_json(r.model_dump_json()) == r
    assert r.stage == "translate-book" and r.stage_key == KEY


def test_accepted_requires_every_required_gate_passed():
    assert record(status="accepted").status == "accepted"
    with pytest.raises(ValidationError):
        record(status="accepted", gate_results=[gate(passed=False)])
    with pytest.raises(ValidationError):  # a required gate with no result at all
        record(status="accepted", required_gates=["translate-book.unit-decisions", "translate-book.human-sample"])


def test_accepted_requires_at_least_one_required_gate():
    with pytest.raises(ValidationError):
        record(status="accepted", required_gates=[], gate_results=[])


def test_the_latest_result_of_a_gate_decides():
    earlier_fail = gate(passed=False, decided_at=NOW - timedelta(minutes=5))
    assert record(status="accepted", gate_results=[earlier_fail, gate()]).status == "accepted"
    later_fail = gate(passed=False, decided_at=NOW + timedelta(minutes=5))
    with pytest.raises(ValidationError):
        record(status="accepted", gate_results=[gate(), later_fail])


def test_required_human_gate_needs_a_human_decision():
    human = gate(gate_id="translate-book.human-sample", kind="human", decider="human", decided_by="reviewer-1")
    ok = record(status="accepted", required_gates=["translate-book.unit-decisions", "translate-book.human-sample"],
                gate_results=[gate(), human])
    assert ok.status == "accepted"


def test_gate_results_must_belong_to_the_record_artifact():
    other = c.ArtifactRef(stage="translate-book", key="f" * 64, uri=f"s3://artifacts/c1/translate-book/{'f' * 64}/",
                          sha256="0" * 64, created_at=NOW)
    with pytest.raises(ValidationError):
        record(gate_results=[gate(artifact=other)])


def test_accepted_requires_a_clean_code_revision():  # P8 / C3
    with pytest.raises(ValidationError):
        record(status="accepted", code_revision="unknown")
    with pytest.raises(ValidationError):
        record(status="accepted", code_revision=SHA + "-dirty")
    assert record(code_revision="unknown").status == "candidate"


def test_code_revision_format_is_checked():
    with pytest.raises(ValidationError):
        record(code_revision="main")


def test_quarantined_and_rejected_need_a_reason():
    with pytest.raises(ValidationError):
        record(status="quarantined")
    with pytest.raises(ValidationError):
        record(status="rejected")
    assert record(status="quarantined", status_reason="legacy pre-gate", code_revision="unknown",
                  inputs=[], gate_results=[], required_gates=[]).status == "quarantined"


def test_lifecycle_transitions_are_explicit():
    allowed = c.STAGE_OUTPUT_TRANSITIONS
    assert allowed["candidate"] == frozenset({"accepted", "rejected"})
    assert "accepted" not in allowed["rejected"]
    assert allowed["quarantined"] == frozenset({"candidate"})  # legacy output is re-gated, never promoted directly
    assert allowed["accepted"] == frozenset({"rejected"})  # revocation only
