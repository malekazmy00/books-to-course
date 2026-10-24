"""Unit builder for the translation rebuild (D-028): ParsedBook -> TranslationUnits.

All inputs are synthetic (no real book text in git). The patterns mirror the source-side
failures in the 2026-10-24 audit catalogue: letter-spaced OCR, line-break hyphens, paragraphs
cut at page breaks, glued/spaced footnote markers, notes/bibliography/TOC/index blocks.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from b2c_contracts import Block, PageRef, ParsedBook, TranslationUnit
from b2c_translation.units import (
    build_translation_units,
    build_units_with_provenance,
)

BOOK = "bk"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "translation_units"

# Filler body text: makes words such as "articulation" / "analysis" attested in the book
# vocabulary, which the OCR repair and de-hyphenation rules require.
FILLER = (
    "The articulation of struggles matters for the analysis of popular mobilization. "
    "Analysts study the articulation of struggles and the analysis of structure. "
    "A structure of struggles shapes the analysis after the spring. "
    "The information about autonomy and the information about the structure was clear. "
)


def blk(page: int, seq: int, text: str, type_: str = "paragraph", section=("Ch 1",), level=None) -> Block:
    return Block(
        block_id=f"{BOOK}:{page}:{seq}", type=type_, level=level, text=text,
        refs=[PageRef(book_id=BOOK, page_no=page)], section_path=list(section), ocr=False,
    )


def book(*blocks: Block, filler: bool = True) -> ParsedBook:
    extra = [blk(900, 0, FILLER), blk(901, 0, FILLER)] if filler else []
    return ParsedBook(
        book_id=BOOK, title="Synthetic", role="spine", page_count=999, toc_source="headings",
        blocks=[*blocks, *extra], parser={"name": "synthetic", "version": "0", "options_hash": "x"},
    )


def units_of(pb: ParsedBook, block_id: str, **kw) -> list[TranslationUnit]:
    return [u for u in build_translation_units(pb, **kw) if block_id in u.block_ids]


def texts(units) -> list[str]:
    return [u.source_text for u in units]


# ------------------------------------------------------------------ contract basics
def test_returns_contract_units_with_sequential_ids_and_links():
    pb = book(blk(1, 0, "Intro", "heading", level=1), blk(1, 1, "First sentence here. Second one."))
    units = build_translation_units(pb)
    assert all(isinstance(u, TranslationUnit) for u in units)
    assert [u.unit_id for u in units] == [f"{BOOK}:u{i:05d}" for i in range(len(units))]
    linked = [u for u in units if u.kind != "noise"]
    for a, b in zip(linked, linked[1:]):
        assert a.next_unit_id == b.unit_id and b.prev_unit_id == a.unit_id
    assert linked[0].prev_unit_id is None and linked[-1].next_unit_id is None


def test_deterministic_and_pure():
    pb = book(blk(1, 0, "Alpha beta gamma. Delta epsilon.\nZeta eta theta."))
    snapshot = pb.model_dump_json()
    a = [u.model_dump() for u in build_translation_units(pb)]
    b = [u.model_dump() for u in build_translation_units(pb)]
    assert a == b
    assert pb.model_dump_json() == snapshot


# ------------------------------------------------------------------ 1. normalization
def test_dehyphenates_line_break_hyphen_when_joined_word_is_attested():
    pb = book(blk(1, 0, "The careful analy-\nsis of the data was long."))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == "The careful analysis of the data was long."
    assert "dehyphenated" in u.normalization


def test_keeps_compound_hyphen_when_joined_form_is_unattested():
    pb = book(blk(1, 0, "It was a widely re-\nported mid-sized event."))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == "It was a widely re-ported mid-sized event."
    assert "dehyphenated" not in u.normalization


def test_repairs_letter_spaced_ocr_only_into_attested_words():
    pb = book(blk(1, 0, "the art i cul at i on of s truggl es", "heading", level=2))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == "the articulation of struggles"
    assert "ocr_letter_spacing" in u.normalization


def test_repairs_uppercase_letter_spaced_heading():
    pb = book(blk(1, 0, "I N F O R M A T I ON", "heading", level=1))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == "INFORMATION"


@pytest.mark.parametrize("text", [
    "We went in to the room some times.",      # real words are never merged
    "Plan B is ready and type A works.",       # isolated capitals stay
    "The x and y axes cross.",                  # algebraic single letters stay
])
def test_letter_spacing_repair_never_merges_real_words(text):
    pb = book(blk(1, 0, text))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == text
    assert "ocr_letter_spacing" not in u.normalization


def test_ligatures_spaces_and_controls_normalized_without_touching_digits():
    pb = book(blk(1, 0, "The ﬁnance and  ﬂow\x07 grew by x² and ½ in 2019."))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.source_text == "The finance and flow grew by x² and ½ in 2019."
    assert "unicode_normalized" in u.normalization


def test_space_before_punctuation_repaired_but_ellipsis_kept():
    pb = book(blk(1, 0, "Names: Doe , Jane . In Palermo . . . in the room ."))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert "Doe, Jane." in u.source_text
    assert "Palermo . . . in the room." in u.source_text


# ------------------------------------------------------------------ 2. page-break joins
def test_joins_paragraph_cut_at_page_break():
    pb = book(blk(3, 0, "Analysts use techniques that when"), blk(4, 0, "applied result in better analysis."))
    units = units_of(pb, f"{BOOK}:3:0")
    assert len(units) == 1
    (u,) = units
    assert u.source_text == "Analysts use techniques that when applied result in better analysis."
    assert u.block_ids == [f"{BOOK}:3:0", f"{BOOK}:4:0"] and u.pages == [3, 4]
    assert "joined_page_break" in u.normalization


def test_page_break_join_skips_running_header_and_dehyphenates():
    pb = book(
        blk(3, 0, "The careful analy-"), blk(4, 0, "RUNNING HEAD", "page_header"),
        blk(4, 1, "sis continued on the next page."),
    )
    (u,) = units_of(pb, f"{BOOK}:3:0")
    assert u.source_text == "The careful analysis continued on the next page."
    assert u.block_ids == [f"{BOOK}:3:0", f"{BOOK}:4:1"]


@pytest.mark.parametrize("first,second", [
    ("This sentence is complete.", "another starts here in lowercase."),  # terminal punctuation
    ("The paragraph ends without a stop", "New Paragraph Starts Capitalised here."),
])
def test_no_join_when_first_paragraph_is_closed_or_next_starts_new(first, second):
    pb = book(blk(3, 0, first), blk(4, 0, second))
    assert all(len(u.block_ids) == 1 for u in build_translation_units(pb))


def test_no_join_into_heading_or_on_same_page_gap_too_large():
    pb = book(blk(3, 0, "This text ends with the"), blk(4, 0, "Chapter Two", "heading", level=1),
              blk(4, 1, "rest of nothing."))
    assert all(len(u.block_ids) == 1 for u in build_translation_units(pb))
    pb2 = book(blk(3, 0, "This text ends with the"), blk(9, 0, "rest of it."))
    assert all(len(u.block_ids) == 1 for u in build_translation_units(pb2))


def test_wrapped_lines_inside_block_are_joined_and_inline_heading_split():
    text = ("ORIGIN AND PURPOSE\n"
            "The investigative commissions that followed the attacks clearly\n"
            "documented the need for a new approach to analysis in the field.\n"
            "When to Use It\n"
            "Use it whenever a set of alternative hypotheses exists.")
    pb = book(blk(1, 0, text))
    units = units_of(pb, f"{BOOK}:1:0")
    assert [(u.kind, u.source_text) for u in units] == [
        ("heading", "ORIGIN AND PURPOSE"),
        ("prose", "The investigative commissions that followed the attacks clearly documented "
                  "the need for a new approach to analysis in the field."),
        ("heading", "When to Use It"),
        ("prose", "Use it whenever a set of alternative hypotheses exists."),
    ]


# ------------------------------------------------------------------ 3. classification
def kinds(pb, block_id):
    return [u.kind for u in units_of(pb, block_id)]


def test_block_type_kinds():
    pb = book(
        blk(1, 0, "SOME RUNNING HEAD", "page_header"), blk(1, 1, "Page footer text", "page_footer"),
        blk(1, 2, "Chapter One", "heading", level=1), blk(1, 3, "A plain list entry here.", "list_item"),
        blk(1, 4, "Figure 2.1 Flow of Analytic Work", "caption"), blk(1, 5, "17"),
        blk(1, 6, "xii"),
    )
    assert kinds(pb, f"{BOOK}:1:0") == ["noise"]
    assert kinds(pb, f"{BOOK}:1:1") == ["noise"]
    assert kinds(pb, f"{BOOK}:1:2") == ["heading"]
    assert kinds(pb, f"{BOOK}:1:3") == ["list_item"]
    assert kinds(pb, f"{BOOK}:1:4") == ["caption"]
    assert kinds(pb, f"{BOOK}:1:5") == ["noise"]
    assert kinds(pb, f"{BOOK}:1:6") == ["noise"]


def test_caption_from_text_but_not_prose_sentence_about_a_figure():
    pb = book(blk(1, 0, "Figure 3.4 Six Families of Techniques\nThe body continues here with more text."),
              blk(2, 0, "Figure 3.4 shows how the families relate to each other."))
    assert kinds(pb, f"{BOOK}:1:0") == ["caption", "prose"]
    assert kinds(pb, f"{BOOK}:2:0") == ["prose"]


def test_markdown_table_becomes_cells():
    table = "| Term | Meaning |\n|------|---------|\n| ACH | Analysis of Competing Hypotheses |\n| KAC | 12 |"
    pb = book(blk(1, 0, table, "table"))
    units = units_of(pb, f"{BOOK}:1:0")
    assert {u.kind for u in units} == {"table_cell"}
    assert texts(units) == ["Term", "Meaning", "ACH", "Analysis of Competing Hypotheses", "KAC", "12"]


def test_unstructured_table_is_one_flagged_cell_unit():
    pb = book(blk(1, 0, "Neutral Positive Strong Weak", "table"))
    (u,) = units_of(pb, f"{BOOK}:1:0")
    assert u.kind == "table_cell" and "table_unparsed" in u.normalization


def test_footnote_block_prose_vs_bibliographic():
    pb = book(
        blk(5, 0, "1. The authors later revised this view after long discussions with analysts.", "footnote"),
        blk(5, 1, "2. Ibid., 55.", "footnote"),
        blk(5, 2, "3. Jane Doe, A Book of Methods (London: Example Press, 2001), 34.", "footnote"),
    )
    assert kinds(pb, f"{BOOK}:5:0") == ["footnote"]
    assert kinds(pb, f"{BOOK}:5:1") == ["reference"]
    assert kinds(pb, f"{BOOK}:5:2") == ["reference"]


@pytest.mark.parametrize("entry", [
    "Doe, Jane. A Book of Methods. London: Example Press, 2016.",
    "Roe, Richard. 'An Article on Things'. Journal of Examples 17, no. 1 (1990): 41-55.",
    "Ibid.",
    "Ibid., 68, 87.",
    "Smith, Second Book, 141.",
    "op. cit., p. 12.",
])
def test_bibliography_and_ibid_entries_are_references(entry):
    pb = book(blk(7, 0, entry, "list_item"), blk(7, 1, "Ibid., 3.", "list_item"),
              blk(7, 2, "Doe, Jane. Another Book. Paris: Example, 2001.", "list_item"))
    assert kinds(pb, f"{BOOK}:7:0") == ["reference"]


def test_notes_section_inside_page_block():
    text = ("N O T ES\n"
            "1. For further information on this theory, see the research by\n"
            "Jane Doe, Two Minds (Oxford: Example Press, 2009).\n"
            "2. Ibid., 112.\n"
            "3. Reasoning by analogy can also be a structured technique, as\n"
            "described in chapter 8.")
    pb = book(blk(79, 0, text))
    units = units_of(pb, f"{BOOK}:79:0")
    got = [(u.kind, u.source_text[:20]) for u in units]
    assert got[0][0] in ("heading", "noise")
    assert [k for k, _ in got[1:]] == ["footnote", "reference", "footnote"]
    assert units[1].source_text.startswith("1. For further information")
    assert units[1].source_text.endswith("(Oxford: Example Press, 2009).")


def test_inline_run_of_notes_is_split_per_note():
    pb = book(blk(353, 7, "140. Ibid., 119-124. 141. Ibid., 124. 142. Doe, Book, 33.", "code"))
    units = units_of(pb, f"{BOOK}:353:7")
    assert texts(units) == ["140. Ibid., 119-124.", "141. Ibid., 124.", "142. Doe, Book, 33."]
    assert {u.kind for u in units} == {"reference"}


def test_toc_run_and_index_entries():
    pb = book(
        blk(6, 0, "C O N T E N T S", "heading", level=1),
        blk(6, 1, "The Originality of the Tradition 15", "list_item"),
        blk(6, 2, "The Faces and Phases of Mobilization 48", "list_item"),
        blk(6, 3, "The Wellsprings of Autonomy 129", "list_item"),
        blk(6, 4, "Notes 321 Bibliography 369 Index 393"),
        blk(7, 0, "1.1 Our Vision\n1.2 Role of Techniques\n1.3 History of Techniques\n5.1 Sorting [ 69 ]"),
        blk(400, 0, "absenteeism, 106, 112-13 activism: and anarchism, 185; party, 69 Africa, 53, 81-82"),
        blk(400, 1, "Merli, Stefano, 237"),
        blk(400, 2, "255, 256-64"),
    )
    for bid in ("6:1", "6:2", "6:3", "6:4"):
        assert kinds(pb, f"{BOOK}:{bid}") == ["toc"], bid
    assert set(kinds(pb, f"{BOOK}:7:0")) == {"toc"}
    assert set(kinds(pb, f"{BOOK}:400:0")) == {"index"}
    assert len(units_of(pb, f"{BOOK}:400:0")) == 3  # one unit per index entry
    assert kinds(pb, f"{BOOK}:400:1") == ["index"]
    assert kinds(pb, f"{BOOK}:400:2") == ["index"]


def test_body_numbered_heading_is_not_toc():
    pb = book(blk(1, 0, "6.3 NOMINAL GROUP TECHNIQUE\nThe technique is a process for generating\n"
                        "and evaluating ideas in groups of analysts."))
    assert kinds(pb, f"{BOOK}:1:0") == ["heading", "prose"]


def test_duplicate_of_preceding_heading_block_is_noise():
    pb = book(blk(195, 0, "6.3 Nominal Group Technique", "heading", level=2),
              blk(195, 1, "6.3 NOMINAL GROUP TECHNIQUE\nNominal Group Technique is a process for ideas."))
    units = units_of(pb, f"{BOOK}:195:1")
    assert [u.kind for u in units] == ["noise", "prose"]
    assert "duplicate_heading" in units[0].normalization


def test_ebook_navigation_labels_are_noise():
    pb = book(blk(83, 0, "Descriptions of Images and Figures\nBack to Figure\nIn system one thinking the judgment is fast."))
    assert kinds(pb, f"{BOOK}:83:0") == ["noise", "noise", "prose"]


# ------------------------------------------------------------------ 4. segmentation
def test_short_paragraph_is_one_unit():
    pb = book(blk(1, 0, "Dr. Smith met Mr. Jones. They talked, e.g. about the U.S. report."))
    assert texts(units_of(pb, f"{BOOK}:1:0")) == ["Dr. Smith met Mr. Jones. They talked, e.g. about the U.S. report."]


def test_long_paragraph_packed_at_sentence_boundaries_respecting_abbreviations():
    sents = [f"Sentence number {i} mentions Dr. Smith and the U.S. agency in passing." for i in range(20)]
    pb = book(blk(1, 0, " ".join(sents)))
    units = units_of(pb, f"{BOOK}:1:0", max_unit_chars=200)
    assert len(units) > 1
    for u in units:
        assert len(u.source_text) <= 200
        assert u.source_text.endswith("in passing.")
        assert not u.source_text.startswith("Smith")
    assert " ".join(texts(units)) == " ".join(sents)


def test_single_long_sentence_is_own_unit_and_split_at_clauses_only_beyond_twice_limit():
    long_s = "This sentence " + ", and then ".join(f"clause {i} goes on" for i in range(12)) + "."
    assert 200 < len(long_s) <= 400
    pb = book(blk(1, 0, "Short one. " + long_s + " Another short one."))
    units = units_of(pb, f"{BOOK}:1:0", max_unit_chars=200)
    assert long_s in texts(units)
    huge = "This sentence " + ", and then ".join(f"clause {i} goes on" for i in range(40)) + "."
    assert len(huge) > 400
    units = units_of(book(blk(1, 0, huge)), f"{BOOK}:1:0", max_unit_chars=200)
    assert len(units) >= 3 and all("split_clause" in u.normalization for u in units)
    assert all(len(u.source_text) <= 200 for u in units)
    assert " ".join(texts(units)) == huge


# ------------------------------------------------------------------ 5. footnote markers
def markers_and_text(pb, block_id):
    return [(u.source_text, u.footnote_markers) for u in units_of(pb, block_id)]


def test_spaced_marker_after_sentence_end_is_extracted_and_ends_unit():
    pb = book(blk(1, 0, "States were overstated. 1 There was also a revival after the spring. 2"))
    assert markers_and_text(pb, f"{BOOK}:1:0") == [
        ("States were overstated.", ["1"]),
        ("There was also a revival after the spring.", ["2"]),
    ]


def test_glued_markers_after_punctuation_quote_and_paren():
    pb = book(blk(1, 0, "It is part of human nature.'1 It tells its own story.2 "
                        "Six families exist (see Figure 1.6).3 Each one helps."))
    assert markers_and_text(pb, f"{BOOK}:1:0") == [
        ("It is part of human nature.'", ["1"]),
        ("It tells its own story.", ["2"]),
        ("Six families exist (see Figure 1.6).", ["3"]),
        ("Each one helps.", []),
    ]


def test_marker_glued_to_year_or_figure_number_only_when_in_sequence():
    pb = book(blk(1, 0, "Engineers met first.1 They met again in 1921.2 They quickly grew. "
                        "This was described in Figure 3.6.3 Each habit can be learned. "
                        "Growth was 1.5 percent in 1999."))
    got = markers_and_text(pb, f"{BOOK}:1:0")
    assert [m for _, m in got] == [["1"], ["2"], ["3"], []]
    assert got[1][0].endswith("in 1921.") and got[2][0].endswith("in Figure 3.6.")
    assert got[3][0].endswith("Growth was 1.5 percent in 1999.")


def test_marker_glued_to_word_mid_sentence_splits_unit():
    pb = book(blk(1, 0, "Engineers met first.1 Red Hat Analysis2 is a useful technique."))
    assert markers_and_text(pb, f"{BOOK}:1:0") == [
        ("Engineers met first.", ["1"]),
        ("Red Hat Analysis", ["2"]),
        ("is a useful technique.", []),
    ]


def test_system_one_pattern():
    pb = book(blk(1, 0, "First claim.1 The bias is found in System 1 Thinking.2 This is System 2 work."))
    got = markers_and_text(pb, f"{BOOK}:1:0")
    assert got[1] == ("The bias is found in System 1 Thinking.", ["2"])
    assert got[2] == ("This is System 2 work.", [])


@pytest.mark.parametrize("text", [
    "In 2011 there were 15 people. 30 June 2013 marked a change.",
    "See p. 45 The Book for details. It was in vol. 3 Part Two.",
    "Growth was 1.5 percent. Version 2.1 The new one is out.",
    "Steps follow. 2. Working in small teams helps. 3. Review each item.",
    "Workers in Q11 and LP1 and COVID19 were counted. The total was 12.",
    "He scored 7 out of 10. 8 Of them passed.",
])
def test_real_numbers_are_never_taken_as_markers(text):
    pb = book(blk(1, 0, text))
    us = units_of(pb, f"{BOOK}:1:0")
    assert all(u.footnote_markers == [] for u in us)
    assert re.sub(r"\s+", " ", " ".join(texts(us))) == text


def test_markers_not_extracted_from_notes_references_or_toc():
    pb = book(blk(5, 0, "2. Ibid., 55. 3. Doe, Book, 12.", "footnote"),
              blk(6, 0, "The Wellsprings of Autonomy 129", "list_item"),
              blk(6, 1, "The Search for Consent 189", "list_item"),
              blk(6, 2, "The Roiling Ferment 254", "list_item"))
    assert all(u.footnote_markers == [] for u in build_translation_units(pb))


def test_superscript_marker():
    pb = book(blk(1, 0, "A claim is made here.¹ Another follows."))
    assert markers_and_text(pb, f"{BOOK}:1:0")[0] == ("A claim is made here.", ["1"])


# ------------------------------------------------------------------ 6. coverage property
def _strip(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _random_block_text(rng: random.Random, marker_state: list[int]) -> str:
    words = ["analysis", "structure", "the", "of", "data", "group", "method", "value", "Gramsci",
             "theory", "in", "a", "model", "2019", "15", "p.", "e.g.", "Dr.", "U.S."]
    sentences = []
    for _ in range(rng.randint(1, 12)):
        n = rng.randint(3, 30)
        s = " ".join(rng.choice(words) for _ in range(n))
        s = s[0].upper() + s[1:] + rng.choice([".", ".", "?", "!", ".'"])
        if rng.random() < 0.3:
            marker_state[0] += 1
            s += rng.choice([" ", ""]) + str(marker_state[0])
        sentences.append(s)
    text = " ".join(sentences)
    if rng.random() < 0.5:  # hard-wrap into lines
        out, line = [], ""
        for w in text.split(" "):
            if len(line) + len(w) > 60:
                out.append(line); line = w
            else:
                line = f"{line} {w}".strip()
        out.append(line)
        text = "\n".join(out)
    if rng.random() < 0.3:  # cut mid-sentence (page-break join candidate)
        text = text.rstrip(".?!'0123456789 ") + " and the"
    return text


@pytest.mark.parametrize("seed", range(30))
def test_property_every_character_covered_exactly_once(seed):
    """Concatenating the unit pieces of a block gives the normalized block text minus the
    extracted markers (noise units included, so nothing silently disappears)."""
    rng = random.Random(seed)
    marker_state = [0]
    blocks = []
    page = 1
    for i in range(rng.randint(2, 14)):
        page += rng.choice([0, 1, 1, 2])
        typ = rng.choice(["paragraph"] * 6 + ["list_item", "heading", "page_header"])
        blocks.append(blk(page, i, _random_block_text(rng, marker_state), typ))
    pb = book(*blocks, filler=False)
    build = build_units_with_provenance(pb, max_unit_chars=rng.choice([80, 150, 400]))
    for b in blocks:
        pieces = build.coverage[b.block_id]
        covered = _strip("".join(text for _, text in pieces))
        markers = [m for m, bid in build.marker_blocks if bid == b.block_id]
        original = _strip(b.text)
        assert re.sub(r"\d", "", covered) == re.sub(r"\d", "", original), b.text
        assert sorted(_digits(original)) == sorted(_digits(covered) + "".join(markers)), b.text
    for u in build.units:  # pieces partition each unit's text across its blocks, once
        own = [text for bid in u.block_ids for uid, text in build.coverage[bid] if uid == u.unit_id]
        assert _strip("".join(own)) == _strip(u.source_text)


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


# ------------------------------------------------------------------ regressions found on the real books
def test_inline_notes_whose_first_label_was_lost():
    pb = book(blk(364, 3, "SPN, 111; Q15, \u00a711. 186. SPN, 112; Q15, \u00a715. 187. Ibid.", "list_item"))
    assert texts(units_of(pb, f"{BOOK}:364:3")) == ["SPN, 111; Q15, \u00a711.", "186. SPN, 112; Q15, \u00a715.", "187. Ibid."]


def test_run_of_short_form_notes_is_reference_list():
    items = ["Salamini, Political Praxis, 128.", "Doe, Two Revolutions, 172-173.", "SPN, 405; Q11, \u00a762.",
             "Roe, 'Moral Economy', 40."]
    pb = book(*[blk(370, i, t, "list_item") for i, t in enumerate(items)])
    for i in range(len(items)):
        assert kinds(pb, f"{BOOK}:370:{i}") == ["reference"]


def test_numbered_method_steps_are_not_endnotes():
    text = ("5. Analyze direct impacts. Document the impact of each variable, starting with the first.\n"
            "6. Analyze loops and indirect impacts. The matrix shows only the direct effect of one variable.")
    pb = book(blk(643, 0, text))
    assert set(kinds(pb, f"{BOOK}:643:0")) <= {"prose", "list_item"}


def test_ibid_with_commentary_is_a_footnote():
    pb = book(blk(360, 0, "Ibid., 11. A rich engagement with common sense is offered in this later study of rural life.",
                  "list_item"))
    assert kinds(pb, f"{BOOK}:360:0") == ["footnote"]


def test_index_region_absorbs_wrapped_entries_and_bare_locators():
    pb = book(
        blk(410, 0, "absenteeism, 106, 112-13 activism: and anarchism, 185; party, 69 Africa, 53, 81-82"),
        blk(410, 1, "movement-oriented labour NGOs"), blk(410, 2, "(MLNGOs), 227, 249-50"),
        blk(410, 3, "MST. See Landless Workers Movement"), blk(410, 4, "284"),
        blk(410, 5, "voluntarism, 154, 174, 184, 190, 245"),
    )
    for i in range(6):
        assert set(kinds(pb, f"{BOOK}:410:{i}")) == {"index"}, i


def test_long_index_entry_is_split_at_subentries_within_limit():
    sub = "; ".join(f"aspect{i}, {i + 10}, {i + 40}-{i + 45}" for i in range(20))
    pb = book(blk(404, 0, f"activism: {sub}"), blk(404, 1, "Africa, 53, 81-82, 109, 146"))
    units = units_of(pb, f"{BOOK}:404:0", max_unit_chars=120)
    assert len(units) > 1 and {u.kind for u in units} == {"index"}
    assert all(len(u.source_text) <= 120 for u in units)
    assert " ".join(texts(units)) == f"activism: {sub}"


def test_bare_number_inside_a_block_is_content_not_page_number():
    pb = book(blk(349, 0, "Variable impact\nNeutral\n5\nStrong positive"))
    got = [(u.kind, u.source_text) for u in units_of(pb, f"{BOOK}:349:0")]
    assert ("noise", "5") not in got and any(t == "5" for _, t in got)


def test_caption_line_does_not_swallow_following_body_text():
    text = ("Figure 6.7C Venn Diagram of Zambrian Corporations and Their Partners Abroad\n"
            "Each of these assumptions, made explicit by the diagram, can now be challenged.")
    pb = book(blk(227, 0, text))
    assert kinds(pb, f"{BOOK}:227:0") == ["caption", "prose"]


def test_quantity_enumerations_are_not_markers():
    pb = book(blk(1, 0, "First point.1 Score by giving 4 points for the best idea, 3 points for the next, "
                        "and 2 points for the third."))
    us = units_of(pb, f"{BOOK}:1:0")
    assert [m for u in us for m in u.footnote_markers] == ["1"]


def test_two_markers_on_one_spot_and_marker_after_decade():
    pb = book(blk(1, 0, "Biases were described in the early1970s.1 Analysts focus on the vivid case.2,3 "
                        "Deceivers know it."))
    got = markers_and_text(pb, f"{BOOK}:1:0")
    assert got[0] == ("Biases were described in the early1970s.", ["1"])
    assert got[1] == ("Analysts focus on the vivid case.", ["2", "3"])


def test_example_labels_and_journal_volumes_are_not_markers():
    pb = book(blk(1, 0, "Claim one.1 Claim two.2 Example 3: Analysts review the data. See the journal "
                        "Foresight 4, no. 1 (March 2007) and Studies 4 (2012): 24-41."))
    us = units_of(pb, f"{BOOK}:1:0")
    assert [m for u in us for m in u.footnote_markers] == ["1", "2"]


def test_marker_after_closing_quote_never_leaves_a_quote_only_unit():
    long = "This sentence is long enough to fill most of the unit budget on its own, " * 4
    pb = book(blk(1, 0, f"{long}and it ends the quotation here.' 1 Next sentence follows."))
    us = units_of(pb, f"{BOOK}:1:0", max_unit_chars=320)
    assert all(re.search(r"\w", u.source_text) for u in us)
    assert any(u.footnote_markers == ["1"] and u.source_text.endswith("here.'") for u in us)


def test_flattened_matrix_cell_coordinates_are_not_markers():
    pb = book(blk(182, 0, "The scores are as follows: nil; nil; D,1; E,1; F,1; 1;\nand 5. The rest follows."))
    assert all(u.footnote_markers == [] for u in units_of(pb, f"{BOOK}:182:0"))


def test_spaced_marker_after_quote_or_word_only_as_next_number():
    pb = book(blk(1, 0, "One claim.1 It is about 'politics as organizing' 2 and diverse forms. "
                        "Another about Gramsci 3 and his readers. The chapter 4 begins here."))
    us = units_of(pb, f"{BOOK}:1:0")
    assert [m for u in us for m in u.footnote_markers] == ["1", "2", "3"]
    assert "The chapter 4 begins here." in " ".join(texts(us))


def test_whole_line_letter_spaced_word_without_book_attestation():
    pb = book(blk(1, 0, "A C K N O W L ED G M EN T S\nThe authors thank the reviewers.", "paragraph"),
              blk(2, 0, "A B C D E F G H I\nA Increase in use", "paragraph"))
    assert texts(units_of(pb, f"{BOOK}:1:0"))[0] == "ACKNOWLEDGMENTS"
    assert texts(units_of(pb, f"{BOOK}:2:0"))[0] == "A B C D E F G H I"  # matrix column labels


# ------------------------------------------------------------------ synthetic fixture (end to end)
def test_synthetic_fixture_book_end_to_end():
    data = json.loads((FIXTURES / "synthetic-book.json").read_text(encoding="utf-8"))
    pb = ParsedBook.model_validate(data["book"])
    units = build_translation_units(pb)
    got = [{"kind": u.kind, "text": u.source_text, "markers": u.footnote_markers,
            "blocks": len(u.block_ids)} for u in units]
    assert got == data["expected_units"]
