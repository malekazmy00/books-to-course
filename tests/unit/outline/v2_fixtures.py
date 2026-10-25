"""Tiny ParsedBook v2 builder for outline v2 tests.

``row(kind, text, page, ...)`` describes one block in reading order; ``bm=(level, title)`` marks a
heading that a PDF bookmark points to (it becomes an ``OutlineEntry`` with that block id).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import b2c_contracts as c

B = "bk"
WORDS = "The analyst weighs each piece of critical evidence against every hypothesis in turn. "


def prose(words: int) -> str:
    base = WORDS.split()
    return " ".join(base[i % len(base)] for i in range(words))


@dataclass
class Row:
    kind: str
    text: str
    page: int
    zone: str = "body"
    level: int | None = None
    level_source: str | None = None
    bm: tuple[int, str] | None = None
    label: str | None = None
    extra: dict = field(default_factory=dict)


def head(text, page, level=2, zone="body", bm=None, level_source=None):
    return Row("heading", text, page, zone, level, level_source or ("pdf_outline" if bm else "layout"), bm)


def para(page, words=120, zone="body", kind="paragraph", text=None):
    return Row(kind, text if text is not None else prose(words), page, zone)


def furniture(page, text=None):
    return Row("page_number", text or str(page), page, "furniture")


def rows_of(parsed: c.ParsedBookV2) -> list[Row]:
    """The rows of an existing fixture book (bookmarks kept), to rebuild it with other ids."""
    marks = {e.block_id: (e.level, e.title) for e in parsed.outline if e.block_id}
    return [Row(b.type, b.text, b.refs[0].page_no, b.zone, b.level, b.level_source, marks.get(b.block_id))
            for b in parsed.blocks]


def book(rows: list[Row], *, outline_unmatched: list[tuple[int, str, int]] = (), book_id: str = B,
         labels: dict[int, str] | None = None, offset: int = 0) -> c.ParsedBookV2:
    blocks, outline = [], []
    for i, r in enumerate(rows):
        seq = i + offset
        bid = f"{book_id}:b{seq:05d}"
        blocks.append(c.BlockV2(block_id=bid, type=r.kind, zone=r.zone, level=r.level if r.kind == "heading" else None,
                                level_source=r.level_source if r.kind == "heading" else None, text=r.text,
                                raw_text=r.text, refs=[c.PageRef(book_id=book_id, page_no=r.page)],
                                section_path=[], ocr=False))
        if r.bm:
            outline.append(c.OutlineEntry(title=r.bm[1], level=r.bm[0], page_no=r.page, block_id=bid))
    for level, title, page in outline_unmatched:
        outline.append(c.OutlineEntry(title=title, level=level, page_no=page, block_id=None))
    n = max(r.page for r in rows)
    labels = labels or {}
    return c.ParsedBookV2(book_id=book_id, title="T", role="spine", page_count=n,
                          toc_source="pdf_outline" if outline else "headings",
                          pages=[c.PageInfo(page_no=p, printed_label=labels.get(p), status="content")
                                 for p in range(1, n + 1)],
                          outline=outline, blocks=blocks, parser={"name": "fixture"},
                          normalizer={"name": "b2c-normalize", "version": "1"})


def sat_like() -> c.ParsedBookV2:
    """Front matter, two bookmarked chapters with bookmarked sections, a body line misread as a
    heading (P4), a section bookmark in the middle of a page (P6), chapter-end NOTES and figure
    descriptions, and no back matter."""
    rows = [
        head("STRUCTURED ANALYTIC TECHNIQUES", 1, zone="front_matter"),
        para(1, 20, zone="front_matter"),
        head("CONTENTS", 2, zone="front_matter"),
        para(2, kind="toc_entry", zone="front_matter", text="Chapter 1 • Getting Organized 3"),
        head("Preface", 3, level=1, zone="front_matter", bm=(1, "Preface")),
        para(3, 300, zone="front_matter"),
        head("Chapter 1 • Getting Organized", 4, level=1, bm=(1, "Chapter 1 • Getting Organized")),  # 6
        para(4, 5, kind="toc_entry", text="5.1 Sorting [ 69 ]"),
        para(4, 700),
        head("5.1 Sorting", 5, level=2, bm=(2, "5.1 Sorting")),  # 9
        para(5, 900),
        head("5.2 SORTING", 6, level=3),  # a body line mistaken for a heading: never a lesson
        para(6, 900),
        para(7, 300),
        head("5.2 Ranking, Scoring, and Prioritizing", 7, level=2, bm=(2, "5.2 Ranking, Scoring, and Prioritizing")),
        para(7, 1200),  # 15
        head("When to Use It", 8, level=3),
        para(8, 1000),
        furniture(8),
        head("NOTES", 9, level=3),  # 19
        para(9, 40, kind="endnote"),
        para(9, 40, kind="endnote"),
        head("Descriptions of Images and Figures", 9, level=3),
        para(9, 30, kind="caption"),
        head("Chapter 2 • Exploration Techniques", 10, level=1, bm=(1, "Chapter 2 • Exploration Techniques")),  # 24
        para(10, 0, text=""),
        head("6.1 Simple Brainstorming", 10, level=2, bm=(2, "6.1 Simple Brainstorming")),
        para(10, 1500),
        head("6.2 Cluster Brainstorming", 11, level=2, bm=(2, "6.2 Cluster Brainstorming")),
        para(11, 1500),
    ]
    return book(rows)


def gramsci_like() -> c.ParsedBookV2:
    """Bookmarks only at chapter level (front/back included), sections from layout headings in
    tracked lowercase small caps, a generic 'conclusion' section, endnotes/bibliography/index in
    the back matter."""
    rows = [
        head("FROM SUBORDINATION TO REVOLUTION", 1, zone="front_matter"),
        para(1, 10, zone="front_matter"),
        head("Copyright", 2, level=1, zone="front_matter", bm=(1, "Copyright")),
        para(2, 30, zone="front_matter", text="© 2025 by John Chalcraft All rights reserved. Library of Congress"),
        para(3, kind="toc_entry", zone="front_matter", text="Introduction 1"),
        para(3, kind="toc_entry", zone="front_matter", text="The Originality of the Living Tradition 5"),
        para(3, kind="toc_entry", zone="front_matter", text="Notes 9 Bibliography 10 Index 11"),
        head("Contents", 3, level=1, zone="front_matter", bm=(1, "Contents")),
        head("Introduction", 4, level=1, bm=(1, "Introduction")),  # 8
        para(4, 700),
        head("gramsci: lodestar of renewal", 4, level=2),
        para(4, 900),
        head("critic al approaches", 5, level=2),
        para(5, 900),
        head("1. The Originality of the Living Tradition", 6, level=1,
             bm=(1, "1. The Originality of the Living Tradition")),  # 14
        para(6, 200),
        head("readings of gramsci", 6, level=2),
        para(6, 1200),
        head("The Philosophy of Praxis", 7, level=2),
        para(7, 150),
        head("conclusion", 8, level=2),
        para(8, 800),
        furniture(8),
        head("Notes", 9, level=1, zone="back_matter", bm=(1, "Notes")),  # 23
        para(9, 40, zone="back_matter", kind="endnote"),
        head("Bibliography", 10, level=1, zone="back_matter", bm=(1, "Bibliography")),
        para(10, 20, zone="back_matter", kind="bibliography_entry"),
        head("Index", 11, level=1, zone="back_matter", bm=(1, "Index")),
        para(11, 10, zone="back_matter", kind="index_entry", text="leadership (direzione), 65-69, 134, 306-7"),
    ]
    return book(rows, labels={4: "1", 5: "2", 6: "5", 7: "6", 8: "7", 9: "9", 10: "10", 11: "11"})


def gramsci_like_with_acknowledgments() -> c.ParsedBookV2:
    """Gramsci's front matter: a bookmarked 'Acknowledgments' entry (one long thank-you paragraph)
    between the contents and the Introduction."""
    rows = rows_of(gramsci_like())
    ack = [head("Acknowledgments", 3, level=1, zone="front_matter", bm=(1, "Acknowledgments")),
           para(3, 300, zone="front_matter", text="This book owes much to the insight, support, and care of "
                "colleagues, friends, the press, and my partner. " + prose(280))]
    return book(rows[:8] + ack + rows[8:], labels={4: "1", 5: "2", 6: "5", 7: "6", 8: "7", 9: "9", 10: "10", 11: "11"})
