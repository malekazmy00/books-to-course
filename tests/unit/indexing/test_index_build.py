"""T-IDX-04 (lesson parity, truncation = 0, no heading-only chunk) and T-IDX-05 on the v2 builder."""
import pytest

from b2c_contracts import BlockV2, Note, PageInfo, ParsedBookV2, StructuredChunk, ChunkPart, parsed_book_v1_from_v2
from b2c_contracts.models import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_indexing.index_build import CHUNKER_VERSION, check_lesson_parity, index_book

B = "bk"
PROSE = ("The hegemony of a class rests on consent as much as on force, and the state organises "
         "that consent through schools, churches and the press. ")


def blk(seq, text, type="paragraph", zone="body", page=1, **kw):
    if type == "heading":
        kw.setdefault("level", 2)
    return BlockV2(block_id=f"{B}:b{seq:05d}", type=type, zone=zone, text=text, raw_text=text,
                   refs=[PageRef(book_id=B, page_no=page)], section_path=kw.pop("section_path", ["Ch"]),
                   ocr=False, **kw)


def book(blocks, notes=(), pages=3, role="spine"):
    return ParsedBookV2(book_id=B, title="Book", role=role, page_count=pages, toc_source="pdf_outline",
                        pages=[PageInfo(page_no=i, status="content") for i in range(1, pages + 1)],
                        blocks=blocks, notes=list(notes), parser={"name": "fixture", "version": "1"},
                        normalizer={"name": "n", "version": "1"})


def outline(*spans, book_id=B):
    lessons = [Lesson(lesson_id=f"m1.l{i}", title=f"Lesson {i}", order=i,
                      spine_span=LessonSpan(start_block=a, end_block=z, pages=(1, 1)), provenance="pdf_outline")
               for i, (a, z) in enumerate(spans, 1)]
    return CourseOutline(course_id="c", spine_book_id=book_id, supplementary_book_ids=[], requires_review=False,
                         modules=[Module(module_id="m1", title="Module", order=1, lessons=lessons)])


def bid(i):
    return f"{B}:b{i:05d}"


def coverage(chunks, block_id):
    return sorted((p.start, p.end) for c in chunks for p in c.parts if p.block_id == block_id)


def test_every_body_character_of_a_lesson_is_in_exactly_one_chunk():
    blocks = [blk(1, "Lesson One", type="heading"), *[blk(i, PROSE * 3) for i in range(2, 8)],
              blk(8, "Lesson Two", type="heading"), blk(9, PROSE)]
    build = index_book(book(blocks), outline=outline((bid(1), bid(7)), (bid(8), bid(9))),
                       target_tokens=60, max_tokens=120, min_tokens=20)
    assert build.report.accepted, build.report.failed
    for b in blocks:
        assert coverage(build.chunks, b.block_id) == [(0, len(b.text))] or \
            _tiles(coverage(build.chunks, b.block_id), len(b.text))
    assert build.report.metrics["orphan_body_blocks"] == 0
    assert build.report.metrics["duplicated_body_blocks"] == 0
    assert {c.lesson_id for c in build.chunks} == {"m1.l1", "m1.l2"}
    assert all(c.chunker_version == CHUNKER_VERSION and c.text_sha256 for c in build.chunks)


def _tiles(ranges, length):
    pos = 0
    for a, z in ranges:
        if a != pos:
            return False
        pos = z
    return pos == length


def test_no_heading_only_chunk_and_headings_attach_forward_or_back():
    blocks = [blk(1, "Opening", type="heading"), blk(2, PROSE), blk(3, "Trailing heading", type="heading")]
    build = index_book(book(blocks), outline=outline((bid(1), bid(3))))
    assert len(build.chunks) == 1
    assert build.chunks[0].block_ids == [bid(1), bid(2), bid(3)]
    assert build.report.metrics["heading_only_chunks"] == 0


def test_heading_only_lesson_fails_the_gate():
    blocks = [blk(1, "Part One", type="heading"), blk(2, "Chapter 1", type="heading"), blk(3, PROSE)]
    build = index_book(book(blocks), outline=outline((bid(1), bid(1)), (bid(2), bid(3))))
    assert build.report.metrics["lessons_without_body"] == 1
    assert not build.report.accepted and "lessons_without_body" in build.report.failed
    assert all(c.lesson_id == "m1.l2" for c in build.chunks)


def test_long_block_is_split_on_sentences_without_losing_text():
    long_text = " ".join(f"Sentence number {i} explains one more idea about consent and force." for i in range(80))
    blocks = [blk(1, long_text)]
    build = index_book(book(blocks), outline=outline((bid(1), bid(1))), target_tokens=100, max_tokens=150)
    assert len(build.chunks) > 1
    assert all(c.token_count <= 150 for c in build.chunks)
    assert _tiles(coverage(build.chunks, bid(1)), len(long_text))
    assert build.report.metrics["truncated_blocks"] == 0
    assert all(c.text.startswith("Sentence number") for c in build.chunks)


def test_tables_stay_atomic():
    table = "\n".join(f"Row {i} | value {i} | note {i}" for i in range(60))
    build = index_book(book([blk(1, PROSE), blk(2, table, type="table")]), outline=outline((bid(1), bid(2))),
                       target_tokens=50, max_tokens=80, min_tokens=20, hard_limit_tokens=4000)
    holders = [c for c in build.chunks if bid(2) in c.block_ids]
    assert len(holders) == 1 and holders[0].parts[-1] == ChunkPart(block_id=bid(2), start=0, end=len(table))


