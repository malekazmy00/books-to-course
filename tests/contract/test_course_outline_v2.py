"""CourseOutline v2 (SCHEMA 1.5.0, R4 / audit 02 §5-6). Negative controls MUST raise.

A v2 outline partitions every block of a ParsedBook v2 into lesson spans and explicitly excluded
ranges (each with a zone and a reason): included + excluded = 100% by construction. Lessons carry
a stable id, a structural level, the source title next to the cleaned display title, and the
evidence each node came from (pdf_outline / printed_toc / heading / llm_inferred).
``course_outline_v1_from_v2`` projects it onto the v1 contract (ids of the v1 ParsedBook
projection) for consumers that have not migrated.
"""
import pytest
from pydantic import ValidationError

import b2c_contracts as c

B = "bk"


def bid(seq: int) -> str:
    return f"{B}:b{seq:05d}"


def span(a, b, pages=(1, 1)):
    return c.OutlineSpanV2(start_block=bid(a), end_block=bid(b), pages=pages)


def lesson(lid, a, b, order=1, **kw):
    base = dict(lesson_id=lid, order=order, level=2, kind="section", source="pdf_outline",
                source_title="1.1 Our Vision", display_title="1.1 Our Vision", title_source="pdf_outline",
                anchor_block=bid(a), spine_span=span(a, b), word_count=900, size_status="ok")
    return c.LessonV2(**(base | kw))


def module(mid, lessons, order=1, **kw):
    base = dict(module_id=mid, order=order, level=1, source="pdf_outline", source_title="Chapter 1",
                display_title="Chapter 1", title_source="pdf_outline", anchor_block=lessons[0].anchor_block,
                lessons=lessons)
    return c.ModuleV2(**(base | kw))


def excluded(a, b, reason="copyright", zone="front_matter"):
    return c.ExcludedRange(start_block=bid(a), end_block=bid(b), pages=(1, 1), zone=zone, reason=reason,
                           title="Copyright", rule="zone:front_matter")


POLICY = c.LessonSizePolicy(policy_id="words-600-4000", min_words=600, max_words=4000)


def outline(modules, excl=(), n=10, **kw):
    base = dict(course_id="crs", spine_book_id=B, block_count=n, builder={"name": "b2c-outline-v2", "version": "1"},
                structure_source="pdf_outline", size_policy=POLICY, modules=list(modules), excluded_ranges=list(excl))
    return c.CourseOutlineV2(**(base | kw))


def valid():
    return outline([module("ch01-abc123", [lesson("ch01-abc123.intro", 2, 4, kind="chapter_intro"),
                                           lesson("ch01-abc123.s1-def456", 5, 7, order=2)])],
                   [excluded(0, 1), excluded(8, 9, "endnotes", "back_matter")])


def test_schema_version_bumped_for_outline_v2():
    # 1.5.0 CourseOutline v2; 1.5.1 acceptance by system gates (owner decision: no human reviewer);
    # later merges bump further (1.6.0 = + indexing v2)
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 5, 1)
    o = valid()
    assert o.format == "course-outline/2" and o.requires_review is True


def test_blocks_are_partitioned_lessons_plus_exclusions():
    o = valid()
    assert o.lesson_count == 2
    with pytest.raises(ValidationError, match="gap"):  # block 8 is neither included nor excluded
        outline([module("m", [lesson("m.a", 2, 7)])], [excluded(0, 1), excluded(9, 9, "index", "back_matter")])
    with pytest.raises(ValidationError, match="overlap"):
        outline([module("m", [lesson("m.a", 1, 9)])], [excluded(0, 1)])
    with pytest.raises(ValidationError, match="block_count"):  # last block not covered
        outline([module("m", [lesson("m.a", 0, 8)])])


def test_partition_follows_the_books_first_block_id():
    # S2 numbers blocks from b00001: the partition is checked from the first block id on
    o = outline([module("m", [lesson("m.a", 1, 9)])], [excluded(10, 10, "index", "back_matter")], n=10)
    assert o.lesson_count == 1
    with pytest.raises(ValidationError, match="block_count"):
        outline([module("m", [lesson("m.a", 1, 9)])], n=10)


def test_tree_order_is_reading_order_and_orders_are_dense():
    with pytest.raises(ValidationError, match="reading order"):
        outline([module("m", [lesson("m.b", 5, 9, order=1), lesson("m.a", 0, 4, order=2)])])
    with pytest.raises(ValidationError, match="order"):
        outline([module("m", [lesson("m.a", 0, 4, order=1), lesson("m.b", 5, 9, order=3)])])


