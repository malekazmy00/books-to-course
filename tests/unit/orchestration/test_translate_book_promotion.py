"""translate-book v2 assemblies go through the promotion API (R1): candidate -> gate -> accepted|rejected.

Same fakes as test_translate_book_activities (moto S3, fake Postgres tables, fake engine/QE).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import boto3
import pytest
from b2c_contracts import ArtifactRef, QAIssue, QEResult
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_translation.qa import QEOutcome
from moto import mock_aws

from b2c_orchestration.promotion import TRANSLATION_ASSEMBLY_POLICY, PromotionService
from b2c_orchestration.translate_book import AssembleTranslationInput, TranslateBookActivities, translation_run_id
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB
from tests.unit.orchestration.test_translate_book_activities import (IDENTITY, FakeEngine, FakeRuntime, PassingQE,
                                                                     batch, book_input, parsed_ref, prepare)

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


@pytest.fixture
def artifacts():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        store.ensure_bucket()
        yield store


@pytest.fixture
def results():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect,
                                  clock=lambda: next(clock))


@pytest.fixture
def registry_db():
    return FakeRegistryDB()


def promotion(registry_db, revision=SHA):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=registry_db.connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision=revision, clock=lambda: NOW)


def acts_with(artifacts, results, promo, qe=PassingQE()):
    return TranslateBookActivities(artifact_store=artifacts, result_store=results,
                                   runtime=FakeRuntime(FakeEngine(), qe), promotion=promo)


class ReviewOneQE(PassingQE):
    def estimate(self, unit, target):
        if unit.unit_id == "bk:u00003":
            return QEOutcome(passed=False, result=QEResult(deterministic_pass=True, route="human_review"),
                             issues=[QAIssue(check_id="qe", category="meaning_error", severity="major", origin="qe")])
        return super().estimate(unit, target)


def run_book(acts, artifacts):
    inp = book_input(parsed_ref(artifacts))
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, prepared.unit_ids))
    assemble = AssembleTranslationInput(run_id=translation_run_id(inp), course_id="c1", units_ref=prepared.units_ref)
    return prepared, assemble


def test_fully_decided_assembly_is_accepted_with_lineage(artifacts, results, registry_db):
    promo = promotion(registry_db)
    acts = acts_with(artifacts, results, promo)
    prepared, assemble = run_book(acts, artifacts)
    out = acts.assemble(assemble)
    assert out.status == "accepted"
    ref = ArtifactRef.model_validate(out.artifact)
    record = promo.require_accepted("c1", ref)
    assert record.inputs == [ArtifactRef.model_validate(prepared.units_ref)]
    assert record.code_revision == SHA and record.config["run_id"] == assemble.run_id
    assert record.identities[0].model_id == IDENTITY.model_id and record.identities[0].role == "engine"
    gate = record.latest_gate_results()[TRANSLATION_ASSEMBLY_POLICY.gate_id]
    assert gate.passed and gate.gate_version == TRANSLATION_ASSEMBLY_POLICY.version
    assert gate.metrics["total_units"] == 5 and gate.metrics["needs_review"] == 0
    # unchanged results -> same artifact, no second gate row or status event
    again = acts.assemble(assemble)
    assert again.artifact == out.artifact and again.status == "accepted"
    assert len(registry_db.gates) == 1 and len(registry_db.events) == 2


def test_assembly_with_units_awaiting_review_is_rejected(artifacts, results, registry_db):
    promo = promotion(registry_db)
    acts = acts_with(artifacts, results, promo, qe=ReviewOneQE())
    _, assemble = run_book(acts, artifacts)
    out = acts.assemble(assemble)
    assert out.status == "rejected"
    assert promo.accepted_only("c1", "translate-book") == []


def test_unstamped_worker_output_stays_candidate(artifacts, results, registry_db):
    acts = acts_with(artifacts, results, promotion(registry_db, revision="unknown"))
    _, assemble = run_book(acts, artifacts)
    assert acts.assemble(assemble).status == "candidate"


def test_without_a_registry_assembly_behaves_as_before(artifacts, results):
    acts = acts_with(artifacts, results, None)
    _, assemble = run_book(acts, artifacts)
    assert acts.assemble(assemble).status is None
