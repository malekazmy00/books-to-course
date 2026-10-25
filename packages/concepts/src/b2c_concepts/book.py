"""Read-only view of the source book for the concept layer: blocks in reading order, which lesson
each block belongs to, and term occurrences in the lesson text (front/back matter outside the
outline's lessons never introduces a concept)."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from b2c_contracts import BookSpan, CourseOutline, CourseOutlineV2, ParsedBook, ParsedBookV2

BODY_TYPES_V1 = frozenset({"heading", "paragraph", "list_item", "table", "caption", "footnote"})
BODY_TYPES_V2 = frozenset({"heading", "paragraph", "list_item", "table", "caption", "footnote", "endnote"})
_SENT_END = re.compile(r"[.!?][\"'”’)\]]*\s+")


@dataclass(frozen=True)
class BookBlock:
    idx: int
    block_id: str
    type: str
    text: str
    pages: tuple[int, ...]


@dataclass(frozen=True)
class LessonRef:
    lesson_id: str
    title: str
    start: int
    end: int  # inclusive block index


@dataclass(frozen=True)
class Occurrence:
    idx: int
    block_id: str
    start: int
    end: int


_TOC_LINE = re.compile(r"[^\n]{0,40}?[A-Za-z][^\n]{0,160}?(?:\s|\.)\d{1,3}")


def toc_like(text: str) -> bool:
    """A contents/figures listing: most lines end in a page number (or one short such line)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    hits = sum(bool(_TOC_LINE.fullmatch(ln)) for ln in lines)
    if len(lines) == 1:
        return hits == 1 and len(lines[0].split()) <= 14
    return hits / len(lines) >= 0.5


def term_pattern(term: str, kind: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in term.replace("™", " ").replace("®", " ").split()]
    if not words:
        return re.compile(r"(?!x)x")
    body = r"[\s\-‐‑]+".join(words)
    if kind == "acronym":
        return re.compile(r"(?<![\w])" + body + r"s?(?![\w])")
    return re.compile(r"(?<![\w])" + body + r"(?:™|®)?(?:s|es|'s|’s)?(?![\w])", re.I)


class BookIndex:
    def __init__(self, book_id: str, blocks: Sequence[BookBlock], lessons: Sequence[LessonRef]) -> None:
        self.book_id = book_id
        self.blocks = list(blocks)
        self.lessons = sorted(lessons, key=lambda lesson: lesson.start)
        self.by_id = {b.block_id: b for b in self.blocks}
        self.lesson_of: dict[int, LessonRef] = {}
        for lesson in self.lessons:
            for i in range(lesson.start, lesson.end + 1):
                self.lesson_of.setdefault(i, lesson)

    # ------------------------------------------------------------------ constructors
    @classmethod
    def _build(cls, book_id: str, blocks: list[BookBlock], spans: list[tuple[str, str, str, str]],
               body_types: frozenset[str]) -> "BookIndex":
        pos = {b.block_id: b.idx for b in blocks}
        lessons = [LessonRef(lid, title, pos[s], pos[e]) for lid, title, s, e in spans if s in pos and e in pos]
        kept = [b for b in blocks if b.type in body_types]
        return cls(book_id, kept, lessons)

    @classmethod
    def from_parsed_v1(cls, book: ParsedBook, outline: CourseOutline) -> "BookIndex":
        blocks = [BookBlock(i, b.block_id, b.type, b.text, tuple(sorted({r.page_no for r in b.refs})))
                  for i, b in enumerate(book.blocks)]
        spans = [(lesson.lesson_id, " ".join(lesson.title.split()), lesson.spine_span.start_block,
                  lesson.spine_span.end_block) for m in outline.modules for lesson in m.lessons]
        return cls._build(book.book_id, blocks, spans, BODY_TYPES_V1)

    @classmethod
    def from_parsed_v2(cls, book: ParsedBookV2, outline: CourseOutlineV2) -> "BookIndex":
        blocks = [BookBlock(i, b.block_id, b.type, b.text, tuple(sorted({r.page_no for r in b.refs})))
                  for i, b in enumerate(book.blocks)]
        spans = [(lesson.lesson_id, lesson.display_title, lesson.spine_span.start_block, lesson.spine_span.end_block)
                 for m in outline.modules for lesson in m.lessons]
        return cls._build(book.book_id, blocks, spans, BODY_TYPES_V2)

    # ------------------------------------------------------------------ queries
    def lesson_blocks(self) -> list[BookBlock]:
        """Teaching text: blocks inside lessons, without table-of-contents-like listings."""
        return [b for b in self.blocks if b.idx in self.lesson_of and not toc_like(b.text)]

    def occurrences(self, term: str, kind: str) -> list[Occurrence]:
        pattern = term_pattern(term, kind)
        out = []
        for b in self.lesson_blocks():
            for m in pattern.finditer(b.text):
                out.append(Occurrence(b.idx, b.block_id, m.start(), m.end()))
        return out

    def sentence_span(self, occ: Occurrence, *, max_chars: int = 600) -> BookSpan:
        """The sentence around an occurrence, sliced verbatim from the block text."""
        text = self.by_id[occ.block_id].text
        start = 0
        for m in _SENT_END.finditer(text, 0, occ.start):
            start = m.end()
        nxt = _SENT_END.search(text, occ.end)
        end = nxt.start() + 1 if nxt else len(text)
        if end - start > max_chars:
            start = max(start, occ.start - max_chars // 2)
            end = min(end, start + max_chars)
            start = text.find(" ", start, occ.start) + 1 if text.find(" ", start, occ.start) >= 0 else start
        quote = text[start:end].strip()
        return BookSpan(block_id=occ.block_id, pages=list(self.by_id[occ.block_id].pages), quote=quote)

    def window(self, block_id: str, around: int, size: int = 1500) -> str:
        text = self.by_id[block_id].text
        if len(text) <= size:
            return text
        start = max(0, min(around - size // 3, len(text) - size))
        return text[start:start + size]


__all__ = ["BODY_TYPES_V1", "BODY_TYPES_V2", "BookBlock", "BookIndex", "LessonRef", "Occurrence", "term_pattern", "toc_like"]
