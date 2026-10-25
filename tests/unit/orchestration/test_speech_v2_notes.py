"""Speech v2 with translator notes (D-041): an explicit second acceptance path.

The strict path is unchanged: without ``notes_ref`` the translate-book assembly must be accepted.
With ``notes_ref`` the accepted ``translator-notes`` artifact (gate ``translator-notes.coverage``)
replaces that requirement: every unit is accepted, dropped, or annotated, and the notes must be
built from the unit results as they are now.
"""
from __future__ import annotations

import json

import pytest
from b2c_contracts import ArtifactRef
from temporalio.exceptions import ApplicationError

from b2c_orchestration.translate_book import AssembleTranslationInput, TranslateBookActivities
from tests.unit.orchestration.test_speech_v2 import (RUN, acts, assembly, promo, results, speech_input, store,  # noqa: F401
                                                     translation)

LEX = {"Gramsci": "غرامشي"}


def notes(store, results, promo, out):
    units_ref = json.loads(store.read(ArtifactRef.model_validate(out.artifact), "translation.json"))["units_ref"]
    a = TranslateBookActivities(artifact_store=store, result_store=results, runtime=None, promotion=promo)
    return a.translator_notes(AssembleTranslationInput(run_id=RUN, course_id="c1", units_ref=units_ref))


def test_a_book_with_annotated_units_reaches_speech_through_its_notes(store, results, promo):
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    assert out.status == "rejected"  # the strict assembly gate is unchanged
    n = notes(store, results, promo, out)
    assert n.status == "accepted"
    a = acts(store, results, promo)
    prepared = a.prepare(speech_input(out, notes_ref=n.artifact, lexicon_overrides=LEX))
    assert prepared.status == "accepted" and prepared.gate_passed
    doc = a._doc(ArtifactRef.model_validate(prepared.speech_text_ref))
    noted = [s for s in doc.segments if s.unit_ids == ["bk:u00002"]]
    assert noted[0].note_part == "announcement" and noted[0].spoken_text.startswith("ملاحظة من الكورس")
    assert noted[-1].note_part == "approximate_translation" and all(s.voice == "course" for s in noted)
    assert "percent" not in " ".join(s.spoken_text for s in noted)
    assert doc.notes_ref == ArtifactRef.model_validate(n.artifact)
    record = promo.require_accepted("c1", ArtifactRef.model_validate(prepared.speech_text_ref))
    assert [r.stage for r in record.inputs] == ["translate-book", "translator-notes"]
    assert record.config["acceptance_path"] == "translator-notes"


def test_without_notes_the_strict_path_still_refuses(store, results, promo):
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    notes(store, results, promo, out)
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, lexicon_overrides=LEX))
    assert err.value.type == "NotAcceptedTranslation"


def test_notes_that_are_not_accepted_are_refused(store, results, promo):
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    unregistered = notes(store, results, None, out)  # built without the registry: never accepted
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, notes_ref=unregistered.artifact, lexicon_overrides=LEX))
    assert err.value.type == "NotAcceptedTranslation"


def test_notes_built_on_older_results_are_refused(store, results, promo):
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    n = notes(store, results, promo, out)
    results.upsert(RUN, "bk", translation(4, "needs_review"))  # a unit changed after the notes were built
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, notes_ref=n.artifact, lexicon_overrides=LEX))
    assert err.value.type == "TranslationChanged"


def test_notes_of_another_stage_are_refused(store, results, promo):
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, notes_ref=out.artifact, lexicon_overrides=LEX))
    assert err.value.type == "InvalidSpeechInput"
