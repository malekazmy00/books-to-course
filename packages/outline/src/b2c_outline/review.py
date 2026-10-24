"""Deterministic, provenance-bearing structural outline approval policy."""
from __future__ import annotations
import hashlib
import json
import re
from dataclasses import dataclass
from b2c_contracts import CourseOutline, ParsedBook
from .from_book import front_matter_prefix_end
POLICY_ID = "b2c-outline-structural-review"
POLICY_VERSION = "1.0.0"
@dataclass(frozen=True)
class SystemReview:
    status: str
    policy_id: str
    policy_version: str
    evidence_sha256: str
    checks: tuple[str, ...]
class OutlineReviewRejected(ValueError):
    pass


def merge_bodyless_lessons(parsed: ParsedBook, outline: CourseOutline) -> CourseOutline:
    """Fold heading-only spans into the next substantive lesson deterministically."""
    positions = {block.block_id: index for index, block in enumerate(parsed.blocks)}
    body_types = {"paragraph", "list_item", "table", "figure", "caption", "equation", "code", "footnote"}
    entries = []
    for module_index, module in enumerate(outline.modules):
        for lesson_index, lesson in enumerate(module.lessons):
            start = positions.get(lesson.spine_span.start_block)
            end = positions.get(lesson.spine_span.end_block)
            if start is None or end is None or end < start:
                return outline
            substantive = any(block.type in body_types and block.text.strip()
                              for block in parsed.blocks[start:end + 1])
            entries.append((module_index, lesson_index, module, lesson, start, end, substantive))
    substantive_indices = [i for i, entry in enumerate(entries) if entry[6]]
    if not substantive_indices or len(substantive_indices) == len(entries):
        return outline
    modules_by_index = {}
    previous_ids = []
    for kept_order, entry_index in enumerate(substantive_indices):
        module_index, _, module, lesson, own_start, own_end, _ = entries[entry_index]
        start = entries[0][4] if kept_order == 0 else entries[substantive_indices[kept_order - 1]][5] + 1
        end = entries[-1][5] if kept_order == len(substantive_indices) - 1 else own_end
        if start > own_start or end < own_end or start < 0 or end >= len(parsed.blocks):
            return outline
        selected = parsed.blocks[start:end + 1]
        pages = [ref.page_no for block in selected for ref in block.refs]
        if not pages:
            return outline
        span = lesson.spine_span.model_copy(update={
            "start_block": parsed.blocks[start].block_id,
            "end_block": parsed.blocks[end].block_id,
            "pages": (min(pages), max(pages)),
        })
        repaired = lesson.model_copy(update={"spine_span": span, "prior_lessons": previous_ids.copy()})
        modules_by_index.setdefault(module_index, []).append(repaired)
        previous_ids.append(lesson.lesson_id)
    repaired_modules = []
    for module_index, module in enumerate(outline.modules):
        lessons = modules_by_index.get(module_index)
        if not lessons:
            continue
        repaired_lessons = [lesson.model_copy(update={"order": order})
                            for order, lesson in enumerate(lessons, start=1)]
        repaired_modules.append(module.model_copy(update={
            "lessons": repaired_lessons, "order": len(repaired_modules) + 1,
        }))
    return outline.model_copy(update={"modules": repaired_modules})


def include_leading_blocks_in_first_lesson(parsed: ParsedBook, outline: CourseOutline) -> CourseOutline:
    """Attach unstructured front matter before lesson 1 without changing its title.

    Only a prefix with no heading is eligible. The structural reviewer still checks
    that the lesson title is present and that every block is covered exactly once.
    """
    if not outline.modules or not outline.modules[0].lessons or outline.excluded_prefix_block_ids:
        return outline
    positions = {block.block_id: index for index, block in enumerate(parsed.blocks)}
    first_module = outline.modules[0]
    first_lesson = first_module.lessons[0]
    start = positions.get(first_lesson.spine_span.start_block)
    end = positions.get(first_lesson.spine_span.end_block)
    if start is None or end is None or start <= 0 or end < start:
        return outline
    if any(block.type == "heading" for block in parsed.blocks[:start]):
        return outline
    pages = [ref.page_no for block in parsed.blocks[:end + 1] for ref in block.refs]
    if not pages:
        return outline
    repaired_span = first_lesson.spine_span.model_copy(update={
        "start_block": parsed.blocks[0].block_id,
        "pages": (min(pages), max(pages)),
    })
    repaired_lesson = first_lesson.model_copy(update={"spine_span": repaired_span})
    repaired_module = first_module.model_copy(update={
        "lessons": [repaired_lesson, *first_module.lessons[1:]],
    })
    return outline.model_copy(update={"modules": [repaired_module, *outline.modules[1:]]})
