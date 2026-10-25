"""Outline v2 activity and its promotion (R4 + R1). There is no human reviewer in this product (owner
decision 2026-10-25): an outline is accepted when the structure gate AND the independent cross-check
pass, both as system GateResults; disagreeing sources are a recorded flag, lost coverage rejects.
moto S3, the fake stage-output registry, ParsedBook v2 fixtures."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

import b2c_contracts as c
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_orchestration import outline_v2 as ov2
from b2c_orchestration.outline_v2 import OUTLINE_V2_STAGE, BuildOutlineV2Input, OutlineV2Activities
from b2c_orchestration.promotion import (OUTLINE_CROSS_CHECK_POLICY, OUTLINE_REQUIRED_GATES,
                                         OUTLINE_STRUCTURE_POLICY, PromotionService)
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.outline.v2_fixtures import book, gramsci_like, para, sat_like

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "b" * 40


@pytest.fixture
def store():
    with mock_aws():
        s = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        s.ensure_bucket()
        yield s


def promotion(db, revision=SHA):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision=revision, clock=lambda: NOW)


def put_parsed(store, parsed: c.ParsedBookV2) -> c.ArtifactRef:
    v2 = parsed.model_dump_json().encode()
    v1 = c.parsed_book_v1_from_v2(parsed)
    v1 = v1.model_copy(update={"parser": {**v1.parser, "source_file_sha256": "e" * 64}})
    return store.put("crs", "ingest-parse-v2", hashlib.sha256(v2).hexdigest(),
                     {"parsed-book-v2.json": v2, "parsed-book.json": v1.model_dump_json().encode()})


def run(acts, parsed_ref):
    return ActivityEnvironment().run(acts.build, BuildOutlineV2Input("crs", parsed_ref.model_dump(mode="json"), []))


def test_activity_stores_v2_v1_projection_gate_and_cross_check_and_accepts_automatically(store):
    db = FakeRegistryDB()
    parsed = sat_like()
    parsed_ref = put_parsed(store, parsed)
    acts = OutlineV2Activities(store=store, promotion=promotion(db))
    out = run(acts, parsed_ref)
    ref = c.ArtifactRef.model_validate(out["ref"])
    assert ref.stage == OUTLINE_V2_STAGE and out["format"] == "course-outline/2"
    assert sorted(store.list_files(ref)) == ["course-outline-v2.json", "course-outline.json", "outline-crosscheck.json",
                                             "outline-quality.json", "source-identity.json"]
    v2 = c.CourseOutlineV2.model_validate_json(store.read(ref, "course-outline-v2.json"))
    v1 = c.CourseOutline.model_validate_json(store.read(ref, "course-outline.json"))
    assert v1 == c.course_outline_v1_from_v2(v2, parsed)
    assert out["gate"] == {"accepted": True, "failed": []} and out["cross_check"]["flags"] == []
    assert out["stage_output_status"] == "accepted" and out["requires_review"] is False
    record = acts.promotion.registry.get("crs", ref.stage, ref.key)
    assert record.required_gates == list(OUTLINE_REQUIRED_GATES) and record.inputs == [parsed_ref]
    latest = record.latest_gate_results()
    assert set(latest) == set(OUTLINE_REQUIRED_GATES)
    assert all(g.passed and g.decider == "system" and g.kind == "deterministic" for g in latest.values())
    again = run(acts, parsed_ref)
    assert again["cache_hit"] is True and again["ref"] == out["ref"] and again["stage_output_status"] == "accepted"


def test_disagreeing_sources_are_accepted_with_the_flag_recorded(store):
    db = FakeRegistryDB()
    parsed = gramsci_like()
    blocks = list(parsed.blocks)
    blocks[5] = blocks[5].model_copy(update={"text": "Conclusion 7", "raw_text": "Conclusion 7"})
    acts = OutlineV2Activities(store=store, promotion=promotion(db))
    out = run(acts, put_parsed(store, parsed.model_copy(update={"blocks": blocks})))
    assert out["stage_output_status"] == "accepted" and out["cross_check"]["flags"] == ["printed_toc_disagrees"]
    ref = c.ArtifactRef.model_validate(out["ref"])
    gate = acts.promotion.registry.get("crs", ref.stage, ref.key).latest_gate_results()[OUTLINE_CROSS_CHECK_POLICY.gate_id]
    assert gate.passed and gate.metrics["flags"] == ["printed_toc_disagrees"]
    assert "bookmarks > printed TOC > layout headings" in gate.metrics["reason"]
    stored = json.loads(store.read(ref, "outline-crosscheck.json"))
    assert stored["flags"] == ["printed_toc_disagrees"]


def test_failed_structure_gate_rejects_with_a_reason(store, monkeypatch):
    db = FakeRegistryDB()
    real = ov2.outline_quality

    def failing(book, outline, **kw):
        report = real(book, outline, **kw)
        checks = [ch.model_copy(update={"value": 2.0, "passed": False}) if ch.name == "malformed_titles" else ch
                  for ch in report.checks]
        return report.model_copy(update={"checks": checks, "accepted": False})

    monkeypatch.setattr(ov2, "outline_quality", failing)
    acts = OutlineV2Activities(store=store, promotion=promotion(db))
    out = run(acts, put_parsed(store, sat_like()))
    assert out["gate"]["failed"] == ["malformed_titles"] and out["stage_output_status"] == "rejected"
    ref = c.ArtifactRef.model_validate(out["ref"])
    record = acts.promotion.registry.get("crs", ref.stage, ref.key)
    assert OUTLINE_STRUCTURE_POLICY.gate_id in record.status_reason


def test_lost_coverage_fails_the_cross_check_and_rejects(store, monkeypatch):
    db = FakeRegistryDB()
    real = ov2.cross_check
    monkeypatch.setattr(ov2, "cross_check", lambda b, o, r: real(b, o, r.model_copy(
        update={"metrics": {**r.metrics, "coverage_ratio": 0.97}})))
    acts = OutlineV2Activities(store=store, promotion=promotion(db))
    out = run(acts, put_parsed(store, sat_like()))
    assert out["cross_check"]["passed"] is False and out["stage_output_status"] == "rejected"


def test_unknown_or_dirty_build_stays_a_candidate(store):
    acts = OutlineV2Activities(store=store, promotion=promotion(FakeRegistryDB(), revision="unknown"))
    assert run(acts, put_parsed(store, sat_like()))["stage_output_status"] == "candidate"


def test_a_book_without_structure_cannot_be_outlined(store):
    acts = OutlineV2Activities(store=store)
    with pytest.raises(ApplicationError) as err:
        run(acts, put_parsed(store, book([para(1, 800), para(2, 800)])))
    assert err.value.type == "OutlineNeedsReview" and err.value.non_retryable


def test_without_a_registry_the_activity_still_builds(store):
    out = run(OutlineV2Activities(store=store), put_parsed(store, sat_like()))
    assert out["stage_output_status"] is None and out["lesson_count"] == 5
