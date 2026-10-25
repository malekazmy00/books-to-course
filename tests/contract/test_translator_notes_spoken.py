"""Translator notes reach speech and episodes (SCHEMA 1.18.0, D-041).

* ``TranslatorNote.withheld``: why a translation was not offered (it could be fabricated);
* ``SpeechSegment.voice/note_id/note_part``: a note is spoken in the course voice, announcement first;
* ``SpeechTextDocument.notes_ref``: the accepted translator-notes artifact the note segments come from;
* ``ScriptLine.note_id``: an Arabic book-text line that is a translator note (course voice).
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c
from tests.contract.test_episode_script import BOOK, OUTLINE, line, script
from tests.contract.test_translator_notes import note

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = c.ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/",
                       sha256="s" * 64, created_at=NOW)
NOTES = c.ArtifactRef(stage="translator-notes", key="n", uri="s3://artifacts/c1/translator-notes/n/",
                      sha256="n" * 64, created_at=NOW)
LEAD = c.TRANSLATOR_NOTE_LEAD_AR


def test_schema_version_is_1_18_or_later():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 18, 0)


# ------------------------------------------------------------------ withheld translation
def bare_note(**kw):
    return note(best_translation=None, best_translation_label_ar=None, best_translation_from=None,
                reasons=["no_translation", "meaning"], reason_ar="تعذّرت ترجمة هذا العنوان ترجمةً موثوقة.",
                announcement_ar=f"{LEAD} تعذّرت ترجمة هذا العنوان ترجمةً موثوقة.", **kw)


def test_a_withheld_translation_is_not_offered():
    n = c.TranslatorNote(**bare_note(withheld="fabrication_risk"))
    assert n.withheld == "fabrication_risk" and n.best_translation is None
    assert c.TranslatorNote(**note()).withheld is None  # 1.16/1.17 payloads
    with pytest.raises(ValidationError):
        c.TranslatorNote(**note(withheld="critical_meaning"))
    with pytest.raises(ValidationError):
        c.TranslatorNote(**bare_note(withheld="because"))


# ------------------------------------------------------------------ speech
def seg(seq, **kw):
    base = dict(segment_id=f"bk:s{seq:05d}", seq=seq, unit_ids=["bk:u00001"], block_ids=["bk:b00001"],
                structure="paragraph", source_text="نص.", spoken_text="نص.", pause_after_ms=250)
    return c.SpeechSegment(**(base | kw))


def doc(segments, **kw):
    base = dict(book_id="bk", language="ar", frontend_version="speech-text-ar-1", source=SOURCE,
                segments=list(segments))
    return c.SpeechTextDocument(**(base | kw))


def test_old_segments_are_the_author_voice():
    assert seg(0).voice == "author" and seg(0).note_id is None and seg(0).note_part is None


def test_a_note_segment_is_the_course_voice_and_names_its_part():
    s = seg(0, voice="course", note_id="bk:u00001:note", note_part="announcement")
    assert s.voice == "course"
    for bad in (dict(voice="course"), dict(note_id="bk:u00001:note", note_part="announcement"),
                dict(voice="course", note_id="bk:u00001:note"),
                dict(voice="course", note_part="approximate_translation")):
        with pytest.raises(ValidationError):
            seg(0, **bad)


def test_note_segments_need_the_notes_artifact():
    segments = [seg(0, voice="course", note_id="bk:u00001:note", note_part="announcement"),
                seg(1, voice="course", note_id="bk:u00001:note", note_part="approximate_translation")]
    assert doc(segments, notes_ref=NOTES).notes_ref == NOTES
    with pytest.raises(ValidationError, match="notes_ref"):
        doc(segments)
    assert doc([seg(0)]).notes_ref is None


def test_the_announcement_comes_before_the_approximate_translation():
    with pytest.raises(ValidationError, match="announcement"):
        doc([seg(0, voice="course", note_id="bk:u00001:note", note_part="approximate_translation"),
             seg(1, voice="course", note_id="bk:u00001:note", note_part="announcement")], notes_ref=NOTES)


# ------------------------------------------------------------------ episode script
def ar_book_section(lines):
    return c.EpisodeSection(section_id="s02", kind="book_text", voice="author", block_ids=["bk:2:1"],
                            unit_ids=["bk:u00001", "bk:u00002"], lines=lines)


def ar_script(book_lines):
    def ar_line(i, text="نص.", **kw):
        return line(i, text, unit_ids=[f"bk:ep03:u{i:05d}"], status="accepted", **kw)
    from tests.contract.test_episode_script import CONCEPT
    sections = [
        c.EpisodeSection(section_id="s01", kind="episode_intro", voice="course", lines=[ar_line(1, sources=[OUTLINE])]),
        ar_book_section(book_lines),
        c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course", lines=[ar_line(3)]),
        c.EpisodeSection(section_id="s04", kind="key_terms", voice="course", lines=[
            ar_line(4, "الهيمنة (hegemony): قوة.", sources=[CONCEPT, BOOK], role="term", original="hegemony")]),
        c.EpisodeSection(section_id="s05", kind="questions", voice="course", lines=[
            ar_line(10 + j, role="question" if j % 2 == 0 else "answer", pause_after_ms=3000 if j % 2 == 0 else 0)
            for j in range(10)]),
    ]
    return script(language="ar", sections=sections, source_script_sha256="a" * 64)


def test_a_book_text_line_can_be_a_translator_note():
    noted = line(22, f"{LEAD} تعذّر التحقق من دقة ترجمة هذه الفقرة. وما يلي ترجمة تقريبية. نص.", [BOOK],
                 unit_ids=["bk:u00002"], status="needs_review", note_id="bk:u00002:note")
    s = ar_script([line(21, "نص.", [BOOK], unit_ids=["bk:u00001"], status="accepted"), noted])
    assert s.sections[1].lines[1].note_id == "bk:u00002:note"


def test_a_note_line_belongs_to_a_unit_that_was_not_accepted():
    with pytest.raises(ValidationError, match="note"):
        ar_script([line(21, "نص.", [BOOK], unit_ids=["bk:u00001"], status="accepted", note_id="bk:u00001:note")])


def test_only_book_text_lines_of_the_arabic_script_carry_notes():
    with pytest.raises(ValidationError, match="note"):
        c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course",
                         lines=[line(3, unit_ids=["bk:ep03:u00003"], status="needs_review", note_id="x:note")])
