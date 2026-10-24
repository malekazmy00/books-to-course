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


# Fallback heading detection (no PDF bookmarks). Deliberately conservative:
# prose such as "2 of the 10 respondents said yes", footnotes such as
# "12 See Smith, p. 4." and sentences opening with "Chapter 3 shows ..." must
# stay paragraph text.
HEADING_POLICY_VERSION = "pypdf-inline-headings-v2"
# Legacy prefix pattern, still used by the Surya/OpenRouter OCR backends whose
# output identity must not change silently. It has the same false positives;
# migrate those backends to _is_heading_line together with a parser-version bump.
_HEADING = re.compile(r"^(?:(?i:chapter|part|section)\s+[0-9ivxlcdm]+\b|(?:[0-9]+(?:\.[0-9]+)*[.)]?\s+).{2,}|[A-Z][A-Z0-9 ,:;()&/-]{3,})")
_KEYWORD_HEADING = re.compile(r"(?i:chapter|part|section)\s+(?:[0-9]+|[ivxlcdm]+)\b[.:]?(?:\s+(?P<title>.+))?")
_NUMBERED_HEADING = re.compile(r"(?P<number>[0-9]{1,2}(?:\.[0-9]{1,2})*)(?P<mark>[.)]?)\s+(?P<title>.+)")
_CAPS_HEADING = re.compile(r"[A-Z][A-Z0-9 ,:;()&/-]{3,}")
_SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on",
                          "or", "the", "to", "with", "vs", "via"})
_FOOTNOTE_LEADS = frozenset({"see", "cf", "cf.", "ibid", "ibid.", "idem", "quoted", "cited", "for",
                             "on", "in", "this", "these", "the", "a", "an", "it"})


def _title_like(title: str) -> bool:
    """Short title-case phrase without sentence punctuation."""
    title = title.strip()
    if not title or title[-1] in ".,;" or "," in title or len(title) > 120:
        return False
    words = re.findall(r"[^\W\d_][\w'’-]*", title)
    if not words or len(words) > 12 or not words[0][0].isupper():
        return False
    content = [word for word in words if word.lower() not in _SMALL_WORDS]
    return bool(content) and all(word[0].isupper() for word in content)


def _is_heading_line(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 140:
        return False
    keyword = _KEYWORD_HEADING.fullmatch(line)
    if keyword is not None:
        return keyword.group("title") is None or _title_like(keyword.group("title"))
    numbered = _NUMBERED_HEADING.fullmatch(line)
    if numbered is not None:
        title = numbered.group("title")
        first = title.split()[0].lower()
        dotted = "." in numbered.group("number") or bool(numbered.group("mark"))
        if not dotted and first in _FOOTNOTE_LEADS:
            return False
        return _title_like(title)
    return _CAPS_HEADING.fullmatch(line) is not None and any(char.isalpha() for char in line)


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


def _split_page_at_headings(text: str) -> list[tuple[str, str]]:
    """Split page text into ("text", chunk) / ("heading", line) items in reading order.

    Heading lines are removed from the surrounding paragraph text, and text above
    a mid-page heading stays with the section that precedes it.
    """
    items: list[tuple[str, str]] = []
    buffer: list[str] = []
    for line in text.splitlines():
        if _is_heading_line(line):
            if "\n".join(buffer).strip():
                items.append(("text", "\n".join(buffer)))
            buffer = []
            items.append(("heading", line.strip()))
        else:
            buffer.append(line)
    if "\n".join(buffer).strip():
        items.append(("text", "\n".join(buffer)))
    return items


def _build_blocks(book_id: str, pages: list[tuple[int, str, bool]],
                  bookmarks_by_page: dict[int, list[tuple[int, str]]], *,
                  detect_headings: bool) -> list[Block]:
    blocks: list[Block] = []
    section_path: list[str] = []
    for page_no, text, _ in pages:
        seq = 0
        refs = [PageRef(book_id=book_id, page_no=page_no)]

        def heading(level: int, title: str) -> None:
            nonlocal seq, section_path
            section_path = section_path[: max(level - 1, 0)]
            section_path.append(title)
            blocks.append(Block(
                block_id=f"{book_id}:{page_no}:{seq}", type="heading",
                level=level, text=title, refs=list(refs),
                section_path=section_path.copy(), ocr=False,
            ))
            seq += 1

        def paragraphs(chunk: str) -> None:
            nonlocal seq
            for paragraph in _paragraphs(chunk):
                blocks.append(Block(
                    block_id=f"{book_id}:{page_no}:{seq}", type="paragraph", text=paragraph,
                    refs=list(refs), section_path=section_path.copy(), ocr=False,
                ))
                seq += 1

        # Bookmark positions within a page are unknown: keep them at the top.
        for level, title in bookmarks_by_page.get(page_no, []):
            heading(level, title)
        if not detect_headings:
            paragraphs(text)
            continue
        for kind, value in _split_page_at_headings(text):
            if kind == "heading":
                heading(1, value)
            else:
                paragraphs(value)
    return blocks


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
    # numbered/chapter-style headings, in place. Arbitrary prose is never promoted to TOC.
    blocks = _build_blocks(upload.book_id, pages, by_page, detect_headings=not bookmarks)

    options = {"backend": "pypdf-text", "min_page_chars": min_page_chars, "version": pypdf.__version__}
    if not bookmarks:
        # Only fallback-heading output changed; bookmarked parses keep their identity.
        options["heading_policy"] = HEADING_POLICY_VERSION
    toc_source = "pdf_outline" if bookmarks else "headings"
    return ParsedBook(
        book_id=upload.book_id, title=upload.title, role=upload.role,
        page_count=len(pages), toc_source=toc_source, blocks=blocks,
        parser={"name": "pypdf-text-fallback", "version": pypdf.__version__,
                "options_hash": hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest()},
    )