def test_non_body_never_enters_lesson_content_and_notes_are_separate_evidence():
    blocks = [blk(1, "Conclusion", type="heading"),
              blk(2, PROSE, note_refs=[{"label": "1", "offset": 5, "note_id": f"{B}:n0001"}]),
              blk(3, "Notes", type="heading", zone="back_matter"),
              blk(4, "Prison Notebooks, Q13, §17.", type="endnote", zone="back_matter", note_label="1"),
              blk(5, "hegemony, 12, 40-42, 77", type="index_entry", zone="back_matter"),
              blk(6, "12", type="page_number", zone="furniture")]
    notes = [Note(note_id=f"{B}:n0001", kind="endnote", label="1", block_ids=[bid(4)], cited_by=[bid(2)])]
    # the (old) outline lets the last lesson run to the end of the book, over notes and index
    build = index_book(book(blocks, notes), outline=outline((bid(1), bid(6))))
    body = [c for c in build.chunks if c.evidence_class == "body"]
    note = [c for c in build.chunks if c.evidence_class == "note"]
    assert [c.block_ids for c in body] == [[bid(1), bid(2)]]
    assert [c.block_ids for c in note] == [[bid(4)]] and note[0].lesson_id == "m1.l1"
    assert build.report.metrics["excluded_index"] == 1
    assert build.report.metrics["excluded_furniture"] == 1
    assert build.report.metrics["non_body_in_content"] == 0
    assert build.report.accepted


def test_quarantined_blocks_are_reported_and_fail_the_gate_above_the_ratio():
    bad = "af t er the spr i ng the art i cul at i on of s truggl es in the war of pos i t i on"
    blocks = [blk(1, PROSE), blk(2, bad), blk(3, PROSE)]
    build = index_book(book(blocks), outline=outline((bid(1), bid(3))))
    assert [q["block_id"] for q in build.quarantined] == [bid(2)]
    assert all(bid(2) not in c.block_ids for c in build.chunks)
    assert build.report.metrics["orphan_body_blocks"] == 0  # quarantined blocks are accounted, not orphans
    assert not build.report.accepted and "quarantined_ratio" in build.report.failed


def test_chunk_id_changes_with_text_even_when_block_ids_do_not():
    a = index_book(book([blk(1, PROSE)]), outline=outline((bid(1), bid(1))))
    b = index_book(book([blk(1, PROSE.replace("force", "coercion"))]), outline=outline((bid(1), bid(1))))
    assert a.chunks[0].block_ids == b.chunks[0].block_ids
    assert a.chunks[0].chunk_id != b.chunks[0].chunk_id


def test_outline_in_v1_projection_ids_is_honoured():
    blocks = [blk(1, "Lesson", type="heading", page=1), blk(2, PROSE, page=1), blk(3, PROSE, page=2)]
    v2 = book(blocks)
    v1 = parsed_book_v1_from_v2(v2)
    ids = [b.block_id for b in v1.blocks]
    build = index_book(v2, outline=outline((ids[0], ids[1]), (ids[2], ids[2])))
    assert [c.block_ids for c in build.chunks] == [ids[:2], ids[2:]]
    assert build.report.source_format == "parsed-book/2"


def test_supplementary_book_uses_section_paths_and_no_outline():
    blocks = [blk(1, PROSE, section_path=["A"]), blk(2, PROSE, section_path=["B"])]
    build = index_book(book(blocks, role="supplementary"))
    assert [c.section_path for c in build.chunks] == [["A"], ["B"]]
    assert all(c.lesson_id is None and c.role == "supplementary" for c in build.chunks)
    assert build.report.metrics["lessons_without_body"] is None


def test_v1_book_is_indexed_with_the_same_gate():
    v1 = ParsedBook(book_id=B, title="T", role="spine", page_count=1, toc_source="headings",
                    parser={"name": "fixture", "version": "1"}, blocks=[
                        Block(block_id="x1", type="page_header", text="HEAD", refs=[PageRef(book_id=B, page_no=1)],
                              section_path=[], ocr=False),
                        Block(block_id="x2", type="paragraph", text=PROSE, refs=[PageRef(book_id=B, page_no=1)],
                              section_path=[], ocr=False)])
    build = index_book(v1, outline=outline(("x1", "x2")))
    assert [c.block_ids for c in build.chunks] == [["x2"]]
    assert build.report.source_format == "parsed-book/1"


def _chunk(cid, parts):
    text = "x"
    return StructuredChunk(chunk_id=cid, book_id=B, role="spine", section_path=[], pages=[1], text=text,
                           token_count=1, embedding_model="unassigned", lesson_id="m1.l1",
                           block_ids=list(dict.fromkeys(p[0] for p in parts)),
                           parts=[ChunkPart(block_id=b, start=s, end=e) for b, s, e in parts])


def test_parity_check_fails_on_orphans_overlaps_and_partial_coverage():
    lesson_blocks = {"m1.l1": {"a": 10, "b": 10, "c": 10}}
    result = check_lesson_parity(lesson_blocks, [_chunk("1", [("a", 0, 10), ("b", 0, 6)]),
                                                 _chunk("2", [("b", 4, 10)])])
    assert result["orphan_body_blocks"] == 1  # c
    assert result["duplicated_body_blocks"] == 1  # b chars 4..6 twice
    ok = check_lesson_parity({"m1.l1": {"a": 10}}, [_chunk("1", [("a", 0, 10)])])
    assert ok == {"orphan_body_blocks": 0, "duplicated_body_blocks": 0, "truncated_blocks": 0}
    partial = check_lesson_parity({"m1.l1": {"a": 10}}, [_chunk("1", [("a", 0, 7)])])
    assert partial["truncated_blocks"] == 1


def test_spans_out_of_order_are_rejected():
    blocks = [blk(1, PROSE), blk(2, PROSE)]
    with pytest.raises(ValueError, match="order"):
        index_book(book(blocks), outline=outline((bid(2), bid(2)), (bid(1), bid(1))))
