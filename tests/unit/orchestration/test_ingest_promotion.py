"""Ingest v2 output (ingest-parse-v2) is a promotion candidate decided by the ingest quality gate
(D-043): accepted only when the deterministic report is accepted, rejected otherwise."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from b2c_contracts import ArtifactRef, IngestQualityReport, QualityCheck
from b2c_core.stage_registry import StageOutputRegistry
from b2c_ingest.quality import GATE_ID
from b2c_orchestration.promotion import INGEST_QUALITY_POLICY, PromotionService
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


def service():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision=SHA, clock=lambda: NOW)


def parsed_ref(key="b" * 64):
    return ArtifactRef(stage="ingest-parse-v2", key=key, uri=f"s3://artifacts/course/ingest-parse-v2/{key}/",
                       sha256="c" * 64, created_at=NOW)


def report(passed: bool) -> IngestQualityReport:
    value = 0.0 if passed else 3.0
    return IngestQualityReport(
        book_id="bk", report_version="2", metrics={"pages_below_agreement": value},
        checks=[QualityCheck(name="pages_below_agreement", value=value, op="<=", threshold=0, passed=passed)],
        accepted=passed, page_findings={} if passed else {"pages_below_agreement": [3, 9, 11]})


def test_policy_is_the_ingest_gate():
    assert INGEST_QUALITY_POLICY.gate_id == GATE_ID == "ingest-v2.quality"
    assert INGEST_QUALITY_POLICY.kind == "deterministic"


def test_accepted_report_promotes_the_parsed_book():
    promo = service()
    record = promo.gate_ingest_parse("course", parsed_ref(), report=report(True), inputs=[])
    assert record.status == "accepted" and record.required_gates == [GATE_ID]
    assert promo.require_accepted("course", parsed_ref()).status == "accepted"


def test_failed_report_rejects_with_the_pages_to_review():
    record = service().gate_ingest_parse("course", parsed_ref(), report=report(False), inputs=[])
    assert record.status == "rejected" and GATE_ID in record.status_reason
    gate = record.latest_gate_results()[GATE_ID]
    assert gate.passed is False and gate.metrics["failed_checks"] == ["pages_below_agreement"]
    assert gate.metrics["page_findings"] == {"pages_below_agreement": [3, 9, 11]}
    assert gate.evidence_ref.endswith("ingest-quality.json")