def review_outline(parsed: ParsedBook, outline: CourseOutline) -> SystemReview:
    """Approve source structure only; makes no claim about pedagogy or human review."""
    checks: list[str] = []
    if parsed.role != "spine" or outline.spine_book_id != parsed.book_id:
        raise OutlineReviewRejected("spine identity mismatch")
    checks.append("spine_identity_matches")
    if parsed.toc_source == "llm_inferred":
        raise OutlineReviewRejected("LLM-inferred outlines require human review")
    checks.append("source_toc_is_non_inferred")
    lessons = [lesson for module in outline.modules for lesson in module.lessons]
    if not lessons: raise OutlineReviewRejected("outline contains no lessons")
    positions = {block.block_id: i for i, block in enumerate(parsed.blocks)}
    expected_excluded = [block.block_id for block in parsed.blocks[:front_matter_prefix_end(parsed)]]
    if outline.excluded_prefix_block_ids != expected_excluded:
        raise OutlineReviewRejected("excluded prefix does not match the explicit front-matter classifier")
    expected_policy = "publisher-front-matter-v1" if expected_excluded else None
    if outline.excluded_prefix_policy != expected_policy:
        raise OutlineReviewRejected("excluded prefix policy provenance is missing or inconsistent")
    expected_start, known = len(expected_excluded), set()
    for lesson in lessons:
        if lesson.provenance != parsed.toc_source: raise OutlineReviewRejected("lesson provenance differs from parsed source")
        if lesson.lesson_id in known: raise OutlineReviewRejected("lesson IDs must be unique")
        if any(prior not in known for prior in lesson.prior_lessons): raise OutlineReviewRejected("forward or unknown prerequisite")
        span = lesson.spine_span
        if span.start_block not in positions or span.end_block not in positions: raise OutlineReviewRejected("unknown source block")
        start, end = positions[span.start_block], positions[span.end_block]
        if start != expected_start or end < start: raise OutlineReviewRejected("spans must be ordered, contiguous, and cover the full block sequence")
        selected = parsed.blocks[start:end+1]
        if not any(block.type in {"paragraph", "list_item", "table", "figure", "caption", "equation", "code", "footnote"} and block.text.strip() for block in selected):
            raise OutlineReviewRejected(f"lesson {lesson.lesson_id} has no substantive body text")
        pages = [ref.page_no for block in selected for ref in block.refs]
        if not pages or span.pages != (min(pages), max(pages)): raise OutlineReviewRejected("page span does not match source blocks")
        title = re.sub(r"\\s+", " ", lesson.title).strip().casefold()
        if not title or not any(title in re.sub(r"\\s+", " ", b.text).strip().casefold() for b in selected):
            raise OutlineReviewRejected(f"lesson title absent from its source span: {lesson.lesson_id}")
        expected_start, known = end+1, known | {lesson.lesson_id}
    if expected_start != len(parsed.blocks): raise OutlineReviewRejected("lesson spans do not cover all parsed blocks")
    checks.extend(["lesson_ids_unique", "prerequisites_precede_lessons", "spans_contiguous_full_coverage", "page_spans_match_source", "lesson_titles_present_in_source", "front_matter_prefix_excluded", "lessons_have_body_text"])
    evidence = {"policy_id": POLICY_ID, "policy_version": POLICY_VERSION, "parsed_book": parsed.model_dump(mode="json"), "outline": outline.model_dump(mode="json"), "checks": checks}
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return SystemReview("approved", POLICY_ID, POLICY_VERSION, hashlib.sha256(raw).hexdigest(), tuple(checks))