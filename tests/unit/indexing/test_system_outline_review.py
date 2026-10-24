from datetime import UTC
import pytest
from b2c_contracts import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_outline import outline_from_book
from b2c_outline.review import (OutlineReviewRejected, review_outline,
                                   include_leading_blocks_in_first_lesson)

def make_case(*, source="headings", title="Lesson", end="b2", pages=(1, 2)):
    parsed = ParsedBook(book_id="b", title="Book", role="spine", page_count=2,
        toc_source=source, blocks=[
            Block(block_id="b1", type="heading", text="Lesson", level=1,
                  refs=[PageRef(book_id="b", page_no=1)], section_path=["Lesson"], ocr=False),
            Block(block_id="b2", type="paragraph", text="Source body.",
                  refs=[PageRef(book_id="b", page_no=2)], section_path=["Lesson"], ocr=False)],
        parser={"name":"test", "version":"1", "options_hash":"x"})
    outline = CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[],
        requires_review=True, modules=[Module(module_id="m1", title="Lesson", order=1, lessons=[
            Lesson(lesson_id="m1.l1", title=title, order=1,
                   spine_span=LessonSpan(start_block="b1", end_block=end, pages=pages),
                   provenance=source, prior_lessons=[])
        ])])
    return parsed, outline

def test_structural_system_review_returns_reproducible_provenance():
    parsed, outline = make_case()
    first = review_outline(parsed, outline)
    assert first.status == "approved" and first.evidence_sha256 == review_outline(parsed, outline).evidence_sha256
    assert "spans_contiguous_full_coverage" in first.checks

def test_structural_system_review_rejects_inferred_or_title_mismatch():
    parsed, outline = make_case(source="llm_inferred")
    with pytest.raises(OutlineReviewRejected, match="LLM-inferred"):
        review_outline(parsed, outline)
    parsed, outline = make_case(title="Invented title")
    with pytest.raises(OutlineReviewRejected, match="title"):
        review_outline(parsed, outline)

def test_structural_system_review_rejects_incomplete_coverage_and_wrong_pages():
    parsed, outline = make_case(end="b1", pages=(1, 1))
    with pytest.raises(OutlineReviewRejected, match="substantive body text"):
        review_outline(parsed, outline)
    parsed, outline = make_case(pages=(1, 1))
    with pytest.raises(OutlineReviewRejected, match="page span"):
        review_outline(parsed, outline)

def test_system_review_repairs_only_unheaded_front_matter_prefix():
    parsed, outline = make_case()
    prefix = Block(block_id="front", type="page_header", text="Lesson",
                   refs=[PageRef(book_id="b", page_no=1)], section_path=[], ocr=False)
    parsed = parsed.model_copy(update={"blocks": [prefix, *parsed.blocks]})
    repaired = include_leading_blocks_in_first_lesson(parsed, outline)
    assert repaired.modules[0].lessons[0].spine_span.start_block == "front"
    assert repaired.modules[0].lessons[0].spine_span.pages == (1, 2)
    assert review_outline(parsed, repaired).status == "approved"


def test_system_review_does_not_swallow_a_leading_heading():
    parsed, outline = make_case()
    prefix = Block(block_id="front-heading", type="heading", level=1, text="Unmapped",
                   refs=[PageRef(book_id="b", page_no=1)], section_path=["Unmapped"], ocr=False)
    parsed = parsed.model_copy(update={"blocks": [prefix, *parsed.blocks]})
    unchanged = include_leading_blocks_in_first_lesson(parsed, outline)
    assert unchanged.modules[0].lessons[0].spine_span.start_block == "b1"
    with pytest.raises(OutlineReviewRejected, match="contiguous"):
        review_outline(parsed, unchanged)


def test_system_review_accepts_substantive_introduction_after_publisher_preface():
    parsed = ParsedBook(book_id="b", title="Book", role="spine", page_count=2,
        toc_source="headings", blocks=[
            Block(block_id="pub", type="heading", text="Publisher's Preface", level=1,
                  refs=[PageRef(book_id="b", page_no=1)], section_path=[], ocr=False),
            Block(block_id="note", type="paragraph", text="Publisher description of this edition.",
                  refs=[PageRef(book_id="b", page_no=1)], section_path=[], ocr=False),
            Block(block_id="intro", type="heading", text="Introduction", level=1,
                  refs=[PageRef(book_id="b", page_no=2)], section_path=[], ocr=False),
            Block(block_id="body", type="paragraph", text="This introduction explains the core analytical method.",
                  refs=[PageRef(book_id="b", page_no=2)], section_path=["Introduction"], ocr=False),
        ], parser={"name":"test", "version":"1", "options_hash":"x"})
    outline = outline_from_book(parsed, course_id="c")
    assert outline.excluded_prefix_block_ids == ["pub", "note"]
    assert outline.modules[0].title == "Introduction"
    assert review_outline(parsed, outline).status == "approved"
