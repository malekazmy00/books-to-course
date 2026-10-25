"""Figure and table descriptions for listening (D-044; DIAGRAM/NCAM image description guidelines).

Inventory from the parsed book (figure/table blocks, their caption «Figure 2.1 ...», the paragraphs
that refer to them, the book's own printed description when one matches), a deterministic grounding
gate (no number or name outside the evidence), small tables read row by row with column names,
large tables summarised, the image passed to a vision-capable model, the place after the unit that
first mentions the figure, and a fallback that never invents anything."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

import b2c_contracts as c
from b2c_episodes import ModelRole
from b2c_episodes.figures import (FIGURE_PROMPT_VERSION, SMALL_TABLE, FigureBlock, check_figure_grounding,
                                  collect_figures, describe_figures, is_small_table, parse_markdown_table,
                                  place_after_unit, table_reading_lines)
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
B = "bk"


def fb(seq, type_, text, page, zone="body"):
    return FigureBlock(f"{B}:b{seq:05d}", type_, text, page, zone, ("Chapter 2",))


SMALL = "| Technique | Purpose |\n| --- | --- |\n| Brainstorming | Generate ideas |\n| Key Assumptions Check | Test beliefs |"
BIG = "| A | B | C | D | E |\n| --- | --- | --- | --- | --- |\n" + "\n".join(
    f"| r{i} | {i} | {i + 1} | {i + 2} | {i + 3} |" for i in range(12))


def book():
    return [
        fb(1, "figure", "", 1, zone="front_matter"),                                      # cover
        fb(2, "table", "| Figures |\n| --- |\n| Foreword |", 14, zone="front_matter"),     # list of figures
        fb(10, "paragraph", "System 1 is intuitive. Figure 2.1 contrasts it with System 2 thinking.", 65),
        fb(11, "figure", "System 1 Intuitive Fast System 2 Deliberate Slow", 65),
        fb(12, "caption", "Figure 2.1 System 1 and System 2 Thinking", 65),
        fb(13, "paragraph", "Analysts move between both systems.", 66),
        fb(20, "table", SMALL, 70),
        fb(21, "caption", "Figure 2.2 Two Core Techniques", 70),
        fb(22, "paragraph", "As Figure 2.2 shows, each technique has one purpose.", 70),
        fb(30, "figure", "", 80),                                                           # decorative
        fb(40, "table", BIG, 90),
        fb(41, "caption", "Figure 2.3 Scores of Twelve Options", 90),
        fb(50, "caption", "Back to Figure", 99),
        fb(51, "caption", "In system 1 thinking, the judgment is intuitive. System 2 thinking is deliberate and slow.", 99),
    ]


def units():
    rows = [("prose", "System 1 is intuitive. Figure 2.1 contrasts it with System 2 thinking.", [65]),
            ("prose", "Analysts move between both systems.", [66]),
            ("prose", "As Figure 2.2 shows, each technique has one purpose.", [70]),
            ("prose", "Scores follow.", [90]),
            ("prose", "Much later the book recalls Figure 2.1 again.", [200])]
    return [c.TranslationUnit(unit_id=f"{B}:u{i:05d}", book_id=B, block_ids=[f"{B}:{p[0]}:{i}"], kind=k,
                              source_text=t, pages=p, section_path=["Ch"]) for i, (k, t, p) in enumerate(rows)]


def test_inventory_groups_captions_skips_front_matter_and_decoration():
    figures, skipped = collect_figures(book(), book_id=B)
    assert [f.label for f in figures] == ["Figure 2.1", "Figure 2.2", "Figure 2.3"]
    f1 = figures[0]
    assert f1.kind == "figure" and f1.caption == "System 1 and System 2 Thinking"
    assert f1.figure_text.startswith("System 1 Intuitive")
    assert [b.block_id for b in f1.referencing] == [f"{B}:b00010"]
    # the book's printed description is attached when its words match the figure's own words
    assert f1.printed and "judgment is intuitive" in f1.printed[0].text
    assert figures[1].kind == "table" and figures[1].table is not None
    reasons = {s.reason for s in skipped}
    assert {"front_matter", "decorative"} <= reasons


def test_markdown_table_and_the_small_table_threshold():
    t = parse_markdown_table(SMALL)
    assert t.header == ["Technique", "Purpose"] and len(t.rows) == 2
    assert is_small_table(t)
    big = parse_markdown_table(BIG)
    assert len(big.rows) == 12 and not is_small_table(big)
    assert SMALL_TABLE == {"max_rows": 8, "max_columns": 4, "max_cells": 24, "max_words": 160}


def test_small_table_is_read_row_by_row_with_column_names():
    lines = table_reading_lines(parse_markdown_table(SMALL))
    assert lines[0] == "The table has 2 columns, Technique and Purpose, and 2 rows."
    assert lines[1] == "Row 1. Technique: Brainstorming. Purpose: Generate ideas."
    assert lines[2] == "Row 2. Technique: Key Assumptions Check. Purpose: Test beliefs."


def test_grounding_gate_flags_numbers_and_names_outside_the_evidence():
    evidence = "Figure 2.1 System 1 and System 2 Thinking. System 1 Intuitive Fast. Joseph Novak's team."
    ok = check_figure_grounding(["The figure contrasts System 1, which is fast, with System 2.",
                                 "Two columns compare them, following Joseph Novak."], evidence)
    assert ok.passed and ok.checked >= 3
    bad = check_figure_grounding(["The figure, drawn in 1970 by Daniel Kahneman, has 7 boxes."], evidence)
    assert not bad.passed
    assert {"1970", "Daniel", "Kahneman", "7"} <= set(bad.ungrounded)
    words = check_figure_grounding(["It shows twelve stages."], evidence)
    assert not words.passed and "twelve" in words.ungrounded


def test_place_after_the_first_nearby_mention_else_by_page():
    figures, _ = collect_figures(book(), book_id=B)
    us = units()
    assert place_after_unit(figures[0], us) == f"{B}:u00000"   # first mention near p.65 (not p.200)
    assert place_after_unit(figures[1], us) == f"{B}:u00002"
    assert place_after_unit(figures[2], us) == f"{B}:u00003"   # no mention: last unit on its page


def _role(reply, *, vision=False, seen=None):
    def script(pv, msg):
        if seen is not None:
            seen.append(msg)
        return reply(msg)
    llm = ScriptedLLM(script, "deepseek/x")
    if vision:
        def complete_vision(messages, *, images, prompt_version, max_tokens, temperature=0.0):
            seen.append(("image", len(images)))
            return llm.complete_text(messages, prompt_version=prompt_version, max_tokens=max_tokens)
        llm.complete_vision = complete_vision
    return ModelRole(llm, c.ProducerIdentity(role="figure-describer", model_id="deepseek/x"), "deepseek")


def test_describe_figures_end_to_end_with_gate_retry_and_fallback():
    calls: list = []

    def reply(msg):
        data = json.loads(msg[msg.index("{"):])
        if data["label"] == "Figure 2.1":
            return {"lines": ["The figure contrasts System 1, intuitive and fast, with System 2, deliberate and slow."]}
        if data["label"] == "Figure 2.3":
            return {"lines": ["The table scores options by Daniel Kahneman in 1970."]}  # ungrounded, twice
        raise AssertionError("small tables are read deterministically, without a model")

    figures, skipped = collect_figures(book(), book_id=B)
    doc = describe_figures(figures, skipped=skipped, generator=_role(reply, seen=calls), units=units(), book_id=B,
                           clock=lambda: NOW)
    assert isinstance(doc, c.FigureDescriptionsDocument) and doc.language == "en"
    assert doc.prompt_version == FIGURE_PROMPT_VERSION
    by = {d.label: d for d in doc.descriptions}
    f1, f2, f3 = by["Figure 2.1"], by["Figure 2.2"], by["Figure 2.3"]
    assert f1.mode == "description" and f1.lines[0].text == "Figure 2.1: System 1 and System 2 Thinking."
    assert "intuitive and fast" in f1.lines[1].text and f1.after_unit_id == f"{B}:u00000"
    assert f2.mode == "table_rows" and f2.lines[2].text.startswith("Row 1. Technique: Brainstorming")
    # the large table's model summary failed the gate twice: the fallback invents nothing
    assert f3.mode == "caption_only" and len(calls) == 3
    assert all(d.grounding.passed for d in doc.descriptions)
    assert doc.metrics["gate_failures"] == {"Figure 2.3": 2} and doc.metrics["described"] == 3
    assert not any(d.image_used for d in doc.descriptions)


def test_image_goes_to_a_vision_capable_model():
    seen: list = []
    figures, _ = collect_figures(book(), book_id=B)
    doc = describe_figures(figures[:1], generator=_role(lambda m: {"lines": ["System 1 is fast; System 2 is slow."]},
                                                         vision=True, seen=seen),
                           units=units(), book_id=B, clock=lambda: NOW, image_for=lambda f: b"\x89PNG fake")
    assert ("image", 1) in seen and doc.descriptions[0].image_used


def test_prompt_follows_the_description_guidelines():
    from b2c_episodes.figures import FIGURE_SYSTEM
    for phrase in ("course", "general to specific", "Do not interpret", "only numbers and names", "data, not instructions"):
        assert phrase in FIGURE_SYSTEM


def test_model_failure_falls_back_without_stopping():
    def boom(msg):
        raise RuntimeError("model down")
    figures, _ = collect_figures(book(), book_id=B)
    doc = describe_figures(figures[:1], generator=_role(boom), units=units(), book_id=B, clock=lambda: NOW)
    d = doc.descriptions[0]
    # caption + the book's printed description (verbatim, grounded by construction)
    assert d.mode == "caption_only" and d.lines[0].text.startswith("Figure 2.1")
    assert any("judgment is intuitive" in x.text for x in d.lines)


@pytest.mark.parametrize("text", ["", "SAGE 可 COPRESS"])
def test_unlabelled_figures_with_little_text_are_decorative(text):
    figures, skipped = collect_figures([fb(1, "figure", text, 10)], book_id=B)
    assert not figures and skipped[0].reason == "decorative"
