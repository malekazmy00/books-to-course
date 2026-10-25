"""Translator-notes stage (D-040): after translation, every unit is accepted, dropped, or has a note.

The gate ``translator-notes.coverage`` is recomputed from the stored unit results, not trusted from
the notes document, so a unit cannot disappear silently. Same fakes as the translate-book tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from b2c_contracts import ArtifactRef, QAIssue, QEResult, TranslatorNotesDocument
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_translation.qa import QEOutcome
from moto import mock_aws

from b2c_orchestration.promotion import TRANSLATOR_NOTES_POLICY, PromotionService, evaluate_translator_notes
from b2c_orchestration.translate_book import (NOTES_STAGE, AssembleTranslationInput, TranslateBookActivities,
                                              translation_run_id)
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB
from tests.unit.orchestration.test_translate_book_activities import (FakeEngine, FakeRuntime, PassingQE, batch,
                                                                     book_input, parsed_ref, prepare)

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


# ------------------------------------------------------------------ the gate rule
def test_gate_passes_only_when_every_unit_is_publishable_or_annotated():
    decisions = {"u0": "accepted", "u1": "dropped", "u2": "needs_review", "u3": "rejected"}
    passed, metrics = evaluate_translator_notes(decisions, noted=["u2", "u3"])
    assert passed
    assert metrics == {"total_units": 4, "accepted": 1, "dropped": 1, "annotated": 2, "unannotated": 0,
                       "pending": 0, "spurious_notes": 0}


@pytest.mark.parametrize("decisions,noted,failing", [
    ({"u0": "accepted", "u1": "needs_review"}, [], "unannotated"),          # lost silently
    ({"u0": "accepted", "u1": "rejected"}, [], "unannotated"),
    ({"u0": "accepted", "u1": None}, [], "pending"),                         # no result yet
    ({"u0": "accepted", "u1": "needs_review"}, ["u1", "u0"], "spurious_notes"),  # note on an accepted unit
    ({"u0": "accepted"}, ["u9"], "spurious_notes"),                          # note on an unknown unit
])
def test_gate_fails_when_a_unit_is_unaccounted_for(decisions, noted, failing):
    passed, metrics = evaluate_translator_notes(decisions, noted=noted)
    assert not passed and metrics[failing] >= 1


def test_gate_refuses_an_empty_book():
    assert not evaluate_translator_notes({}, noted=[])[0]
    assert TRANSLATOR_NOTES_POLICY.kind == "deterministic"
    assert TRANSLATOR_NOTES_POLICY.gate_id == "translator-notes.coverage"


# ------------------------------------------------------------------ the activity
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


class MeaningDoubtQE(PassingQE):
    """The semantic judge doubts one unit: it ends needs_review after its attempts."""

    def estimate(self, unit, target):
        if unit.unit_id == "bk:u00003":
            return QEOutcome(passed=False, result=QEResult(deterministic_pass=True, route="human_review"),
                             issues=[QAIssue(check_id="llm_mqm.meaning_error", category="meaning_error",
                                             severity="major", origin="qe")])
        return super().estimate(unit, target)


def run(artifacts, results, promo, qe=PassingQE(), ids=None):
    acts = TranslateBookActivities(artifact_store=artifacts, result_store=results,
                                   runtime=FakeRuntime(FakeEngine(), qe), promotion=promo)
    inp = book_input(parsed_ref(artifacts))
    prepared = prepare(acts, inp)
    acts.translate_batch(batch(inp, prepared, ids or prepared.unit_ids))
    return acts, AssembleTranslationInput(run_id=translation_run_id(inp), course_id="c1",
                                          units_ref=prepared.units_ref)


def test_a_doubtful_unit_becomes_a_note_and_the_notes_stage_is_accepted(artifacts, results, registry_db):
    promo = promotion(registry_db)
    acts, inp = run(artifacts, results, promo, qe=MeaningDoubtQE())
    assert acts.assemble(inp).status == "rejected"  # the strict assembly gate is unchanged
    out = acts.translator_notes(inp)
    assert out.status == "accepted"
    assert out.counts == {"total_units": 5, "accepted": 3, "dropped": 1, "annotated": 1, "unannotated": 0,
                          "pending": 0, "spurious_notes": 0}
    ref = ArtifactRef.model_validate(out.artifact)
    assert ref.stage == NOTES_STAGE
    doc = TranslatorNotesDocument.model_validate_json(artifacts.read(ref, "translator-notes.json"))
    [note] = doc.notes
    assert note.unit_id == "bk:u00003" and note.reasons == ["meaning"] and note.best_translation
    assert note.source_text.startswith("Rice grows best")
    record = promo.require_accepted("c1", ref)
    assert record.inputs == [ArtifactRef.model_validate(inp.units_ref)]
    gate = record.latest_gate_results()[TRANSLATOR_NOTES_POLICY.gate_id]
    assert gate.passed and gate.decider == "system" and gate.metrics["annotated"] == 1
    # same results -> same artifact, no second gate row
    again = acts.translator_notes(inp)
    assert again.artifact == out.artifact and again.cache_hit and len(registry_db.gates) == 2


def test_units_without_a_result_keep_the_notes_stage_from_being_accepted(artifacts, results, registry_db):
    promo = promotion(registry_db)
    acts, inp = run(artifacts, results, promo, ids=["bk:u00000", "bk:u00001"])
    out = acts.translator_notes(inp)
    assert out.status == "rejected" and out.counts["pending"] == 3
    doc = TranslatorNotesDocument.model_validate_json(
        artifacts.read(ArtifactRef.model_validate(out.artifact), "translator-notes.json"))
    assert doc.unannotated_unit_ids == ["bk:u00002", "bk:u00003", "bk:u00004"]


def test_without_a_registry_notes_are_still_written(artifacts, results):
    acts, inp = run(artifacts, results, None, qe=MeaningDoubtQE())
    out = acts.translator_notes(inp)
    assert out.status is None and out.counts["annotated"] == 1
    body = json.loads(artifacts.read(ArtifactRef.model_validate(out.artifact), "translator-notes.json"))
    assert body["format"] == "b2c.translator-notes/1"
