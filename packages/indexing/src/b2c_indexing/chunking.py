"""Legacy (v1) chunker, kept to measure the before state; production indexing uses index_build.

It ignores block types, makes heading-only chunks and overlaps whole blocks (audit 03 C2/M1).
"""
from __future__ import annotations

import hashlib
import re

from b2c_contracts.models import Block, CourseOutline, ParsedBook, StructuredChunk
from b2c_core.arabic_text import normalize_arabic

_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Tokenizer-independent estimate, pending in-domain evaluation.

    Arabic diacritics/tatweel are removed first so they do not count as tokens.
    """
    return max(1, len(_TOKEN_RE.findall(normalize_arabic(text))))


def _chunk_id(book_id: str, block_ids: list[str]) -> str:
    digest = hashlib.sha256((book_id + ":" + ":".join(block_ids)).encode()).hexdigest()[:20]
    return f"{book_id}:chunk:{digest}"


def _assemble(book: ParsedBook, blocks: list[Block], embedding_model: str,
              section_path: list[str]) -> StructuredChunk:
    ids = [b.block_id for b in blocks]
    pages = sorted({r.page_no for b in blocks for r in b.refs})
    text = "\n\n".join(b.text for b in blocks if b.text.strip())
    return StructuredChunk(chunk_id=_chunk_id(book.book_id, ids), book_id=book.book_id,
        role=book.role, section_path=section_path, block_ids=ids, pages=pages,
        text=text, token_count=estimate_tokens(text), embedding_model=embedding_model)


def _section_assignments(book: ParsedBook, outline: CourseOutline | None):
    """Assign accepted lesson spans as hard chunk boundaries for the spine book."""
    if outline is None:
        return [(tuple(block.section_path), list(block.section_path)) for block in book.blocks]
    if outline.spine_book_id != book.book_id:
        raise ValueError("course outline spine_book_id does not match the ParsedBook")
    positions = {block.block_id: i for i, block in enumerate(book.blocks)}
    assigned: list[tuple[str, ...] | None] = [None] * len(book.blocks)
    display: dict[tuple[str, ...], list[str]] = {}
    previous_end = -1
    for module in outline.modules:
        for lesson in module.lessons:
            span = lesson.spine_span
            if span.start_block not in positions or span.end_block not in positions:
                raise ValueError(f"outline span for {lesson.lesson_id} references an unknown block")
            start, end = positions[span.start_block], positions[span.end_block]
            if end < start or start <= previous_end:
                raise ValueError("outline lesson spans are reversed, overlapping, or out of order")
            key = ("outline", module.module_id, lesson.lesson_id)
            display[key] = [module.title, lesson.title]
            for index in range(start, end + 1):
                assigned[index] = key
            previous_end = end
    result = []
    for block, key in zip(book.blocks, assigned):
        if key is None:
            path = list(block.section_path)
            result.append((("unassigned", *path), path))
        else:
            result.append((key, display[key]))
    return result


def chunk_book(book: ParsedBook, *, target_tokens: int = 500, overlap_ratio: float = 0.12,
               embedding_model: str = "unassigned",
               outline: CourseOutline | None = None) -> list[StructuredChunk]:
    """Never cross accepted outline/section boundaries; keep blocks and tables atomic.

    When supplied, an accepted course outline is the section source for its spine
    book. This consumes the upstream Phase 2 artifact; it does not infer structure.
    Supplementary books can be chunked from their own ParsedBook section paths.
    """
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    if not 0 <= overlap_ratio < 0.5:
        raise ValueError("overlap_ratio must be in [0, 0.5)")
    assignments = _section_assignments(book, outline)
    sections: list[tuple[tuple[str, ...], list[str], list[Block]]] = []
    for block, (key, section_path) in zip(book.blocks, assignments):
        if not sections or sections[-1][0] != key:
            sections.append((key, section_path, []))
        sections[-1][2].append(block)
    result: list[StructuredChunk] = []
    overlap_limit = int(target_tokens * overlap_ratio)
    for _, section_path, section in sections:
        groups: list[list[Block]] = []
        pending: list[Block] = []
        size = 0
        for block in section:
            n = estimate_tokens(block.text)
            if pending and size + n > target_tokens:
                groups.append(pending)
                carry: list[Block] = []
                carry_n = 0
                for old in reversed(pending):
                    old_n = estimate_tokens(old.text)
                    if carry_n + old_n > overlap_limit:
                        break
                    carry.insert(0, old)
                    carry_n += old_n
                if len(carry) == len(pending):
                    # Carrying every block would make the emitted chunk a strict
                    # subset of the next one (a duplicated chunk), not an overlap.
                    carry, carry_n = [], 0
                pending, size = carry, carry_n
            pending.append(block)
            size += n
        if pending:
            groups.append(pending)
        result.extend(_assemble(book, group, embedding_model, section_path)
                      for group in groups if group)
    return result
