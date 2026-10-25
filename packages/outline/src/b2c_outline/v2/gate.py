"""Deterministic outline gate (R4, audit 02 §5.3) and reference comparison.

The gate checks what a structural outline must get right before a human looks at it:
- coverage: every content block is in a lesson or in an explicitly excluded range (100%);
- titles: no multi-line, letter-spaced, lowercase-start, TOC-like or empty display title;
- no lesson without instructional words; no endnote/bibliography/index words and no back-matter
  block inside a lesson; every size violation carries a documented exception;
- the tree against a reference outline (ICDAR book-structure style): boundary, title and level
  agreement of modules/lessons with the reference entries, and zone agreement (a reference entry
  in the body must open taught content, one in front/back matter must be excluded).

The internal reference is the book's own PDF bookmarks (else its printed TOC). That is the same
evidence the builder starts from, so it checks fidelity to the source structure, not truth;
golden outlines (eval) are resolved and compared with the same function. Passing this gate never
accepts an outline: acceptance needs a human decision (``HUMAN_GATE_ID``).
"""
from __future__ import annotations

import statistics
from collections import Counter

from b2c_contracts import CourseOutlineV2, OutlineQualityReport, ParsedBookV2, QualityCheck

from .build import APPARATUS, INSTRUCTIONAL
from .reference import FURNITURE, RefEntry, bookmark_entries, printed_toc_entries, title_key
from .titles import malformed_title

GATE_ID = "outline-v2.structure"
HUMAN_GATE_ID = "outline-v2.human-approval"
REPORT_VERSION = "1"
BOUNDARY_TOLERANCE = 1  # content blocks (furniture not counted), audit 02 §5.3 "±1 block"
_CHECKS = (
    ("outline_matches_book", "==", 1.0),
    ("coverage_ratio", "==", 1.0),
    ("malformed_titles", "==", 0.0),
    ("empty_lessons", "==", 0.0),
    ("apparatus_words_in_lessons", "==", 0.0),
    ("back_matter_blocks_in_lessons", "==", 0.0),
    ("undocumented_size_violations", "==", 0.0),
    ("reference_title_f1", ">=", 0.95),
    ("reference_boundary_f1", ">=", 0.95),
    ("reference_level_accuracy", ">=", 0.95),
    ("reference_zone_accuracy", ">=", 0.95),
)
GATE_DEFINITION = {"gate_id": GATE_ID, "report_version": REPORT_VERSION, "boundary_tolerance": BOUNDARY_TOLERANCE,
                   "checks": [{"name": n, "op": op, "threshold": t} for n, op, t in _CHECKS]}


def _f1(tp: int, n_pred: int, n_ref: int) -> tuple[float, float, float]:
    p = tp / n_pred if n_pred else (1.0 if not n_ref else 0.0)
    r = tp / n_ref if n_ref else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def _nodes(outline: CourseOutlineV2, position: dict[str, int]) -> list[tuple[int, int, str, str]]:
    """(anchor position, level, display title, source title) of modules and non-intro lessons."""
    out = [(position[m.anchor_block], m.level, m.display_title, m.source_title) for m in outline.modules]
    out += [(position[lesson.anchor_block], lesson.level, lesson.display_title, lesson.source_title)
            for lesson in outline.lessons if lesson.kind == "section"]
    return out


