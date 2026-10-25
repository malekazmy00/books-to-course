"""ParsedBook v2 (SCHEMA 1.3.0, R3 / audit 01 §5-6). Negative controls MUST raise.

v2 blocks are paragraphs, not page slices: one block may span pages (several PageRefs with
bbox and printed label), carries a zone and an explicit apparatus type, keeps the parser's
raw text next to the normalized text, and links in-text note markers to note objects.
``parsed_book_v1_from_v2`` projects v2 to the v1 contract for consumers not yet migrated.
"""
import pytest
from pydantic import ValidationError

import b2c_contracts as c

B = "bk"


def ref(page, label=None, bbox=(10.0, 20.0, 300.0, 400.0)):
    return c.PageRef(book_id=B, page_no=page, printed_label=label, bbox=bbox)


def block(seq, **kw):
    base = dict(block_id=f"{B}:b{seq:05d}", type="paragraph", zone="body", text="Text.",
                raw_text="Text.", refs=[ref(1)], section_path=["Ch 1"], ocr=False)
    return c.BlockV2(**(base | kw))


def pages(n=3):
    return [c.PageInfo(page_no=i, printed_label=str(i), status="content") for i in range(1, n + 1)]


def book(blocks, notes=(), outline=(), n=3, **kw):
    base = dict(book_id=B, title="T", role="spine", page_count=n, toc_source="pdf_outline",
                pages=pages(n), outline=list(outline), blocks=list(blocks), notes=list(notes),
                parser={"name": "docling", "version": "2.131.0"},
                normalizer={"name": "b2c-normalize", "version": "1"})
    return c.ParsedBookV2(**(base | kw))


def test_schema_version_bumped_for_v2():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 4, 0)  # v2 arrived in 1.4.0
    assert book([block(1)]).format == "parsed-book/2"


def test_block_may_span_pages_and_keeps_raw_text():
    b = block(1, text="A sentence that continues on the next page.",
              raw_text="A sentence that con-\ntinues on the next page.",
              refs=[ref(1, "ix"), ref(2, "x")], normalization=["dehyphenated", "joined_page_break"])
    pb = book([b])
    assert [r.page_no for r in pb.blocks[0].refs] == [1, 2]
    assert pb.blocks[0].raw_text != pb.blocks[0].text


def test_heading_needs_level_and_level_only_on_headings():
    with pytest.raises(ValidationError):
        block(1, type="heading")
    with pytest.raises(ValidationError):
        block(1, level=2)
    assert block(1, type="heading", level=2, level_source="pdf_outline").level == 2


def test_furniture_zone_iff_furniture_type():
    with pytest.raises(ValidationError):
        block(1, type="running_header", zone="body")
    with pytest.raises(ValidationError):
        block(1, type="paragraph", zone="furniture")
    assert block(1, type="page_number", zone="furniture", text="12").zone == "furniture"


def test_note_label_only_on_notes():
    with pytest.raises(ValidationError):
        block(1, note_label="3")
    assert block(1, type="endnote", zone="back_matter", note_label="3").note_label == "3"


def test_note_ref_offset_inside_text():
    with pytest.raises(ValidationError):
        block(1, text="abc", note_refs=[c.NoteRef(label="1", offset=4)])


def test_block_ids_unique_and_refs_within_book():
    with pytest.raises(ValidationError):
        book([block(1), block(1)])
    with pytest.raises(ValidationError):
        book([block(1, refs=[ref(9)])])
    with pytest.raises(ValidationError):  # ref of another book
        book([block(1, refs=[c.PageRef(book_id="other", page_no=1)])])


def test_pages_cover_the_book_in_order():
    with pytest.raises(ValidationError):
        book([block(1)], n=3, pages=pages(2))
    with pytest.raises(ValidationError):
        book([block(1)], n=2, pages=list(reversed(pages(2))))


def test_note_links_must_resolve():
    note_block = block(2, type="endnote", zone="back_matter", note_label="1", text="Ibid., 4.", raw_text="1. Ibid., 4.")
    body = block(1, text="A claim.", note_refs=[c.NoteRef(label="1", offset=8, note_id="bk:n0001")])
    note = c.Note(note_id="bk:n0001", kind="endnote", label="1", block_ids=[note_block.block_id],
                  cited_by=[body.block_id])
    assert book([body, note_block], notes=[note]).notes[0].cited_by == [body.block_id]
    with pytest.raises(ValidationError):  # dangling link
        book([body.model_copy(update={"note_refs": [c.NoteRef(label="1", offset=8, note_id="bk:n9")]}),
              note_block], notes=[note])
    with pytest.raises(ValidationError):  # note text must be a note block
        book([body, note_block], notes=[note.model_copy(update={"block_ids": [body.block_id]})])


