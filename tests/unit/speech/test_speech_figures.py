"""Figures are read aloud by description (D-044, replacing the exclusion of figures from speech).

The book's speech text reads each ACCEPTED Arabic figure description after the unit it is placed
after, in the course voice: «وصف الشكل.», the description, then «انتهى وصف الشكل.». A description
whose translation is not accepted is not read (and is recorded as excluded); caption/table units of
the book stay excluded by kind. The episode front end reads a ``figure_reading`` section the same way."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from b2c_contracts import (FIGURE_ANNOUNCEMENT_AR, FIGURE_END_AR, ArtifactRef, EngineIdentity, FigureDescription,
                           FigureDescriptionsDocument, QEResult, ScriptLine, ScriptSource, TranslationUnit,
                           UnitTranslation)

from b2c_speech.text import PronunciationLexicon, build_speech_text, evaluate_speech_text

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/", sha256="s" * 64,
                     created_at=NOW)
FIGS = ArtifactRef(stage="figure-descriptions-ar", key="f", uri="s3://artifacts/c1/figure-descriptions-ar/f/",
                   sha256="f" * 64, created_at=NOW)
ENGINE = EngineIdentity(provider="fake", model_id="m", prompt_version="p@1")
TEXTS = [("heading", "Chapter Two", "الفصل الثاني"),
         ("prose", "Figure 2.1 contrasts two systems.", "يقارن الشكل بين نظامين."),
         ("caption", "Figure 2.1 System 1 and System 2", "الشكل: النظام الأول والنظام الثاني"),
         ("prose", "Analysts use both.", "يستخدم المحللون النظامين.")]
CAP = ScriptSource(kind="book", block_id="bk:b00012", quote="Figure 2.1 System 1 and System 2")


def units():
    return [TranslationUnit(unit_id=f"bk:u{i:05d}", book_id="bk", block_ids=[f"bk:b{i:05d}"], kind=k,
                            source_text=s, pages=[65], section_path=["Ch"]) for i, (k, s, _) in enumerate(TEXTS)]


def translations():
    return {f"bk:u{i:05d}": UnitTranslation(unit_id=f"bk:u{i:05d}", target_text=t, origin="engine", engine=ENGINE,
                                            qe=QEResult(deterministic_pass=True, route="auto_accept"),
                                            decision="accepted", decided_by="policy", decided_at=NOW)
            for i, (_, _, t) in enumerate(TEXTS)}


def figure(fid="fig001", after="bk:u00001", status="accepted", kind="figure"):
    lines = [ScriptLine(line_id=f"{fid}.l00", text="الشكل اثنان نقطة واحد: النظام الأول والنظام الثاني.",
                        sources=[CAP], unit_ids=[f"bk:{fid}:u00001"], status=status),
             ScriptLine(line_id=f"{fid}.l01", text="يقارن الشكل بين تفكير سريع وتفكير بطيء.", sources=[CAP],
                        unit_ids=[f"bk:{fid}:u00002"], status="accepted")]
    return FigureDescription(figure_id=fid, kind=kind, label="Figure 2.1", caption="System 1 and System 2",
                             pages=[65], block_ids=["bk:b00011", "bk:b00012"], after_unit_id=after,
                             mode="description" if kind == "figure" else "table_rows", lines=lines)


def figures(*descs):
    return FigureDescriptionsDocument(book_id="bk", language="ar", descriptions=list(descs) or [figure()],
                                      source_document_sha256="e" * 64, created_at=NOW)


def build(figs=None):
    return build_speech_text(units(), translations(), book_id="bk", source=SOURCE, lexicon=PronunciationLexicon(),
                             figures=figs, figures_ref=FIGS if figs is not None else None)


def test_without_figures_nothing_changes():
    doc = build()
    assert doc.figures_ref is None and not any(s.figure_id for s in doc.segments)


def test_figure_is_read_in_place_announced_and_closed_in_the_course_voice():
    doc = build(figures())
    order = [(s.unit_ids[0], s.figure_part) for s in doc.segments]
    first_fig = next(i for i, s in enumerate(doc.segments) if s.figure_id)
    assert doc.segments[first_fig - 1].unit_ids == ["bk:u00001"]          # right after the mention
    fig = [s for s in doc.segments if s.figure_id == "fig001"]
    assert fig[0].figure_part == "announcement" and fig[0].spoken_text.startswith(FIGURE_ANNOUNCEMENT_AR["figure"][:-1])
    assert fig[-1].figure_part == "end" and fig[-1].spoken_text.startswith(FIGURE_END_AR["figure"][:-1])
    assert {s.figure_part for s in fig[1:-1]} == {"description"}
    assert all(s.voice == "course" for s in fig)
    assert doc.segments[doc.segments.index(fig[-1]) + 1].unit_ids == ["bk:u00003"]
    assert ("bk:u00002", None) not in order                               # the caption unit is still not read
    assert doc.figures_ref == FIGS
    assert evaluate_speech_text(doc)[0]


def test_a_table_is_announced_as_a_table():
    doc = build(figures(figure(kind="table")))
    fig = [s for s in doc.segments if s.figure_id]
    assert fig[0].spoken_text.startswith(FIGURE_ANNOUNCEMENT_AR["table"][:-1])


def test_a_description_whose_translation_is_not_accepted_is_not_read():
    doc = build(figures(figure(status="needs_review")))
    assert not any(s.figure_id for s in doc.segments)
    assert {e.reason for e in doc.excluded} >= {"figure_not_accepted"}
    assert doc.figures_ref is None


def test_a_figure_placed_after_an_excluded_unit_follows_the_last_spoken_one():
    doc = build(figures(figure(after="bk:u00002")))   # anchored on the (excluded) caption unit
    fig = next(i for i, s in enumerate(doc.segments) if s.figure_id)
    assert doc.segments[fig - 1].unit_ids == ["bk:u00001"]


def test_figures_need_their_artifact():
    with pytest.raises(ValueError, match="figures_ref"):
        build_speech_text(units(), translations(), book_id="bk", source=SOURCE, figures=figures())
