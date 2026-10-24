"""Parse digital-text PDFs into provenance-preserving ParsedBook blocks.

This is the deterministic pypdf fallback. It refuses image-only/sparse PDFs rather
than silently dropping pages; OCR is provided by the future Docling adapter.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator

import pypdf
from pypdf import PdfReader

from b2c_contracts import Block, BookUpload, PageRef, ParsedBook


class UnsupportedPDF(ValueError):
    """The source is not a readable PDF supported by this backend."""


class OCRRequired(UnsupportedPDF):
    """One or more pages lack a usable text layer and must go through OCR."""


_HEADING = re.compile(r"^(?:(?i:chapter|part|section)\s+[0-9ivxlcdm]+\b|(?:[0-9]+(?:\.[0-9]+)*[.)]?\s+).{2,}|[A-Z][A-Z0-9 ,:;()&/-]{3,})")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _outline_entries(reader: PdfReader) -> list[tuple[int, int, str]]:
    """Flatten PDF bookmarks to (zero-based page, depth, title), in reading order."""
    result: list[tuple[int, int, str]] = []

    def walk(items: list[Any], depth: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            title = str(getattr(item, "title", "")).strip()
            try:
                page = reader.get_destination_page_number(item)
            except (ValueError, TypeError, IndexError):
                continue
            if title and page is not None and page >= 0:
                result.append((page, depth, title))

    try:
        walk(reader.outline, 0)
    except (AttributeError, TypeError):
        return []
    # PDF outlines are intended to follow reading order; sorting also handles files
    # whose outline tree stores entries out of page order. Keep source order for ties.
    ordered = sorted(enumerate(result), key=lambda pair: (pair[1][0], pair[0]))
    return [entry for _, entry in ordered]


def _paragraphs(text: str) -> Iterator[str]:
    for part in re.split(r"\n\s*\n", text):
        cleaned = re.sub(r"[ \t]+", " ", part).strip()
        if cleaned:
            yield cleaned


def parse_pdf_book(upload: BookUpload, source: str | Path, *, min_page_chars: int = 8) -> ParsedBook:
    """Build a ParsedBook from a PDF whose pages have native text.

    `upload.file_sha256` is checked before parsing so metadata cannot be paired with
    a different document. Page numbers in PageRef are physical, one-based indices.
    """
    path = Path(source)
    if upload.mime != "application/pdf":
        raise UnsupportedPDF(f"pypdf backend only supports application/pdf, got {upload.mime}")
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise UnsupportedPDF("file does not have a PDF signature")
    actual_sha = _sha256(path)
    if actual_sha.lower() != upload.file_sha256.lower():
        raise UnsupportedPDF("uploaded file SHA-256 does not match BookUpload.file_sha256")

    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnsupportedPDF("password-protected PDFs are not supported by this backend")
        if not reader.pages:
            raise UnsupportedPDF("PDF contains no pages")
        pages = []
        for i, page in enumerate(reader.pages):
            text = (page.extract_text() or "").strip()
            try:
                has_images = bool(page.images)
            except Exception:
                has_images = False
            pages.append((i + 1, text, has_images))
    except UnsupportedPDF:
        raise
    except Exception as exc:
        raise UnsupportedPDF(f"could not read PDF: {exc}") from exc

    if not any(text for _, text, _ in pages):
        raise OCRRequired("PDF has no extractable text; all pages require Docling/OCR")
    sparse = [number for number, text, has_images in pages
              if has_images and len(re.sub(r"\s+", "", text)) < min_page_chars]
    if sparse:
        shown = ", ".join(map(str, sparse[:12]))
        extra = "…" if len(sparse) > 12 else ""
        raise OCRRequired(f"pages with insufficient native text require Docling/OCR: {shown}{extra}")

    bookmarks = _outline_entries(reader)
    by_page: dict[int, list[tuple[int, str]]] = {}
    for zero_page, depth, title in bookmarks:
        by_page.setdefault(zero_page + 1, []).append((depth + 1, title))

    # In the absence of PDF bookmarks, recognize only explicit, conservative
    # numbered/chapter-style headings. Arbitrary prose is never promoted to TOC.
    if not bookmarks:
        for page_no, text, _ in pages:
            for line in text.splitlines():
                line = line.strip()
                if line and len(line) <= 140 and _HEADING.match(line):
                    by_page.setdefault(page_no, []).append((1, line))

    blocks: list[Block] = []
    section_path: list[str] = []
    for page_no, text, _ in pages:
        seq = 0
        for level, title in by_page.get(page_no, []):
            section_path = section_path[: max(level - 1, 0)]
            section_path.append(title)
            blocks.append(Block(
                block_id=f"{upload.book_id}:{page_no}:{seq}", type="heading",
                level=level, text=title, refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                section_path=section_path.copy(), ocr=False,
            ))
            seq += 1
        for paragraph in _paragraphs(text):
            blocks.append(Block(
                block_id=f"{upload.book_id}:{page_no}:{seq}", type="paragraph", text=paragraph,
                refs=[PageRef(book_id=upload.book_id, page_no=page_no)],
                section_path=section_path.copy(), ocr=False,
            ))
            seq += 1

    options = {"backend": "pypdf-text", "min_page_chars": min_page_chars, "version": pypdf.__version__}
    toc_source = "pdf_outline" if bookmarks else "headings"
    return ParsedBook(
        book_id=upload.book_id, title=upload.title, role=upload.role,
        page_count=len(pages), toc_source=toc_source, blocks=blocks,
        parser={"name": "pypdf-text-fallback", "version": pypdf.__version__,
                "options_hash": hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()},
    )