def compare_to_reference(book: ParsedBookV2, outline: CourseOutlineV2, entries: list[RefEntry]) -> dict:
    """Boundary / title / level F1 of the outline tree against reference entries, and zone agreement.

    Compared levels: the reference's top body level and the level below it (chapters and sections)
    when the reference has it. Every outline node may match (a section the builder put one level
    off still matches, and costs level accuracy); outline nodes at compared levels that match no
    entry are false positives. Unresolved entries are misses."""
    position = {b.block_id: i for i, b in enumerate(book.blocks)}
    rank, k = [], 0
    for b in book.blocks:
        rank.append(k)
        k += b.type not in FURNITURE
    body = [e for e in entries if e.zone == "body"]
    levels = sorted({e.level for e in body})
    compared = set(levels[:2]) if len(levels) >= 2 and levels[1] == levels[0] + 1 else set(levels[:1])
    depth = max(compared) if compared else 0
    refs = [e for e in body if e.level in compared]
    nodes = _nodes(outline, position)
    used: set[int] = set()
    pairs: list[tuple[RefEntry, tuple]] = []
    for e in refs:
        if e.index is None:
            continue
        best = None
        for j, node in enumerate(nodes):
            if j in used:
                continue
            d = abs(rank[node[0]] - rank[e.index])
            if d > BOUNDARY_TOLERANCE:
                continue
            same = title_key(e.title) in (title_key(node[2]), title_key(node[3]))
            key = (d, not same, j)
            if best is None or key < best[0]:
                best = (key, j)
        if best is not None:
            used.add(best[1])
            pairs.append((e, nodes[best[1]]))
    n_pred = sum(1 for j, node in enumerate(nodes) if node[1] <= depth or j in used)
    tp = len(pairs)
    tp_title = sum(1 for e, node in pairs if title_key(e.title) in (title_key(node[2]), title_key(node[3])))
    bp, br, bf = _f1(tp, n_pred, len(refs))
    tp_, tr, tf = _f1(tp_title, n_pred, len(refs))
    level_ok = sum(1 for e, node in pairs if e.level == node[1])
    taught = [False] * len(book.blocks)
    for lesson in outline.lessons:
        a, b = position[lesson.spine_span.start_block], position[lesson.spine_span.end_block]
        for i in range(a, b + 1):
            taught[i] = True
    resolved = [e for e in entries if e.index is not None]
    zone_ok = sum(1 for e in resolved if taught[e.index] == (e.zone == "body"))
    return {
        "reference_entries": len(entries), "reference_unresolved": len(entries) - len(resolved),
        "reference_compared_levels": sorted(compared), "reference_compared_entries": len(refs),
        "reference_matched": tp, "reference_boundary_precision": round(bp, 4),
        "reference_boundary_recall": round(br, 4), "reference_boundary_f1": round(bf, 4),
        "reference_title_f1": round(tf, 4),
        "reference_level_accuracy": round(level_ok / tp, 4) if tp else (1.0 if not refs else 0.0),
        "reference_zone_accuracy": round(zone_ok / len(resolved), 4) if resolved else None,
        "reference_missed": [e.title for e in refs if all(e is not p[0] for p in pairs)][:20],
    }


def outline_metrics(book: ParsedBookV2, outline: CourseOutlineV2) -> dict:
    """Coverage, title, apparatus and size metrics (no thresholds)."""
    position = {b.block_id: i for i, b in enumerate(book.blocks)}
    words = [len(b.text.split()) if b.type in INSTRUCTIONAL else 0 for b in book.blocks]
    owner: list[str | None] = [None] * len(book.blocks)
    overlaps = 0
    for item, label in [*((lesson.spine_span, "lesson") for lesson in outline.lessons),
                        *((r, "excluded:" + r.reason) for r in outline.excluded_ranges)]:
        a, b = position[item.start_block], position[item.end_block]
        for i in range(a, b + 1):
            overlaps += owner[i] is not None
            owner[i] = label
    content = [i for i, b in enumerate(book.blocks) if b.type not in FURNITURE]
    covered = sum(1 for i in content if owner[i] is not None)
    lesson_blocks = [i for i in content if owner[i] == "lesson"]
    excluded_words: Counter = Counter()  # every word of the excluded content (notes, entries, captions ...)
    for i in content:
        if owner[i] and owner[i].startswith("excluded:"):
            excluded_words[owner[i][9:]] += len(book.blocks[i].text.split())
    malformed = [m.display_title for m in outline.modules if malformed_title(m.display_title)]
    malformed += [lesson.display_title for lesson in outline.lessons if malformed_title(lesson.display_title)]
    sizes = sorted(lesson.word_count for lesson in outline.lessons)
    policy = outline.size_policy
    in_bounds = sum(1 for s in sizes if policy.min_words <= s <= policy.max_words)
    toc_like = sum(1 for i in lesson_blocks if book.blocks[i].type == "toc_entry"
                   or (book.blocks[i].type == "heading" and "toc_like" in malformed_title(book.blocks[i].text)))
    return {
        "modules": len(outline.modules), "lessons": outline.lesson_count,
        "chapter_intro_lessons": sum(1 for lesson in outline.lessons if lesson.kind == "chapter_intro"),
        "excluded_ranges": len(outline.excluded_ranges),
        "content_blocks": len(content), "covered_content_blocks": covered, "overlapping_blocks": overlaps,
        "coverage_ratio": round(covered / len(content), 6) if content else 1.0,
        "words_in_lessons": sum(words[i] for i in lesson_blocks),
        "words_excluded": sum(excluded_words.values()),
        "words_excluded_by_reason": dict(sorted(excluded_words.items())),
        "body_zone_words_excluded": sum(len(book.blocks[i].text.split()) for i in content
                                        if owner[i] and owner[i] != "lesson" and book.blocks[i].zone == "body"),
        "instructional_words_excluded": sum(words[i] for i in content if owner[i] and owner[i] != "lesson"),
        "malformed_titles": len(malformed), "malformed_title_examples": malformed[:10],
        "empty_lessons": sum(1 for lesson in outline.lessons if lesson.word_count == 0),
        "apparatus_words_in_lessons": sum(len(book.blocks[i].text.split()) for i in lesson_blocks
                                          if book.blocks[i].type in APPARATUS),
        "back_matter_blocks_in_lessons": sum(1 for i in lesson_blocks if book.blocks[i].zone == "back_matter"),
        "front_matter_blocks_in_lessons": sum(1 for i in lesson_blocks if book.blocks[i].zone == "front_matter"),
        "toc_like_blocks_in_lessons": toc_like,
        "undocumented_size_violations": sum(1 for lesson in outline.lessons
                                            if lesson.size_status != "ok" and not lesson.size_exception),
        "lesson_words_min": sizes[0] if sizes else 0, "lesson_words_median": statistics.median(sizes) if sizes else 0,
        "lesson_words_p90": sizes[int(len(sizes) * 0.9)] if sizes else 0, "lesson_words_max": sizes[-1] if sizes else 0,
        "lessons_below_min": sum(1 for lesson in outline.lessons if lesson.size_status == "below_min"),
        "lessons_above_max": sum(1 for lesson in outline.lessons if lesson.size_status == "above_max"),
        "size_in_bounds_ratio": round(in_bounds / len(sizes), 4) if sizes else 1.0,
    }


