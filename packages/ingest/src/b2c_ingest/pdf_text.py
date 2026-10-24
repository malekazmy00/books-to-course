"""Small, deterministic PDF text extraction for Phase 1 and local source inspection.

This is a project-owned fallback for text PDFs. It does not perform OCR; scanned
pages with no text layer are reported so the caller can route them to Docling/OCR.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def extract_pdf_pages(
    source: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[tuple[int, str]]:
    """Return extracted page text with 1-based physical PDF page numbers."""
    path = Path(source)
    if start_page < 1 or (end_page is not None and end_page < start_page):
        raise ValueError("page range must be 1-based and end_page >= start_page")

    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        if not reader.decrypt(""):
            raise ValueError(f"encrypted PDF is not supported: {path}")

    stop = min(end_page or len(reader.pages), len(reader.pages))
    if start_page > len(reader.pages):
        raise ValueError(f"start_page {start_page} exceeds PDF page count {len(reader.pages)}")

    result: list[tuple[int, str]] = []
    for page_no in range(start_page, stop + 1):
        text = reader.pages[page_no - 1].extract_text() or ""
        result.append((page_no, text.strip()))
    return result


def extract_pdf_to_text(
    source: str | Path,
    destination: str | Path,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> tuple[int, int]:
    """Write page-marked UTF-8 text; return (page_count, word_count)."""
    pages = extract_pdf_pages(source, start_page=start_page, end_page=end_page)
    if not any(text for _, text in pages):
        raise ValueError("PDF pages contain no extractable text; OCR/Docling is required")

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(f"[PAGE {page_no}]\n{text}" for page_no, text in pages) + "\n"
    target.write_text(content, encoding="utf-8")
    return len(pages), len(content.split())


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract text from a text-based PDF using the B2C ingestion package")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--start-page", type=int, default=1, help="1-based physical page, inclusive")
    parser.add_argument("--end-page", type=int, help="1-based physical page, inclusive")
    args = parser.parse_args()
    pages, words = extract_pdf_to_text(args.source, args.destination, start_page=args.start_page, end_page=args.end_page)
    print(f"extracted_pages={pages} words={words} output={args.destination}")


if __name__ == "__main__":
    main()
