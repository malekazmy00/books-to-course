"""TranslatorNote / TranslatorNotesDocument (SCHEMA 1.16.0, D-040): transparent failure.

A unit that was not accepted is never hidden and never blocks the book: it becomes a note in
place, in the course voice, with the best available translation (labelled approximate) and the
original text. The document accounts for every unit of the book.
"""
import pytest
from pydantic import ValidationError

import b2c_contracts as c

LEAD = c.TRANSLATOR_NOTE_LEAD_AR


def note(**kw) -> dict:
    data = dict(note_id="bk:u00003:note", unit_id="bk:u00003", book_id="bk", block_ids=["bk:1:3"], pages=[1],
                section_path=["Chapter One"], unit_kind="prose", unit_decision="needs_review",
                reasons=["meaning"], reason_ar="قد لا تنقل ترجمة هذه الفقرة معنى الأصل بدقة.",
                source_text="The yield rose by 12 percent.",
                best_translation="ارتفع المحصول بنسبة 12 في المئة.",
                best_translation_label_ar=c.BEST_TRANSLATION_LABEL_AR, best_translation_from="final",
                announcement_ar=f"{LEAD} قد لا تنقل ترجمة هذه الفقرة معنى الأصل بدقة. وما يلي ترجمة تقريبية.")
    data.update(kw)
    return data


def test_schema_version_is_1_16_or_later():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 16, 0)


def test_a_note_speaks_in_the_course_voice_and_keeps_the_original():
    n = c.TranslatorNote(**note())
    assert n.voice == "course" and n.source_language == "en"
    assert n.announcement_ar.startswith(LEAD)
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(voice="author"))


def test_the_announcement_must_open_with_the_course_lead():
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(announcement_ar="قد لا تنقل ترجمة هذه الفقرة معنى الأصل بدقة."))


@pytest.mark.parametrize("field", ["announcement_ar", "reason_ar"])
def test_learner_facing_text_has_no_latin_jargon(field):
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(**{field: f"{LEAD} MQM major meaning_error."}))


def test_best_translation_comes_with_its_label_and_origin_or_not_at_all():
    for missing in ("best_translation_label_ar", "best_translation_from"):
        with pytest.raises(ValidationError):
            c.TranslatorNote(**note(**{missing: None}))
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(best_translation="   "))


def test_no_translation_reason_iff_there_is_no_best_translation():
    bare = note(best_translation=None, best_translation_label_ar=None, best_translation_from=None,
                unit_decision="rejected", reasons=["no_translation"],
                reason_ar="تعذّرت ترجمة هذه الفقرة ترجمة موثوقة.",
                announcement_ar=f"{LEAD} تعذّرت ترجمة هذه الفقرة ترجمة موثوقة.")
    assert c.TranslatorNote(**bare).best_translation is None
    with pytest.raises(ValidationError):  # no translation but the reason does not say so
        c.TranslatorNote(**{**bare, "reasons": ["meaning"]})
    with pytest.raises(ValidationError):  # a translation is offered but the reason says there is none
        c.TranslatorNote(**note(reasons=["no_translation"]))


def test_reasons_are_unique_and_not_empty():
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(reasons=[]))
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(reasons=["meaning", "meaning"]))


def test_accepted_or_dropped_units_do_not_get_notes():
    for decision in ("accepted", "dropped"):
        with pytest.raises(ValidationError):
            c.TranslatorNote(**note(unit_decision=decision))


def document(**kw) -> dict:
    data = dict(book_id="bk", run_id="f" * 64, builder_version="translator-notes-test", total_units=5,
                accepted=3, dropped=1, annotated=1, pending=0, notes=[note()], unannotated_unit_ids=[])
    data.update(kw)
    return data


def test_the_document_accounts_for_every_unit():
    doc = c.TranslatorNotesDocument(**document())
    assert doc.format == "b2c.translator-notes/1"
    with pytest.raises(ValidationError):  # one unit unaccounted for
        c.TranslatorNotesDocument(**document(total_units=6))
    with pytest.raises(ValidationError):  # annotated count disagrees with the notes
        c.TranslatorNotesDocument(**document(annotated=2, accepted=2))
    with pytest.raises(ValidationError):  # pending count disagrees with the listed ids
        c.TranslatorNotesDocument(**document(pending=1, accepted=2))


def test_pending_units_are_listed_and_never_also_annotated():
    doc = c.TranslatorNotesDocument(**document(total_units=6, pending=1, unannotated_unit_ids=["bk:u00004"]))
    assert doc.unannotated_unit_ids == ["bk:u00004"]
    with pytest.raises(ValidationError):
        c.TranslatorNotesDocument(**document(total_units=6, pending=1, unannotated_unit_ids=["bk:u00003"]))


def test_one_note_per_unit_and_same_book():
    with pytest.raises(ValidationError):
        c.TranslatorNotesDocument(**document(notes=[note(), note()], annotated=2, accepted=2))
    with pytest.raises(ValidationError):
        c.TranslatorNotesDocument(**document(notes=[note(book_id="other")]))
