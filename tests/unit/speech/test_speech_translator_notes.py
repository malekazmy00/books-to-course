"""Translator notes in the speech text (D-041): a unit that was not accepted but carries a note is
spoken in the course voice — the announcement, then the approximate translation (if one is offered).
The English original is never spoken. A unit without a result, or not accepted and without a note,
still refuses the build.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from b2c_contracts import (BEST_TRANSLATION_LABEL_AR, TRANSLATOR_NOTE_LEAD_AR, ArtifactRef, EngineIdentity, QEResult,
                           TranslationUnit, TranslatorNote, UnitTranslation)

from b2c_speech.text import NotSpeechReady, PronunciationLexicon, build_speech_text, evaluate_speech_text

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/",
                     sha256="s" * 64, created_at=NOW)
NOTES = ArtifactRef(stage="translator-notes", key="n", uri="s3://artifacts/c1/translator-notes/n/",
                    sha256="n" * 64, created_at=NOW)
ENGINE = EngineIdentity(provider="fake", model_id="m", prompt_version="p@1")
TEXTS = [("heading", "Chapter One", "الفصل الأول"),
         ("prose", "The yield rose by two dozen percent in that decade.", "ارتفع المحصول في ذلك العقد."),
         ("prose", "Analysts use the technique daily.", "يستخدم المحللون هذه التقنية يوميًا."),
         ("reference", "1. J. Smith, Forecasting (2001).", "1. J. Smith, Forecasting (2001).")]
ANNOUNCE = f"{TRANSLATOR_NOTE_LEAD_AR} قد يختلف رقم أو تاريخ أو إحالة في ترجمة هذه الفقرة عن الأصل. وما يلي ترجمة تقريبية."


def units():
    return [TranslationUnit(unit_id=f"bk:u{i:05d}", book_id="bk", block_ids=[f"bk:b{i:05d}"], kind=k,
                            source_text=s, pages=[1], section_path=["Ch"]) for i, (k, s, _) in enumerate(TEXTS)]


def translations(**decisions):
    out = {}
    for i, (_, _, t) in enumerate(TEXTS):
        d = decisions.get(f"u{i}", "accepted")
        out[f"bk:u{i:05d}"] = UnitTranslation(
            unit_id=f"bk:u{i:05d}", target_text=t, origin="engine", engine=ENGINE,
            qe=QEResult(deterministic_pass=True, route="auto_accept") if d == "accepted" else None,
            decision=d, decided_by="policy", decided_at=NOW)
    return out


def note(i, *, best=True, kind="prose"):
    base = dict(note_id=f"bk:u{i:05d}:note", unit_id=f"bk:u{i:05d}", book_id="bk", block_ids=[f"bk:b{i:05d}"],
                pages=[1], unit_kind=kind, unit_decision="needs_review", source_text=TEXTS[i][1])
    if best:
        return TranslatorNote(**base, reasons=["numbers"],
                              reason_ar="قد يختلف رقم أو تاريخ أو إحالة في ترجمة هذه الفقرة عن الأصل.",
                              best_translation=TEXTS[i][2], best_translation_label_ar=BEST_TRANSLATION_LABEL_AR,
                              best_translation_from="final", announcement_ar=ANNOUNCE)
    text = f"{TRANSLATOR_NOTE_LEAD_AR} تعذّرت ترجمة هذه الفقرة ترجمةً موثوقة. والنص الأصلي محفوظ في النسخة المكتوبة من الكورس."
    return TranslatorNote(**base, reasons=["no_translation"], reason_ar="تعذّرت ترجمة هذه الفقرة ترجمةً موثوقة.",
                          withheld="fabrication_risk", announcement_ar=text)


def build(trs, notes=None):
    return build_speech_text(units(), trs, book_id="bk", source=SOURCE, lexicon=PronunciationLexicon(),
                             notes=notes, notes_ref=NOTES if notes is not None else None)


def test_a_noted_unit_is_spoken_announcement_first_in_the_course_voice():
    doc = build(translations(u1="needs_review"), {"bk:u00001": note(1)})
    assert doc.notes_ref == NOTES
    noted = [s for s in doc.segments if s.unit_ids == ["bk:u00001"]]
    assert [s.note_part for s in noted][0] == "announcement" and noted[-1].note_part == "approximate_translation"
    assert all(s.voice == "course" and s.note_id == "bk:u00001:note" for s in noted)
    said = " ".join(s.spoken_text for s in noted)
    assert said.startswith("ملاحظة من الكورس") and "ارتفع المحصول في ذلك العقد" in said
    assert "dozen" not in said and "yield" not in said  # the English original is not read
    others = [s for s in doc.segments if s.unit_ids != ["bk:u00001"]]
    assert others and all(s.voice == "author" and s.note_id is None for s in others)
    assert evaluate_speech_text(doc)[0]


def test_without_an_offered_translation_only_the_announcement_is_spoken():
    doc = build(translations(u1="rejected"), {"bk:u00001": note(1, best=False)})
    noted = [s for s in doc.segments if s.unit_ids == ["bk:u00001"]]
    assert noted and {s.note_part for s in noted} == {"announcement"}
    assert "ارتفع" not in " ".join(s.spoken_text for s in noted)


def test_a_unit_not_accepted_and_without_a_note_still_refuses_the_build():
    with pytest.raises(NotSpeechReady):
        build(translations(u1="needs_review", u2="needs_review"), {"bk:u00001": note(1)})
    with pytest.raises(NotSpeechReady):
        build(translations(u1="needs_review"))


def test_a_unit_without_a_result_refuses_the_build_even_with_notes():
    trs = translations(u1="needs_review")
    del trs["bk:u00002"]
    with pytest.raises(NotSpeechReady):
        build(trs, {"bk:u00001": note(1)})


def test_notes_on_apparatus_are_not_spoken():
    doc = build(translations(u3="needs_review"), {"bk:u00003": note(3, kind="reference")})
    assert {e.unit_id: e.reason for e in doc.excluded}["bk:u00003"] == "not_spoken_kind:reference"
    assert not any(s.note_id for s in doc.segments)


def test_a_note_must_belong_to_a_unit_that_was_not_accepted():
    with pytest.raises(ValueError, match="accepted"):
        build(translations(), {"bk:u00001": note(1)})


def test_note_segments_need_the_notes_artifact():
    with pytest.raises(ValueError, match="notes_ref"):
        build_speech_text(units(), translations(u1="needs_review"), book_id="bk", source=SOURCE,
                          lexicon=PronunciationLexicon(), notes={"bk:u00001": note(1)})
