from __future__ import annotations

import hashlib

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from b2c_contracts import BookUpload, PageRef, ParsedBook, Block
from b2c_ingest import OCRRequired, UnsupportedPDF, parse_pdf_book
from b2c_outline import InferredSection, OutlineUnavailable, outline_from_book, validate_outline_coverage


def make_text_pdf(path, text: str = "Chapter 1 Introduction\nThis is a probability paragraph with enough words.") -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_outline_item("Chapter 1 Introduction", 0)
    with path.open("wb") as output:
        writer.write(output)
    return path.read_bytes()


def upload_for(pdf: bytes) -> BookUpload:
    return BookUpload(
        book_id="book-main", role="spine", file_sha256=hashlib.sha256(pdf).hexdigest(),
        mime="application/pdf", title="Probability", rights_status="internal_test_only",
    )


def test_parse_pdf_preserves_hash_pages_and_bookmarks(tmp_path):
    path = tmp_path / "book.pdf"
    raw = make_text_pdf(path)
    parsed = parse_pdf_book(upload_for(raw), path)

    assert parsed.book_id == "book-main"
    assert parsed.page_count == 1
    assert parsed.toc_source == "pdf_outline"
    assert parsed.parser["name"] == "pypdf-text-fallback"
    assert parsed.blocks[0].type == "heading"
    assert parsed.blocks[0].refs == [PageRef(book_id="book-main", page_no=1)]
    assert any("probability paragraph" in block.text for block in parsed.blocks)


def test_parse_pdf_rejects_hash_mismatch(tmp_path):
    path = tmp_path / "book.pdf"
    raw = make_text_pdf(path)
    wrong_upload = upload_for(raw).model_copy(update={"file_sha256": "0" * 64})

    with pytest.raises(UnsupportedPDF, match="SHA-256"):
        parse_pdf_book(wrong_upload, path)


def test_parse_pdf_routes_scanned_document_to_ocr(tmp_path):
    path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as output:
        writer.write(output)
    raw = path.read_bytes()

    with pytest.raises(OCRRequired, match="no extractable text"):
        parse_pdf_book(upload_for(raw), path)


def test_outline_maps_headings_to_sequential_lesson_spans():
    refs = [PageRef(book_id="book-main", page_no=page) for page in (1, 2, 3, 4)]
    blocks = [
        Block(block_id="book-main:1:0", type="heading", level=1, text="Probability", refs=[refs[0]], section_path=["Probability"], ocr=False),
        Block(block_id="book-main:1:1", type="heading", level=2, text="Events", refs=[refs[0]], section_path=["Probability", "Events"], ocr=False),
        Block(block_id="book-main:2:0", type="paragraph", text="Event text", refs=[refs[1]], section_path=["Probability", "Events"], ocr=False),
        Block(block_id="book-main:3:0", type="heading", level=2, text="Random variables", refs=[refs[2]], section_path=["Probability", "Random variables"], ocr=False),
        Block(block_id="book-main:4:0", type="paragraph", text="Variable text", refs=[refs[3]], section_path=["Probability", "Random variables"], ocr=False),
    ]
    parsed = ParsedBook(book_id="book-main", title="Probability", role="spine", page_count=4,
                        toc_source="pdf_outline", blocks=blocks,
                        parser={"name": "test", "version": "1", "options_hash": "0" * 64})

    outline = outline_from_book(parsed, course_id="course-1", supplementary_book_ids=["book-extra"])
    module = outline.modules[0]
    assert module.title == "Probability"
    assert [lesson.title for lesson in module.lessons] == ["Events", "Random variables"]
    assert module.lessons[0].spine_span.start_block == "book-main:1:0"
    assert module.lessons[0].spine_span.end_block == "book-main:2:0"
    assert module.lessons[1].prior_lessons == [module.lessons[0].lesson_id]
    assert outline.supplementary_book_ids == ["book-extra"]
    assert outline.requires_review is True


def test_outline_includes_unheaded_front_matter_in_first_lesson():
    refs = [PageRef(book_id="book-main", page_no=page) for page in (1, 2)]
    blocks = [
        Block(block_id="front", type="page_header", text="Probability",
              refs=[refs[0]], section_path=[], ocr=False),
        Block(block_id="chapter", type="heading", level=1, text="Probability",
              refs=[refs[0]], section_path=["Probability"], ocr=False),
        Block(block_id="body", type="paragraph", text="Probability content",
              refs=[refs[1]], section_path=["Probability"], ocr=False),
    ]
    parsed = ParsedBook(book_id="book-main", title="Probability", role="spine", page_count=2,
                        toc_source="pdf_outline", blocks=blocks,
                        parser={"name": "test", "version": "1", "options_hash": "0" * 64})
    outline = outline_from_book(parsed, course_id="course-1")
    assert outline.modules[0].lessons[0].spine_span.start_block == "front"
    assert validate_outline_coverage(parsed, outline).ratio == 1.0


def test_outline_requires_review_when_no_structure_is_available():
    refs = [PageRef(book_id="book-main", page_no=1)]
    parsed = ParsedBook(
        book_id="book-main", title="No structure", role="spine", page_count=1,
        toc_source="headings",
        blocks=[
            Block(block_id="book-main:1:0", type="paragraph", text="First body block",
                  refs=refs, section_path=[], ocr=False),
            Block(block_id="book-main:1:1", type="paragraph", text="Second body block",
                  refs=refs, section_path=[], ocr=False),
        ],
        parser={"name": "test", "version": "1", "options_hash": "0" * 64},
    )
    with pytest.raises(OutlineUnavailable, match="no outline inference provider"):
        outline_from_book(parsed, course_id="course-1")

    outline = outline_from_book(
        parsed, course_id="course-1",
        infer_sections=lambda book: [
            InferredSection("Foundations", book.blocks[0].block_id, book.blocks[0].block_id),
            InferredSection("Applications", book.blocks[1].block_id, book.blocks[1].block_id),
        ],
    )
    lessons = [lesson for module in outline.modules for lesson in module.lessons]
    assert [lesson.provenance for lesson in lessons] == ["llm_inferred", "llm_inferred"]
    assert outline.requires_review is True
    assert lessons[1].prior_lessons == [lessons[0].lesson_id]
    coverage = validate_outline_coverage(parsed, outline)
    assert coverage.ratio == 1.0


def test_outline_coverage_rejects_gap_and_reports_98_percent_threshold():
    refs = [PageRef(book_id="book-main", page_no=1)]
    blocks = [
        Block(block_id=f"book-main:1:{i}", type="paragraph", text=text,
              refs=refs, section_path=[], ocr=False)
        for i, text in enumerate(["x" * 99, "y" * 100])
    ]
    parsed = ParsedBook(book_id="book-main", title="No structure", role="spine", page_count=1,
                        toc_source="headings", blocks=blocks,
                        parser={"name": "test", "version": "1", "options_hash": "0" * 64})
    with pytest.raises(OutlineUnavailable, match="minimum is 98%"):
        outline_from_book(
            parsed, course_id="course-1",
            infer_sections=lambda book: [InferredSection("Mostly covered", book.blocks[0].block_id, book.blocks[0].block_id)],
        )