def test_outline_entry_points_to_existing_block():
    h = block(1, type="heading", level=1, level_source="pdf_outline", text="Introduction")
    ok = c.OutlineEntry(title="Introduction", level=1, page_no=1, block_id=h.block_id)
    assert book([h], outline=[ok]).outline[0].block_id == h.block_id
    with pytest.raises(ValidationError):
        book([h], outline=[ok.model_copy(update={"block_id": "bk:b99999"})])


def test_v1_adapter_projects_types_ids_and_markers():
    blocks = [
        block(1, type="running_header", zone="furniture", text="Running head", refs=[ref(1)]),
        block(2, type="heading", level=1, level_source="pdf_outline", text="Introduction", refs=[ref(1)]),
        block(3, text="A claim that runs over the page.", refs=[ref(1), ref(2)],
              note_refs=[c.NoteRef(label="1", offset=32, note_id="bk:n0001")]),
        block(4, type="page_number", zone="furniture", text="2", refs=[ref(2)]),
        block(5, type="endnote", zone="back_matter", note_label="1", text="Ibid., 4.", refs=[ref(3)]),
        block(6, type="index_entry", zone="back_matter", text="Gramsci, 4, 12", refs=[ref(3)]),
    ]
    note = c.Note(note_id="bk:n0001", kind="endnote", label="1", block_ids=[blocks[4].block_id],
                  cited_by=[blocks[2].block_id])
    v1 = c.parsed_book_v1_from_v2(book(blocks, notes=[note]))
    assert isinstance(v1, c.ParsedBook)
    assert [b.type for b in v1.blocks] == ["page_header", "heading", "paragraph", "page_footer",
                                           "footnote", "list_item"]
    assert [b.block_id for b in v1.blocks] == ["bk:1:0", "bk:1:1", "bk:1:2", "bk:2:0", "bk:3:0", "bk:3:1"]
    # the marker goes back where it stood, glued, so v1 consumers can still find it
    assert v1.blocks[2].text == "A claim that runs over the page.1"
    assert [r.page_no for r in v1.blocks[2].refs] == [1, 2]
    # the endnote keeps its printed label in v1 text
    assert v1.blocks[4].text == "1. Ibid., 4."
    assert v1.parser["format"] == "parsed-book/2->1"


def check(name="word_recall", value=0.999, op=">=", threshold=0.995, passed=True):
    return c.QualityCheck(name=name, value=value, op=op, threshold=threshold, passed=passed)


def test_quality_check_verdict_must_match_value():
    with pytest.raises(ValidationError):
        check(value=0.9, passed=True)
    with pytest.raises(ValidationError):
        check(value=None, passed=False)  # not measurable is not a failure, it is "n/a"
    assert check(value=None, passed=None).passed is None
    assert check(name="ligatures", value=0, op="<=", threshold=0, passed=True).passed


def test_quality_report_cannot_accept_a_failed_gate():
    failed = check(value=0.9, passed=False)
    with pytest.raises(ValidationError):
        c.IngestQualityReport(book_id=B, report_version="1", metrics={}, checks=[failed], accepted=True)
    report = c.IngestQualityReport(book_id=B, report_version="1", metrics={"word_recall": 0.9},
                                   checks=[failed, check(value=None, passed=None)], accepted=False)
    assert report.failed == ["word_recall"]
    with pytest.raises(ValidationError):  # an empty gate is not a pass
        c.IngestQualityReport(book_id=B, report_version="1", metrics={}, checks=[], accepted=True)


def test_quality_report_names_the_pages_behind_a_check():
    """SCHEMA 1.19.0 (D-043): page_findings lists the pages each per-page check found, so a
    failed gate points a reviewer at pages, not only at a book-level number."""
    failed = check(name="pages_below_agreement", value=2, op="<=", threshold=0, passed=False)
    report = c.IngestQualityReport(book_id=B, report_version="2", metrics={}, checks=[failed], accepted=False,
                                   page_findings={"pages_below_agreement": [12, 40]})
    assert report.page_findings["pages_below_agreement"] == [12, 40]
    assert c.IngestQualityReport(book_id=B, report_version="1", metrics={}, checks=[failed],
                                 accepted=False).page_findings == {}
    with pytest.raises(ValidationError):  # findings belong to a check of the report
        c.IngestQualityReport(book_id=B, report_version="2", metrics={}, checks=[failed], accepted=False,
                              page_findings={"no_such_check": [1]})
    with pytest.raises(ValidationError):  # physical pages are 1-based
        c.IngestQualityReport(book_id=B, report_version="2", metrics={}, checks=[failed], accepted=False,
                              page_findings={"pages_below_agreement": [0]})
