"""Structure evidence read from a ParsedBook v2: PDF bookmarks, the printed table of contents, and
reference outlines (golden files) resolved to block positions.

A reference entry is ``(title, level, page, zone)``; resolving it finds the heading block it
names. Bookmarks already carry the block they were reconciled with (S2); a golden file only has
the page, so the heading is looked up on that page by normalized title.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from b2c_contracts import ParsedBookV2
from b2c_core.source_text import heading_key

FURNITURE = frozenset({"running_header", "running_footer", "page_number"})
_TOC_ITEM = re.compile(r"(?P<title>.+?)\s+(?P<page>\d{1,4}|[ivxlcdm]{1,7})(?=\s+\S|\s*$)", re.I)
_LEADERS = re.compile(r"(?:\s*\.){3,}\s*")
_NUMBERING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+\S")


@dataclass(frozen=True)
class RefEntry:
    title: str
    level: int
    page: int | None
    zone: str = "body"  # body | front_matter | back_matter (golden labels)
    index: int | None = None  # resolved block position


def title_key(text: str) -> str:
    return heading_key(re.sub(r"\s+", " ", text))


def keys_match(a: str, b: str) -> bool:
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    short, long_ = sorted((ka, kb), key=len)
    return len(short) >= 6 and len(short) >= 0.6 * len(long_) and (long_.startswith(short) or long_.endswith(short))


def find_heading(book: ParsedBookV2, title: str, page: int | None, *, after: int = -1,
                 window: int = 0) -> int | None:
    """Block position of the heading (or short title-like block) named ``title`` near ``page``."""
    best: tuple[int, int] | None = None
    for i, b in enumerate(book.blocks):
        if i <= after or b.type in FURNITURE or b.type in ("table", "figure"):
            continue
        if page is not None and not any(abs(r.page_no - page) <= window for r in b.refs):
            continue
        short = b.type == "heading" or (len(b.text) <= 150 and not b.text.rstrip().endswith("."))
        if not short or not keys_match(b.text, title):
            continue
        rank = (0 if b.type == "heading" else 1, i)
        if best is None or rank < best:
            best = rank
    return None if best is None else best[1]


def bookmark_entries(book: ParsedBookV2) -> list[RefEntry]:
    """PDF bookmarks with the block each points to (S2 reconciliation, else a lookup on its page)."""
    index = {b.block_id: i for i, b in enumerate(book.blocks)}
    out = []
    for e in book.outline:
        i = index.get(e.block_id) if e.block_id else find_heading(book, e.title, e.page_no)
        zone = book.blocks[i].zone if i is not None else "body"
        out.append(RefEntry(e.title, e.level, e.page_no, zone, i))
    return out


def _label_pages(book: ParsedBookV2) -> dict[str, int]:
    pages: dict[str, int] = {}
    for p in book.pages:
        if p.printed_label and p.printed_label.lower() not in pages:
            pages[p.printed_label.lower()] = p.page_no
    return pages


def printed_toc_entries(book: ParsedBookV2) -> list[RefEntry]:
    """Entries of the printed table of contents (front matter TOC blocks), resolved to headings.

    Lines look like 'Title 15 Next Title 48' (several entries per block) or table rows; a printed
    page number is mapped to the physical page through /PageLabels when the book has them.
    """
    labels = _label_pages(book)
    raw: list[tuple[str, str | None, int]] = []
    in_toc = False
    for i, b in enumerate(book.blocks):
        if b.zone != "front_matter":
            continue
        if b.type == "heading":
            in_toc = title_key(b.text) in ("contents", "tableofcontents")
            continue
        if b.type == "toc_entry" or (in_toc and b.type == "table"):
            cells = [c.strip() for line in b.text.split("\n") for c in line.split("|")] if b.type == "table" \
                else [b.text]
            for cell in cells:
                cell = _LEADERS.sub(" ", cell).strip()
                if not cell or set(cell) <= set("-: "):
                    continue
                items = list(_TOC_ITEM.finditer(cell))
                if items and items[-1].end() >= len(cell.rstrip()) - 1:
                    raw.extend((m.group("title").strip(), m.group("page"), i) for m in items)
                else:
                    raw.append((cell, None, i))
    out: list[RefEntry] = []
    after = max((i for *_, i in raw), default=-1)
    for title, label, _ in raw:
        title = re.sub(r"\s+", " ", title).strip(" .")
        if len(title_key(title)) < 3:
            continue
        page = labels.get(label.lower()) if label else None
        m = _NUMBERING.match(title)
        level = m.group(1).count(".") + 1 if m else 1
        found = find_heading(book, title, page, after=after, window=1) if page is not None else None
        if found is None:
            found = find_heading(book, title, None, after=after)
        zone = book.blocks[found].zone if found is not None else "body"
        out.append(RefEntry(title, level, page, zone, found))
    return out


def load_reference(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def resolve_reference(book: ParsedBookV2, entries: list[dict[str, Any]]) -> list[RefEntry]:
    """Golden entries ({title, level, page, zone}) resolved to heading blocks of this parse."""
    out = []
    for e in entries:
        page = int(e["page"])
        i = find_heading(book, e["title"], page)
        if i is None:
            i = find_heading(book, e["title"], page, window=1)
        out.append(RefEntry(e["title"], int(e["level"]), page, e.get("zone", "body"), i))
    return out


__all__ = ["RefEntry", "bookmark_entries", "find_heading", "keys_match", "load_reference", "printed_toc_entries",
           "resolve_reference", "title_key"]
