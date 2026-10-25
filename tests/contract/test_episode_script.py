"""EpisodeScript (SCHEMA 1.14.0; D-032, D-035..D-038; learning-companion §2, §12–§15).

One shape for the English script (generated, grounded) and its Arabic translation. The contract
enforces the three voices, the episode order (intro, book text, summary, key terms, five questions,
optional "others say"), the course-introduction episode 00, grounding on every generated line,
attribution of external material, the thinking pause after each question, and that the faithful
core (book text) is a reference to the book's translation units, never generated text."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
BOOK = c.ScriptSource(kind="book", lesson_id="l1", block_id="bk:2:1", quote="Hegemony is force plus consent.")
CONCEPT = c.ScriptSource(kind="concept", concept_id="cpt_000000000001")
DOSSIER = c.ScriptSource(kind="dossier", claim_id="clm_000000000001", url="https://en.wikipedia.org/wiki/X")
OUTLINE = c.ScriptSource(kind="outline", episode=3)


def line(i, text="A grounded sentence.", sources=(BOOK,), **kw):
    return c.ScriptLine(line_id=f"l{i:03d}", text=text, sources=list(sources), **kw)


def intro():
    return c.EpisodeSection(section_id="s01", kind="episode_intro", voice="course",
                            lines=[line(1, "Structured Analytic Techniques. Episode 3: Hegemony.", [OUTLINE]),
                                   line(2, "This episode covers hegemony.", [BOOK])])


def book_text():
    return c.EpisodeSection(section_id="s02", kind="book_text", voice="author", block_ids=["bk:2:1"],
                            unit_ids=["bk:u00001"])


def summary():
    return c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course", lines=[line(3)])


def key_terms():
    return c.EpisodeSection(section_id="s04", kind="key_terms", voice="course", lines=[
        line(4, "hegemony: force plus consent.", [CONCEPT, BOOK], role="term", original="hegemony")])


def questions(n=5, pause=3000):
    lines = []
    for i in range(n):
        lines.append(line(10 + 2 * i, f"Question {i}?", [BOOK], role="question", pause_after_ms=pause))
        lines.append(line(11 + 2 * i, f"Answer {i}.", [BOOK], role="answer"))
    return c.EpisodeSection(section_id="s05", kind="questions", voice="course", lines=lines)


def others():
    return c.EpisodeSection(section_id="s06", kind="others_say", voice="external", lines=[
        line(40, "Reviewers call the book important.", [DOSSIER], attributed_to="Policing (review)")])


def script(sections=None, **kw):
    base = dict(book_id="bk", language="en", episode=3, kind="lesson", lesson_id="l1", title="Hegemony",
                book_title="Structured Analytic Techniques", translation_run_id="r" * 64,
                sections=sections if sections is not None else
                [intro(), book_text(), summary(), key_terms(), questions(), others()],
                created_at=NOW)
    return c.EpisodeScript(**(base | kw))


def test_schema_version_bumped_for_episode_scripts():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 14, 0)


def test_lesson_episode_in_d038_order():
    s = script()
    assert [x.kind for x in s.sections] == ["episode_intro", "book_text", "lesson_summary", "key_terms",
                                            "questions", "others_say"]
    assert s.format == "b2c.episode-script/1" and s.label(last_episode=81) == "03"
    assert script(sections=[intro(), book_text(), summary(), key_terms(), questions()]).sections[-1].kind == "questions"


def test_order_is_enforced():
    with pytest.raises(ValidationError, match="order"):
        script(sections=[book_text(), intro(), summary(), key_terms(), questions()])
    with pytest.raises(ValidationError, match="order"):
        script(sections=[intro(), book_text(), key_terms(), summary(), questions()])
    with pytest.raises(ValidationError, match="order"):
        script(sections=[intro(), summary(), key_terms(), questions()])  # no book text


def test_voices_are_fixed_per_kind():
    with pytest.raises(ValidationError, match="voice"):
        c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="author", lines=[line(3)])
    with pytest.raises(ValidationError, match="voice"):
        c.EpisodeSection(section_id="s02", kind="book_text", voice="course", block_ids=["bk:2:1"],
                         unit_ids=["bk:u00001"])
    with pytest.raises(ValidationError, match="voice"):
        c.EpisodeSection(section_id="s06", kind="others_say", voice="course", lines=[line(40, sources=[DOSSIER])])


def test_external_lines_are_attributed_to_a_dossier_source():
    with pytest.raises(ValidationError, match="attributed"):
        c.EpisodeSection(section_id="s06", kind="others_say", voice="external",
                         lines=[line(40, sources=[DOSSIER])])
    with pytest.raises(ValidationError, match="dossier"):
        c.EpisodeSection(section_id="s06", kind="others_say", voice="external",
                         lines=[line(40, sources=[BOOK], attributed_to="someone")])


def test_every_generated_line_is_grounded():
    with pytest.raises(ValidationError):
        line(1, sources=[])
    with pytest.raises(ValidationError):
        c.ScriptSource(kind="book", block_id="bk:2:1")  # a book source quotes the book
    with pytest.raises(ValidationError):
        c.ScriptSource(kind="dossier", claim_id="clm_000000000001")  # and a dossier source has its url
    with pytest.raises(ValidationError):
        c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course", lines=[])


def test_book_text_is_a_reference_not_generated_text():
    with pytest.raises(ValidationError, match="unit"):
        c.EpisodeSection(section_id="s02", kind="book_text", voice="author", block_ids=["bk:2:1"])
    with pytest.raises(ValidationError, match="book text"):  # English script: references only
        script(sections=[intro(), c.EpisodeSection(section_id="s02", kind="book_text", voice="author",
                                                   block_ids=["bk:2:1"], unit_ids=["bk:u00001"],
                                                   lines=[line(90, "Generated paraphrase.",
                                                                     unit_ids=["bk:u00001"])]),
                         summary(), key_terms(), questions()])
    with pytest.raises(ValidationError, match="translation_run_id"):
        script(translation_run_id=None)


def test_exactly_five_questions_with_pause_and_location():
    with pytest.raises(ValidationError, match="five"):
        questions(n=4)
    with pytest.raises(ValidationError, match="five"):
        questions(n=6)
    with pytest.raises(ValidationError, match="pause"):
        questions(pause=0)
    lines = list(questions().lines)
    lines[1] = line(11, "Answer.", [CONCEPT], role="answer")  # no lesson location
    with pytest.raises(ValidationError, match="location"):
        c.EpisodeSection(section_id="s05", kind="questions", voice="course", lines=lines)
    lines = list(questions().lines)
    lines[0], lines[1] = lines[1], lines[0]
    with pytest.raises(ValidationError, match="question"):
        c.EpisodeSection(section_id="s05", kind="questions", voice="course", lines=lines)


def test_key_terms_carry_the_original_and_the_concept():
    with pytest.raises(ValidationError, match="original"):
        c.EpisodeSection(section_id="s04", kind="key_terms", voice="course",
                         lines=[line(4, "x: y.", [CONCEPT, BOOK], role="term")])
    with pytest.raises(ValidationError, match="concept"):
        c.EpisodeSection(section_id="s04", kind="key_terms", voice="course",
                         lines=[line(4, "x: y.", [BOOK], role="term", original="x")])


def course_intro_sections(ack=True):
    s = [c.EpisodeSection(section_id="s01", kind="about_book", voice="external",
                          lines=[line(1, sources=[DOSSIER], attributed_to="OpenAlex")]),
         c.EpisodeSection(section_id="s02", kind="about_author", voice="external",
                          lines=[line(2, sources=[DOSSIER], attributed_to="Wikipedia")]),
         c.EpisodeSection(section_id="s03", kind="book_method", voice="course", lines=[line(3)]),
         c.EpisodeSection(section_id="s04", kind="course_guide", voice="course",
                          lines=[line(4, sources=[c.ScriptSource(kind="course", note="D-038")])])]
    if ack:
        s.append(c.EpisodeSection(section_id="s05", kind="acknowledgments", voice="course", lines=[line(5)]))
    return s


def test_course_introduction_is_episode_00():
    s = script(episode=0, kind="course_intro", lesson_id=None, title="Course introduction", translation_run_id=None,
               sections=course_intro_sections())
    assert s.label(last_episode=9) == "00" and s.sections[-1].kind == "acknowledgments"
    with pytest.raises(ValidationError):
        script(episode=0, kind="lesson", sections=course_intro_sections())
    with pytest.raises(ValidationError):
        script(episode=1, kind="course_intro", lesson_id=None, sections=course_intro_sections())
    with pytest.raises(ValidationError, match="order"):  # acknowledgments come last
        sections = course_intro_sections()
        script(episode=0, kind="course_intro", lesson_id=None, translation_run_id=None,
               sections=[sections[0], sections[4], *sections[1:4]])
    with pytest.raises(ValidationError, match="order"):  # no book text in the introduction
        script(episode=0, kind="course_intro", lesson_id=None, sections=[*course_intro_sections()[:4], book_text()])


def test_arabic_variant_traces_every_line_to_translation_units():
    def ar_line(i, text="نص.", **kw):
        return line(i, text, unit_ids=[f"bk:ep03:u{i:05d}"], status="accepted", **kw)
    ar_sections = [
        c.EpisodeSection(section_id="s01", kind="episode_intro", voice="course", lines=[ar_line(1, sources=[OUTLINE])]),
        c.EpisodeSection(section_id="s02", kind="book_text", voice="author", block_ids=["bk:2:1"],
                         unit_ids=["bk:u00001"], lines=[line(2, "الهيمنة (hegemony) قوة.", [BOOK],
                                                             unit_ids=["bk:u00001"], status="accepted",
                                                             inserted_originals=["hegemony"])]),
        c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course", lines=[ar_line(3)]),
        c.EpisodeSection(section_id="s04", kind="key_terms", voice="course", lines=[
            ar_line(4, "الهيمنة (hegemony): قوة.", sources=[CONCEPT, BOOK], role="term", original="hegemony")]),
        c.EpisodeSection(section_id="s05", kind="questions", voice="course", lines=[
            ar_line(10 + j, role="question" if j % 2 == 0 else "answer", pause_after_ms=3000 if j % 2 == 0 else 0)
            for j in range(10)]),
    ]
    s = script(language="ar", sections=ar_sections, source_script_sha256="a" * 64)
    assert s.language == "ar"
    with pytest.raises(ValidationError, match="source_script_sha256"):
        script(language="ar", sections=ar_sections)
    untraced = list(ar_sections)
    untraced[2] = c.EpisodeSection(section_id="s03", kind="lesson_summary", voice="course", lines=[line(3, "نص.")])
    with pytest.raises(ValidationError, match="unit"):
        script(language="ar", sections=untraced, source_script_sha256="a" * 64)
    with pytest.raises(ValidationError, match="unit"):  # book text only from its own units
        c.EpisodeSection(section_id="s02", kind="book_text", voice="author", block_ids=["bk:2:1"],
                         unit_ids=["bk:u00001"], lines=[line(2, "نص.", unit_ids=["bk:u00009"], status="accepted")])


def test_ids_unique_and_judge_from_another_family():
    with pytest.raises(ValidationError, match="unique"):
        script(sections=[intro(), book_text(), c.EpisodeSection(section_id="s03", kind="lesson_summary",
                                                                voice="course", lines=[line(1)]),
                         key_terms(), questions()])
    ident = lambda m: c.ProducerIdentity(role="x", model_id=m)
    with pytest.raises(ValidationError, match="family"):
        script(generator=ident("deepseek/a"), validator=ident("deepseek/b"), generator_family="deepseek",
               validator_family="deepseek")
    assert script(generator=ident("deepseek/a"), validator=ident("google/b"), generator_family="deepseek",
                  validator_family="google").validator_family == "google"