def _matches_book(book: ParsedBookV2, outline: CourseOutlineV2) -> bool:
    if outline.spine_book_id != book.book_id or outline.block_count != len(book.blocks):
        return False
    ids = {b.block_id for b in book.blocks}
    spans = [*(lesson.spine_span for lesson in outline.lessons), *outline.excluded_ranges]
    anchors = [m.anchor_block for m in outline.modules] + [lesson.anchor_block for lesson in outline.lessons]
    return all(s.start_block in ids and s.end_block in ids for s in spans) and all(a in ids for a in anchors)


def internal_reference(book: ParsedBookV2) -> tuple[str | None, list[RefEntry]]:
    marks = bookmark_entries(book)
    if marks:
        return "pdf_outline", marks
    toc = printed_toc_entries(book)
    if toc:
        return "printed_toc", toc
    return None, []


def outline_quality(book: ParsedBookV2, outline: CourseOutlineV2, *, outline_sha256: str | None = None,
                    reference: tuple[str | None, list[RefEntry]] | None = None) -> OutlineQualityReport:
    """The deterministic gate. ``accepted`` is the machine verdict only."""
    matches = _matches_book(book, outline)
    metrics: dict = {"outline_matches_book": 1.0 if matches else 0.0}
    ref_name, entries = reference if reference is not None else internal_reference(book)
    if matches:
        metrics.update(outline_metrics(book, outline))
        if entries:
            metrics.update(compare_to_reference(book, outline, entries))
        toc = printed_toc_entries(book)
        if toc and ref_name != "printed_toc":
            toc_metrics = compare_to_reference(book, outline, toc)
            metrics["printed_toc_entries"] = len(toc)
            metrics["printed_toc_zone_accuracy"] = toc_metrics["reference_zone_accuracy"]
            metrics["printed_toc_boundary_recall"] = toc_metrics["reference_boundary_recall"]
    checks = []
    for name, op, threshold in _CHECKS:
        value = metrics.get(name)
        if value is None or (name.startswith("reference_") and not entries):
            checks.append(QualityCheck(name=name, value=None, op=op, threshold=threshold, passed=None,
                                       detail="no reference outline" if name.startswith("reference_") else ""))
            continue
        value = float(value)
        ok = {"==": value == threshold, ">=": value >= threshold, "<=": value <= threshold}[op]
        checks.append(QualityCheck(name=name, value=value, op=op, threshold=threshold, passed=ok))
    accepted = all(ch.passed is not False for ch in checks) and any(ch.passed for ch in checks)
    return OutlineQualityReport(book_id=book.book_id, report_version=REPORT_VERSION, outline_sha256=outline_sha256,
                                reference=ref_name if entries else None, metrics=metrics, checks=checks,
                                accepted=accepted)


__all__ = ["BOUNDARY_TOLERANCE", "GATE_DEFINITION", "GATE_ID", "HUMAN_GATE_ID", "REPORT_VERSION",
           "compare_to_reference", "internal_reference", "outline_metrics", "outline_quality"]
