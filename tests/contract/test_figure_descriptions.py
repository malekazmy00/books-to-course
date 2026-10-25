"""Figure and table descriptions for listening (SCHEMA 1.20.0; D-044).

A description is written in the course voice, in English first (D-035), passes a grounding gate
(no number or name that the figure's own text or the paragraphs around it do not contain) and is
then translated like any episode line. Speech reads it in place, announced («وصف الشكل.») and closed
(«انتهى وصف الشكل.»); the episode script carries it as a ``figure_reading`` section."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
CAP = c.ScriptSource(kind="book", block_id="bk:b00171", quote="Figure 2.1 System 1 and System 2 Thinking")


def gline(i, text, **kw):
    return c.ScriptLine(line_id=f"fig001.l{i:02d}", text=text, sources=[CAP], **kw)


def grounding(ok=True):
    return c.FigureGrounding(passed=ok, checked=3, ungrounded=[] if ok else ["1970"])


def desc(**kw):
    base = dict(figure_id="fig001", kind="figure", label="Figure 2.1", caption="System 1 and System 2 Thinking",
                pages=[65], block_ids=["bk:b00169", "bk:b00171"], referencing_block_ids=["bk:b00160"],
                after_unit_id="bk:u00520", mode="description",
                lines=[gline(0, "Figure 2.1: System 1 and System 2 Thinking."),
                       gline(1, "The figure compares two kinds of thinking side by side.")],
                grounding=grounding())
    base.update(kw)
    return c.FigureDescription(**base)


def doc(descriptions=None, language="en", **kw):
    return c.FigureDescriptionsDocument(book_id="bk", language=language, descriptions=descriptions or [desc()],
                                        created_at=NOW, **kw)


def test_schema_version():
    # figure descriptions arrived in 1.20.0 (D-044); later contract versions keep them
    assert tuple(map(int, c.SCHEMA_VERSION.split("."))) >= (1, 20, 0)


def test_a_valid_english_document():
    d = doc(skipped=[c.FigureSkip(block_ids=["bk:b00005"], pages=[1], reason="front_matter")])
    assert d.format == "b2c.figure-descriptions/1"
    assert d.descriptions[0].voice == "course"
    assert c.FIGURE_ANNOUNCEMENT_AR["figure"] == "وصف الشكل." and c.FIGURE_ANNOUNCEMENT_AR["table"] == "وصف الجدول."
    assert c.FIGURE_END_AR["figure"] == "انتهى وصف الشكل."


def test_grounding_verdict_matches_its_findings():
    with pytest.raises(ValidationError):
        c.FigureGrounding(passed=True, checked=2, ungrounded=["Novak"])
    with pytest.raises(ValidationError):
        c.FigureGrounding(passed=False, checked=2, ungrounded=[])


def test_an_english_description_must_have_passed_its_grounding_gate():
    with pytest.raises(ValidationError, match="grounding"):
        doc([desc(grounding=grounding(False))])
    with pytest.raises(ValidationError, match="grounding"):
        doc([desc(grounding=None)])


def test_table_modes_are_for_tables_only():
    with pytest.raises(ValidationError):
        desc(mode="table_rows")
    assert desc(kind="table", mode="table_rows").mode == "table_rows"
    assert desc(kind="table", mode="table_summary").mode == "table_summary"


def test_ids_unique_and_arabic_lines_carry_translation():
    with pytest.raises(ValidationError, match="unique"):
        doc([desc(), desc()])
    with pytest.raises(ValidationError):  # Arabic line without its unit/decision
        doc(language="ar", source_document_sha256="a" * 64)
    ar_lines = [gline(0, "الشكل 2.1: التفكير.", unit_ids=["bk:fig:u00001"], status="accepted")]
    ok = doc([desc(lines=ar_lines)], language="ar", source_document_sha256="a" * 64)
    assert ok.descriptions[0].lines[0].status == "accepted"
    with pytest.raises(ValidationError):  # Arabic names the English document it translates
        doc([desc(lines=ar_lines)], language="ar")
    with pytest.raises(ValidationError):  # translation status belongs to the Arabic document
        doc([desc(lines=ar_lines)])


def seg(i, **kw):
    return c.SpeechSegment(segment_id=f"bk:s{i:05d}", seq=i, unit_ids=["bk:u00001"], structure="paragraph",
                           source_text="نص", spoken_text="نص", pause_after_ms=550, **kw)


def test_speech_segment_figure_fields():
    s = seg(0, voice="course", figure_id="fig001", figure_part="announcement")
    assert s.figure_part == "announcement"
    with pytest.raises(ValidationError):  # course voice must say why: a note or a figure
        seg(0, voice="course")
    with pytest.raises(ValidationError):  # a figure segment is the course voice
        seg(0, figure_id="fig001", figure_part="description")
    with pytest.raises(ValidationError):  # not both a note and a figure
        seg(0, voice="course", note_id="n", note_part="announcement", figure_id="fig001", figure_part="description")


def test_speech_document_with_figures_names_the_figures_artifact():
    ref = c.ArtifactRef(stage="translate-book", key="k", uri="s3://a/c/translate-book/k/", sha256="s" * 64,
                        created_at=NOW)
    fig_ref = ref.model_copy(update={"stage": "figure-descriptions-ar"})
    segs = [seg(0), seg(1, voice="course", figure_id="fig001", figure_part="announcement").model_copy(
        update={"unit_ids": ["bk:fig:u00001"]})]
    with pytest.raises(ValidationError, match="figures_ref"):
        c.SpeechTextDocument(book_id="bk", language="ar", frontend_version="v", source=ref, segments=segs)
    d = c.SpeechTextDocument(book_id="bk", language="ar", frontend_version="v", source=ref, segments=segs,
                             figures_ref=fig_ref)
    assert d.figures_ref == fig_ref


def _episode(sections):
    return c.EpisodeScript(book_id="bk", language="en", episode=3, kind="lesson", lesson_id="l1", title="T",
                           book_title="B", sections=sections, translation_run_id="r" * 64, created_at=NOW)


def _lesson_sections(figure):
    o = c.ScriptSource(kind="outline", episode=3)
    qa = []
    for i in range(5):
        qa += [c.ScriptLine(line_id=f"q{i}", role="question", text="Q?", sources=[CAP], pause_after_ms=4000),
               c.ScriptLine(line_id=f"a{i}", role="answer", text="A.", sources=[CAP])]
    return [c.EpisodeSection(section_id="i", kind="episode_intro", voice="course",
                             lines=[c.ScriptLine(line_id="i0", text="B. Episode 3: T.", sources=[o])]),
            c.EpisodeSection(section_id="b1", kind="book_text", voice="author", block_ids=["bk:1:0"],
                             unit_ids=["bk:u00001"]),
            figure,
            c.EpisodeSection(section_id="b2", kind="book_text", voice="author", block_ids=["bk:1:1"],
                             unit_ids=["bk:u00002"]),
            c.EpisodeSection(section_id="s", kind="lesson_summary", voice="course",
                             lines=[c.ScriptLine(line_id="s0", text="S.", sources=[CAP])]),
            c.EpisodeSection(section_id="k", kind="key_terms", voice="course", lines=[c.ScriptLine(
                line_id="k0", role="term", text="x: y", original="x",
                sources=[c.ScriptSource(kind="concept", concept_id="cpt_000000000001")])]),
            c.EpisodeSection(section_id="q", kind="questions", voice="course", lines=qa)]


def test_figure_reading_section_in_the_book_text():
    fig = c.EpisodeSection(section_id="f1", kind="figure_reading", voice="course", figure_id="fig001",
                           figure_kind="figure", lines=[gline(0, "Figure 2.1: System 1 and System 2 Thinking.")])
    script = _episode(_lesson_sections(fig))
    assert [s.kind for s in script.sections][2] == "figure_reading"
    with pytest.raises(ValidationError):  # a figure reading names its figure
        c.EpisodeSection(section_id="f1", kind="figure_reading", voice="course", figure_kind="figure",
                         lines=[gline(0, "x.")])
    with pytest.raises(ValidationError):  # the course voice, not the author's
        c.EpisodeSection(section_id="f1", kind="figure_reading", voice="author", figure_id="fig001",
                         figure_kind="figure", lines=[gline(0, "x.")])
    with pytest.raises(ValidationError):  # only a figure reading carries a figure id
        c.EpisodeSection(section_id="s", kind="lesson_summary", voice="course", figure_id="fig001",
                         lines=[gline(0, "x.")])
