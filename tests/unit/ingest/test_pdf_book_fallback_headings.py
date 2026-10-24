"""pypdf fallback heading detection (no PDF bookmarks)."""
from __future__ import annotations

import pytest

from b2c_ingest.pdf_book import _build_blocks, _is_heading_line


@pytest.mark.parametrize("line", [
    "Chapter 1 Introduction", "CHAPTER IV", "2.1 Research Methods", "3. Results and Discussion",
    "4) Data Collection", "7 Soil Fertility", "INTRODUCTION",
])
def test_fallback_heading_lines_are_recognized(line):
    assert _is_heading_line(line)


@pytest.mark.parametrize("line", [
    "2 of the 10 respondents said yes",
    "12 See Smith, The Soil Book, p. 4.",
    "12 See Smith…",
    "3 Ibid.",
    "1. Mix the soil with water.",
    "Chapter 3 shows that yields rose sharply.",
    "NASA reported that the satellite failed",
    "2001 was a dry year for most farmers",
])
def test_fallback_heading_rejects_prose_footnotes_and_list_items(line):
    assert not _is_heading_line(line)


def _fallback(pages):
    return _build_blocks("b", pages, {}, detect_headings=True)


def test_text_above_mid_page_heading_keeps_previous_section():
    blocks = _fallback([
        (1, "1. Introduction\nOpening text of the introduction.", False),
        (2, "Tail of the introduction carried over.\n\n2. Methods\nMethod body text.", False),
    ])
    by_text = {block.text: block for block in blocks}
    assert [b.type for b in blocks] == ["heading", "paragraph", "paragraph", "heading", "paragraph"]
    assert by_text["Tail of the introduction carried over."].section_path == ["1. Introduction"]
    assert by_text["Method body text."].section_path == ["2. Methods"]
    page2 = [b.text for b in blocks if b.refs[0].page_no == 2]
    assert page2 == ["Tail of the introduction carried over.", "2. Methods", "Method body text."]
    assert [b.block_id for b in blocks if b.refs[0].page_no == 2] == ["b:2:0", "b:2:1", "b:2:2"]


def test_heading_lines_are_not_duplicated_in_paragraph_text():
    blocks = _fallback([(1, "Some lead text.\n2.1 Results\nThe results were clear.", False)])
    paragraphs = [b.text for b in blocks if b.type == "paragraph"]
    assert paragraphs == ["Some lead text.", "The results were clear."]


def test_numbered_prose_and_footnotes_stay_in_paragraphs():
    blocks = _fallback([(1, "Survey summary\n2 of the 10 respondents said yes\n\n12 See Smith, p. 4.", False)])
    assert all(b.type == "paragraph" for b in blocks)
    assert "2 of the 10 respondents said yes" in blocks[0].text
    assert blocks[1].text == "12 See Smith, p. 4."


def test_bookmarked_pages_keep_headings_at_top_and_text_intact():
    blocks = _build_blocks("b", [(1, "2.1 Results\nBody.", False)], {1: [(1, "Results")]},
                           detect_headings=False)
    assert [b.type for b in blocks] == ["heading", "paragraph"]
    assert blocks[1].text == "2.1 Results\nBody."
