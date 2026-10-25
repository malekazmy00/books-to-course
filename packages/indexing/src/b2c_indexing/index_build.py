"""Index build v2 (R5): content gate -> span-based, structure-aware chunks -> gate report.

Design (docs/audits/2026-10-24/03-indexing-retrieval.md §5, plan §R5):

* **Coverage by construction.** Lesson content is *assembled* from the accepted outline's
  lesson spans, never retrieved. Every character of every teaching block of a span lands in
  exactly one chunk (``ChunkPart`` ranges); ``check_lesson_parity`` proves it and a failure
  fails the gate (no log line).
* **Only body is teaching content.** Furniture, front/back matter, TOC, index, bibliography
  and figure OCR text are excluded; notes become chunks of their own evidence class, attached
  to the lesson of the block that cites them; corrupted blocks are quarantined and listed.
* **No heading-only chunk.** Headings attach to the content that follows them (or, at the end
  of a span, to the content before them). A span with headings only has no content: the gate
  reports it (``lessons_without_body``) instead of indexing a title as a lesson.
* **Truncation = 0.** A prose block longer than ``max_tokens`` is split on sentence
  boundaries (whitespace as a last resort); the parts tile the block exactly. Tables,
  equations and code stay atomic (T-IDX-01) and are bounded by ``hard_limit_tokens``.
* **No overlap.** Lesson parity requires exactly-once coverage; retrieval context comes
  from ``context_header`` (book > module > lesson), not from duplicated text.

Token counts use ``estimate_tokens`` (model-tokenizer counting is still open, audit 03 M2).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from b2c_contracts import ChunkPart, IndexQualityReport, ParsedBookV2, QualityCheck, StructuredChunk
from b2c_contracts.models import CourseOutline, ParsedBook

from .chunking import estimate_tokens
from .content_gate import CORRUPT_FLAGS, GATE_VERSION, GateBlock, gate_blocks

CHUNKER_VERSION = "b2c-chunker/2"
REPORT_VERSION = "b2c-index-gate/1"
EXCLUSION_REASONS = ("furniture", "front_matter", "back_matter", "toc", "index", "bibliography",
                     "figure_text", "empty", "outline_excluded")
_ATOMIC_TYPES = frozenset({"table", "equation", "code"})
_HEADING_RESERVE = 32  # tokens kept free for headings that attach to a split piece
_SENTENCE_GAP = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+(?=\S)")
_SPACE_GAP = re.compile(r"\s+(?=\S)")


@dataclass(frozen=True)
class _Piece:
    block: GateBlock
    out_id: str
    start: int
    end: int
    tokens: int
    hard_split: bool = False

    @property
    def heading(self) -> bool:
        return self.block.heading


@dataclass
class IndexBuild:
    chunks: list[StructuredChunk]
    report: IndexQualityReport
    quarantined: list[dict] = field(default_factory=list)
    lesson_blocks: dict[str, dict[str, int]] = field(default_factory=dict)


# ----------------------------------------------------------------------------- parity
def check_lesson_parity(expected: Mapping[str, Mapping[str, int]], chunks: Sequence[StructuredChunk],
                        key_of: Callable[[StructuredChunk], str | None] = lambda c: c.lesson_id
                        ) -> dict[str, int]:
    """Every expected block (key -> {block_id: text length}) must be tiled exactly once by the
    body chunks of its key: orphan = no part at all, duplicated = a character in two parts,
    truncated = a gap or a missing tail."""
    ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for chunk in chunks:
        if chunk.evidence_class != "body":
            continue
        key = key_of(chunk)
        for part in chunk.parts:
            ranges.setdefault((key, part.block_id), []).append((part.start, part.end))
    orphan = duplicated = truncated = 0
    for key, blocks in expected.items():
        for block_id, length in blocks.items():
            spans = sorted(ranges.get((key, block_id), []))
            if not spans:
                orphan += 1
                continue
            pos, dup, gap = 0, False, False
            for start, end in spans:
                if start < pos:
                    dup = True
                elif start > pos:
                    gap = True
                pos = max(pos, end)
            duplicated += dup
            truncated += gap or pos != length
    return {"orphan_body_blocks": orphan, "duplicated_body_blocks": duplicated, "truncated_blocks": truncated}


# ----------------------------------------------------------------------------- splitting
def _split(block: GateBlock, out_id: str, target: int, limit: int) -> list[_Piece]:
    text = block.text
    tokens = estimate_tokens(text)
    if tokens <= limit or block.type in _ATOMIC_TYPES:
        return [_Piece(block, out_id, 0, len(text), tokens)]
    starts = [0, *(m.end() for m in _SENTENCE_GAP.finditer(text))]
    sentences = [(a, z) for a, z in zip(starts, [*starts[1:], len(text)]) if a < z]
    pieces: list[_Piece] = []
    cur_start, cur_tokens = None, 0

    def flush(end: int) -> None:
        nonlocal cur_start, cur_tokens
        if cur_start is not None and end > cur_start:
            pieces.append(_Piece(block, out_id, cur_start, end, estimate_tokens(text[cur_start:end])))
        cur_start, cur_tokens = None, 0

    for a, z in sentences:
        n = estimate_tokens(text[a:z])
        if n > limit:  # one sentence longer than the limit: cut at whitespace
            flush(a)
            words = [a, *(m.end() for m in _SPACE_GAP.finditer(text, a, z))]
            w_start, w_tokens = a, 0
            for wa, wz in zip(words, [*words[1:], z]):
                wn = estimate_tokens(text[wa:wz])
                if w_tokens and w_tokens + wn > target:
                    pieces.append(_Piece(block, out_id, w_start, wa, w_tokens, hard_split=True))
                    w_start, w_tokens = wa, 0
                w_tokens += wn
            pieces.append(_Piece(block, out_id, w_start, z, estimate_tokens(text[w_start:z]), hard_split=True))
            continue
        if cur_start is not None and cur_tokens + n > target:
            flush(a)
        if cur_start is None:
            cur_start = a
        cur_tokens += n
    flush(len(text))
    return pieces


def _group(pieces: list[_Piece], target: int, max_tokens: int, min_tokens: int) -> list[list[_Piece]]:
    groups: list[list[_Piece]] = []
    cur: list[_Piece] = []
    size = 0
    for piece in pieces:
        has_content = any(not p.heading for p in cur)
        if has_content:
            close = (piece.heading and size >= min_tokens) or (
                size + piece.tokens > target and (size >= min_tokens or size + piece.tokens > max_tokens))
            if close:
                groups.append(cur)
                cur, size = [], 0
        cur.append(piece)
        size += piece.tokens
    if cur:
        if any(not p.heading for p in cur):
            groups.append(cur)
        elif groups:
            groups[-1].extend(cur)  # trailing headings attach to the content before them
        else:
            return []  # headings only: no teaching content here
    if len(groups) >= 2:
        tail = sum(p.tokens for p in groups[-1])
        if tail < min_tokens and tail + sum(p.tokens for p in groups[-2]) <= max_tokens:
            groups[-2].extend(groups.pop())
    return groups


# ----------------------------------------------------------------------------- assembly
def _parts(group: list[_Piece]) -> list[ChunkPart]:
    merged: list[list] = []
    for p in group:
        if merged and merged[-1][0] == p.out_id and merged[-1][2] == p.start:
            merged[-1][2] = p.end
        else:
            merged.append([p.out_id, p.start, p.end])
    return [ChunkPart(block_id=b, start=s, end=e) for b, s, e in merged]


def _chunk(book_id: str, title: str, role: str, evidence: str, lesson_id: str | None,
           section_path: list[str], group: list[_Piece], embedding_model: str,
           extra_flags: tuple[str, ...] = ()) -> StructuredChunk:
    parts = _parts(group)
    by_id = {p.out_id: p.block for p in group}
    text = "\n\n".join(by_id[part.block_id].text[part.start:part.end].strip() for part in parts)
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = json.dumps([book_id, evidence, [[p.block_id, p.start, p.end] for p in parts], text_sha,
                           CHUNKER_VERSION], separators=(",", ":"))
    flags = sorted({f for p in group for f in p.block.flags if f not in CORRUPT_FLAGS}
                   | ({"hard_split"} if any(p.hard_split for p in group) else set()) | set(extra_flags))
    return StructuredChunk(
        chunk_id=f"{book_id}:chunk:{hashlib.sha256(identity.encode()).hexdigest()[:20]}",
        book_id=book_id, role=role, section_path=list(section_path),
        block_ids=[part.block_id for part in parts],
        pages=sorted({page for p in group for page in p.block.pages}), text=text,
        token_count=estimate_tokens(text), embedding_model=embedding_model, evidence_class=evidence,
        lesson_id=lesson_id, parts=parts, text_sha256=text_sha, chunker_version=CHUNKER_VERSION,
        context_header=" › ".join([title, *section_path]), quality_flags=flags)


def _resolve_ids(gated: list[GateBlock], outline: CourseOutline | None) -> list[str]:
    native = [g.block_id for g in gated]
    if outline is None or not gated or gated[0].alt_id is None:
        return native
    wanted = {e for m in outline.modules for lesson in m.lessons
              for e in (lesson.spine_span.start_block, lesson.spine_span.end_block)}
    if wanted <= set(native):
        return native
    alt = [g.alt_id or g.block_id for g in gated]
    if wanted <= set(alt):
        return alt  # outline drawn on the v1 projection of this v2 book
    return native


def _assign(out_ids: list[str], outline: CourseOutline | None, book_id: str):
    """Per block: (lesson_id, display path) or None; plus lesson order and outline exclusions."""
    lesson_of: list[tuple[str, list[str]] | None] = [None] * len(out_ids)
    if outline is None:
        return lesson_of, [], set()
    if outline.spine_book_id != book_id:
        raise ValueError("course outline spine_book_id does not match the ParsedBook")
    pos = {b: i for i, b in enumerate(out_ids)}
    lessons: list[str] = []
    previous_end = -1
    for module in outline.modules:
        for lesson in module.lessons:
            span = lesson.spine_span
            if span.start_block not in pos or span.end_block not in pos:
                raise ValueError(f"outline span for {lesson.lesson_id} references an unknown block")
            start, end = pos[span.start_block], pos[span.end_block]
            if end < start or start <= previous_end:
                raise ValueError("outline lesson spans are reversed, overlapping, or out of order")
            for i in range(start, end + 1):
                lesson_of[i] = (lesson.lesson_id, [module.title, lesson.title])
            lessons.append(lesson.lesson_id)
            previous_end = end
    return lesson_of, lessons, set(outline.excluded_prefix_block_ids)


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def index_book(book: ParsedBook | ParsedBookV2, *, outline: CourseOutline | None = None,
               target_tokens: int = 400, max_tokens: int = 800, min_tokens: int = 64,
               hard_limit_tokens: int = 2048, embedding_model: str = "unassigned",
               max_quarantine_ratio: float = 0.02, outline_sha256: str | None = None) -> IndexBuild:
    if not 0 < min_tokens <= target_tokens <= max_tokens <= hard_limit_tokens:
        raise ValueError("need 0 < min_tokens <= target_tokens <= max_tokens <= hard_limit_tokens")
    gated = gate_blocks(book)
    out_ids = _resolve_ids(gated, outline)
    lesson_of, lesson_order, outline_excluded = _assign(out_ids, outline, book.book_id)
    index_of_native = {g.block_id: i for i, g in enumerate(gated)}
    reasons = Counter(g.reason for g in gated if g.evidence == "excluded")
    for i, g in enumerate(gated):
        if g.evidence != "excluded" and out_ids[i] in outline_excluded:
            reasons["outline_excluded"] += 1

    def usable(i: int) -> bool:
        return gated[i].evidence != "excluded" and out_ids[i] not in outline_excluded

    # ---- sections of teaching blocks: a lesson span, or a run of one section path
    sections: list[tuple[str, str | None, list[str], list[int]]] = []  # key, lesson, path, indexes
    for i, g in enumerate(gated):
        if not (usable(i) and g.teaching):
            continue
        if lesson_of[i] is not None:
            key, lesson_id, path = "lesson:" + lesson_of[i][0], lesson_of[i][0], lesson_of[i][1]
        else:
            key, lesson_id, path = "section:" + json.dumps(g.section_path), None, list(g.section_path)
        if sections and sections[-1][0] == key:
            sections[-1][3].append(i)
        else:
            sections.append((key, lesson_id, path, [i]))
    # a heading-only run outside lessons attaches forward to the next section outside lessons
    merged_sections: list[tuple[str, str | None, list[str], list[int]]] = []
    carry: list[int] = []
    for n, (key, lesson_id, path, idx) in enumerate(sections):
        idx = carry + idx if lesson_id is None else idx
        carry = []
        heading_only = all(gated[i].heading for i in idx)
        nxt = sections[n + 1] if n + 1 < len(sections) else None
        if lesson_id is None and heading_only and nxt is not None and nxt[1] is None:
            carry = idx
            continue
        merged_sections.append((key, lesson_id, path, idx))

    chunks: list[StructuredChunk] = []
    expected: dict[str, dict[str, int]] = {}
    lessons_with_body: set[str] = set()
    split_blocks = 0
    key_of_chunk: dict[str, str] = {}
    for key, lesson_id, path, idx in merged_sections:
        pieces: list[_Piece] = []
        for i in idx:
            block_pieces = _split(gated[i], out_ids[i], target_tokens, max_tokens - _HEADING_RESERVE)
            split_blocks += len(block_pieces) > 1
            pieces.extend(block_pieces)
        groups = _group(pieces, target_tokens, max_tokens, min_tokens)
        if not groups:
            continue  # headings only (counted as structural below)
        expected[key] = {out_ids[i]: len(gated[i].text) for i in idx}
        if lesson_id is not None:
            lessons_with_body.add(lesson_id)
        single = len(groups) == 1
        for group in groups:
            short = sum(p.tokens for p in group) < min_tokens
            flags = ("short_section",) if short and single else (("short_chunk",) if short else ())
            chunk = _chunk(book.book_id, book.title, book.role, "body", lesson_id, path, group,
                           embedding_model, flags)
            chunks.append(chunk)
            key_of_chunk[chunk.chunk_id] = key

    # ---- notes: own evidence class, attached to the lesson of the block that cites them
    note_sections: list[tuple[str, str | None, list[str], list[int]]] = []
    for i, g in enumerate(gated):
        if g.evidence != "note" or g.quarantined or not usable(i):
            continue
        owner = lesson_of[i]
        for citing in g.cited_by:
            j = index_of_native.get(citing)
            if j is not None and lesson_of[j] is not None:
                owner = lesson_of[j]
                break
        if owner is not None:
            key, lesson_id, path = "note:" + owner[0], owner[0], owner[1]
        else:
            key, lesson_id, path = "note:" + json.dumps(g.section_path), None, list(g.section_path)
        if note_sections and note_sections[-1][0] == key:
            note_sections[-1][3].append(i)
        else:
            note_sections.append((key, lesson_id, path, [i]))
    for key, lesson_id, path, idx in note_sections:
        pieces = [p for i in idx for p in _split(gated[i], out_ids[i], target_tokens, max_tokens - _HEADING_RESERVE)]
        for group in _group(pieces, target_tokens, max_tokens, min_tokens):
            chunks.append(_chunk(book.book_id, book.title, book.role, "note", lesson_id, path, group,
                                 embedding_model))

    # ---- gate
    parity = check_lesson_parity(expected, chunks, key_of=lambda c: key_of_chunk.get(c.chunk_id))
    body_chunks = [c for c in chunks if c.evidence_class == "body"]
    teaching_ids = {out_ids[i] for i, g in enumerate(gated) if usable(i) and g.teaching}
    non_body = sum(1 for c in body_chunks if any(b not in teaching_ids for b in c.block_ids))
    heading_ids = {out_ids[i] for i, g in enumerate(gated) if g.heading}
    heading_only = sum(1 for c in chunks if all(b in heading_ids for b in c.block_ids))
    tokens = [c.token_count for c in body_chunks]
    by_out = dict(zip(out_ids, gated))
    over_max = sum(1 for c in body_chunks if c.token_count > max_tokens
                   and not any(by_out[b].type in _ATOMIC_TYPES for b in c.block_ids))
    over_hard = sum(1 for c in chunks if c.token_count > hard_limit_tokens)
    body_candidates = [i for i, g in enumerate(gated) if g.evidence == "body" and usable(i)]
    quarantined = [i for i in body_candidates if gated[i].quarantined]
    quarantined += [i for i, g in enumerate(gated) if g.evidence == "note" and g.quarantined and usable(i)]
    q_ratio = round(sum(1 for i in body_candidates if gated[i].quarantined) / len(body_candidates), 4) \
        if body_candidates else 0.0
    structural = sum(1 for _, _, _, idx in merged_sections for i in idx
                     if not any(not gated[j].heading for j in idx))
    lessons_without_body = (len([lid for lid in lesson_order if lid not in lessons_with_body])
                            if outline is not None else None)
    outside = (sum(1 for i in body_candidates if lesson_of[i] is None and gated[i].teaching)
               if outline is not None else None)
    flag_counts = Counter(f for i in body_candidates for f in gated[i].flags)
    metrics: dict[str, float | int | None] = {
        "blocks_total": len(gated),
        "body_blocks": len(teaching_ids),
        "heading_blocks": sum(1 for i in body_candidates if gated[i].heading and gated[i].teaching),
        "note_blocks": sum(1 for g in gated if g.evidence == "note"),
        "quarantined_blocks": len(quarantined),
        "quarantined_ratio": q_ratio,
        **{f"excluded_{r}": reasons.get(r, 0) for r in EXCLUSION_REASONS},
        **{f"flagged_{f}": n for f, n in sorted(flag_counts.items())},
        "body_chunks": len(body_chunks),
        "note_chunks": len(chunks) - len(body_chunks),
        "tokens_min": min(tokens) if tokens else None,
        "tokens_p5": _percentile(tokens, 0.05),
        "tokens_p50": _percentile(tokens, 0.5),
        "tokens_p95": _percentile(tokens, 0.95),
        "tokens_max": max(tokens) if tokens else None,
        "short_chunks": sum(1 for c in body_chunks if c.token_count < min_tokens),
        "split_blocks": split_blocks,
        "chunks_over_max_tokens": over_max,
        "chunks_over_hard_limit": over_hard,
        "heading_only_chunks": heading_only,
        "non_body_in_content": non_body,
        **parity,
        "structural_headings": structural,
        "lessons": len(lesson_order) if outline is not None else None,
        "lessons_without_body": lessons_without_body,
        "body_blocks_outside_lessons": outside,
    }

    def check(name: str, op: str, threshold: float, detail: str) -> QualityCheck:
        value = metrics[name]
        if value is None:
            return QualityCheck(name=name, value=None, op=op, threshold=threshold, passed=None, detail=detail)
        ok = {"<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}[op]
        return QualityCheck(name=name, value=float(value), op=op, threshold=threshold, passed=ok, detail=detail)

    checks = [
        check("orphan_body_blocks", "==", 0, "T-IDX-04: every body block of a span is in a chunk"),
        check("duplicated_body_blocks", "==", 0, "T-IDX-04: ... in exactly one chunk"),
        check("truncated_blocks", "==", 0, "T-IDX-04: parts tile the block text (truncation = 0)"),
        check("heading_only_chunks", "==", 0, "T-IDX-05: a title is metadata, not a chunk"),
        check("non_body_in_content", "==", 0, "T-IDX-05: no furniture/TOC/index/bibliography/notes in content"),
        check("chunks_over_max_tokens", "==", 0, f"prose chunks <= {max_tokens} estimated tokens"),
        check("chunks_over_hard_limit", "==", 0, f"every chunk (tables too) <= {hard_limit_tokens}"),
        check("quarantined_ratio", "<=", max_quarantine_ratio, "audit 03 §5.6: corrupted body <= 2%"),
        check("lessons_without_body", "==", 0, "every outline lesson has body text"),
        check("body_chunks", ">=", 1, "the book has teaching content"),
    ]
    accepted = all(c.passed is not False for c in checks) and any(c.passed for c in checks)
    report = IndexQualityReport(
        book_id=book.book_id, role=book.role, report_version=f"{REPORT_VERSION}+{GATE_VERSION}",
        chunker_version=CHUNKER_VERSION,
        source_format="parsed-book/2" if isinstance(book, ParsedBookV2) else "parsed-book/1",
        outline_sha256=outline_sha256, metrics=metrics, checks=checks, accepted=accepted)
    quarantine_list = [{"block_id": out_ids[i], "lesson_id": lesson_of[i][0] if lesson_of[i] else None,
                        "evidence": gated[i].evidence, "flags": list(gated[i].flags),
                        "pages": list(gated[i].pages)} for i in sorted(quarantined)]
    lesson_blocks = {key.split(":", 1)[1]: blocks for key, blocks in expected.items() if key.startswith("lesson:")}
    return IndexBuild(chunks=chunks, report=report, quarantined=quarantine_list, lesson_blocks=lesson_blocks)


__all__ = ["CHUNKER_VERSION", "REPORT_VERSION", "IndexBuild", "check_lesson_parity", "index_book"]