def test_ids_unique_and_stable_shape():
    with pytest.raises(ValidationError, match="unique"):
        outline([module("m", [lesson("m.a", 0, 4), lesson("m.a", 5, 9, order=2)])])
    with pytest.raises(ValidationError):
        lesson("M 1/a", 0, 4)  # ids are lowercase slug-like tokens, usable in keys and paths


def test_spans_belong_to_the_spine_book():
    with pytest.raises(ValidationError, match="spine"):
        outline([module("m", [lesson("m.a", 0, 9)])], spine_book_id="other")
    with pytest.raises(ValidationError):
        c.OutlineSpanV2(start_block="bk:3:0", end_block=bid(4), pages=(1, 1))  # v1 id
    with pytest.raises(ValidationError, match="reversed"):
        c.OutlineSpanV2(start_block=bid(5), end_block=bid(4), pages=(1, 1))


def test_titles_are_single_line_and_size_violations_are_documented():
    with pytest.raises(ValidationError, match="line"):
        lesson("m.a", 0, 4, display_title="STRUCTURED\nANALYTIC")
    with pytest.raises(ValidationError, match="size_exception"):
        lesson("m.a", 0, 4, word_count=80, size_status="below_min")
    ok = lesson("m.a", 0, 4, word_count=80, size_status="below_min", size_exception="chapter_intro")
    assert ok.size_exception == "chapter_intro"
    with pytest.raises(ValidationError, match="anchor"):
        lesson("m.a", 0, 4, anchor_block=bid(6))


def test_prior_lessons_must_refer_to_earlier_lessons():
    with pytest.raises(ValidationError, match="prior"):
        outline([module("m", [lesson("m.a", 0, 4, prior_lessons=["m.b"]), lesson("m.b", 5, 9, order=2)])])


def test_size_policy_bounds():
    with pytest.raises(ValidationError):
        c.LessonSizePolicy(policy_id="x", min_words=500, max_words=400)


def test_quality_report_cannot_accept_failed_checks():
    ok = c.QualityCheck(name="coverage", value=1.0, op="==", threshold=1.0, passed=True)
    bad = c.QualityCheck(name="malformed_titles", value=2, op="==", threshold=0, passed=False)
    r = c.OutlineQualityReport(book_id=B, report_version="1", outline_sha256="a" * 64, reference="pdf_outline",
                               metrics={}, checks=[ok, bad], accepted=False)
    assert r.failed == ["malformed_titles"]
    with pytest.raises(ValidationError):
        c.OutlineQualityReport(book_id=B, report_version="1", outline_sha256="a" * 64, reference=None,
                               metrics={}, checks=[ok, bad], accepted=True)


# ----------------------------------------------------------------------------- v1 projection
def _book(n=10):
    blocks = []
    for i in range(n):
        page = 1 if i < 5 else 2
        kind = "heading" if i in (2, 5) else "paragraph"
        blocks.append(c.BlockV2(block_id=bid(i), type=kind, level=1 if kind == "heading" else None,
                                zone="front_matter" if i < 2 else "body", text=f"t{i}", raw_text=f"t{i}",
                                refs=[c.PageRef(book_id=B, page_no=page)], section_path=[], ocr=False))
    return c.ParsedBookV2(book_id=B, title="T", role="spine", page_count=2, toc_source="pdf_outline",
                          pages=[c.PageInfo(page_no=p, status="content") for p in (1, 2)], blocks=blocks,
                          parser={"name": "x"}, normalizer={"name": "n", "version": "1"})


def test_v1_projection_maps_ids_through_the_parsed_book_v1_projection():
    book = _book()
    o = outline([module("ch01-abc123", [lesson("ch01-abc123.intro", 2, 4, kind="chapter_intro",
                                               display_title="Chapter 1 • Introduction"),
                                        lesson("ch01-abc123.s1-def456", 5, 9, order=2, spine_span=span(5, 9, (2, 2)))])],
                [excluded(0, 1)])
    v1 = c.course_outline_v1_from_v2(o, book)
    v1_ids = [b.block_id for b in c.parsed_book_v1_from_v2(book).blocks]
    lessons = [l for m in v1.modules for l in m.lessons]
    assert [l.lesson_id for l in lessons] == ["ch01-abc123.intro", "ch01-abc123.s1-def456"]
    assert lessons[0].title == "Chapter 1 • Introduction" and lessons[0].prior_lessons == []
    assert (lessons[0].spine_span.start_block, lessons[0].spine_span.end_block) == (v1_ids[2], v1_ids[4])
    assert lessons[1].spine_span.pages == (2, 2)
    assert v1.excluded_prefix_block_ids == v1_ids[:2] and v1.requires_review is True
    with pytest.raises(ValueError, match="block_count"):
        c.course_outline_v1_from_v2(outline([module("m", [lesson("m.a", 0, 10)])], n=11), book)
