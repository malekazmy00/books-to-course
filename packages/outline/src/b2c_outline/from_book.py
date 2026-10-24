"""Create a reviewable CourseOutline from parsed structural headings."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from b2c_contracts import CourseOutline, Lesson, LessonSpan, Module, ParsedBook


class OutlineUnavailable(ValueError):
    """The parsed book has no trustworthy structural headings."""


@dataclass(frozen=True)
class InferredSection:
    """One model-proposed section bounded by exact ParsedBook block IDs."""
    title: str
    start_block: str
    end_block: str


@dataclass(frozen=True)
class OutlineCoverage:
    body_characters: int
    covered_characters: int
    ratio: float


@dataclass(frozen=True)
class _Heading:
    index: int
    level: int
    title: str
    block_id: str
    page: int


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value[:32] or "section"



def _heading_key(value: str) -> str:
    """Normalize OCR spacing/punctuation while retaining Unicode letters and digits."""
    value = value.casefold().translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"}))
    return "".join(character for character in value if character.isalnum())


_FRONT_MATTER_PREFIXES = (
    "publisherpreface", "publisherspreface", "publishernote", "publishersnote",
    "editorsnote", "editorpreface", "foreword", "preface", "copyright",
    "allrightsreserved", "tableofcontents", "contents", "acknowledgments",
    "acknowledgements", "dedication", "abouttheauthor", "aboutthepublisher",
    "aboutthisedition", "colophon", "مقدمةالناشر", "كلمةالناشر", "تقديمالناشر",
    "مقدمةالمحرر", "كلمةالمحرر", "حقوقالنشر", "حقوقالطبع", "فهرسالمحتويات",
    "المحتويات", "شكر وتقدير", "شكر وتقدير".replace(" ", ""), "اهداء",
    "عنالمؤلف", "عنالناشر", "عنالطبعة",
)


def is_front_matter_heading(title: str) -> bool:
    """Recognize explicit publisher/editor metadata headings, not instructional introductions."""
    key = _heading_key(title)
    return any(key.startswith(prefix) for prefix in _FRONT_MATTER_PREFIXES)


def front_matter_prefix_end(parsed: ParsedBook) -> int:
    """Return the first substantive top-level heading after an explicit front-matter run."""
    headings = [(_Heading(index, block.level or 1, block.text.strip(), block.block_id,
                          min(ref.page_no for ref in block.refs)))
                for index, block in enumerate(parsed.blocks) if block.type == "heading"]
    if not headings:
        return 0
    root_level = min(item.level for item in headings)
    roots = [item for item in headings if item.level == root_level]
    skipped_front_matter = False
    for item in roots:
        if is_front_matter_heading(item.title):
            skipped_front_matter = True
            continue
        return item.index if skipped_front_matter else 0
    return len(parsed.blocks) if skipped_front_matter else 0


def outline_from_book(
    parsed: ParsedBook,
    *,
    course_id: str,
    supplementary_book_ids: list[str] | None = None,
    infer_sections: Callable[[ParsedBook], list[InferredSection]] | None = None,
) -> CourseOutline:
    """Map PDF bookmarks/headings to modules and lessons without LLM inference.

    The first-level headings define modules; their immediate child headings define
    lessons. If a module has no children, it becomes a single lesson. This is a
    deterministic draft and still requires human review before course generation.
    """
    headings: list[_Heading] = []
    for index, block in enumerate(parsed.blocks):
        if block.type == "heading":
            page = min(ref.page_no for ref in block.refs)
            headings.append(_Heading(index, block.level or 1, block.text.strip(), block.block_id, page))
    if not headings:
        if infer_sections is None:
            raise OutlineUnavailable("no structural headings found and no outline inference provider is configured")
        outline = _outline_from_inferred_sections(
            parsed, infer_sections(parsed), course_id=course_id,
            supplementary_book_ids=supplementary_book_ids or [],
        )
        validate_outline_coverage(parsed, outline)
        return outline

    root_level = min(item.level for item in headings)
    raw_roots = [(position, item) for position, item in enumerate(
        (h for h in headings if h.level == root_level), start=1)]
    roots: list[tuple[int, _Heading]] = []
    for original_number, heading in raw_roots:
        key = _heading_key(heading.title)
        previous = roots[-1][1] if roots else None
        if previous is not None and key and key == _heading_key(previous.title):
            between = parsed.blocks[previous.index + 1:heading.index]
            if not any(block.type in _BODY_TYPES and block.text.strip() for block in between):
                # OCR may split/space a repeated heading across adjacent blocks.
                continue
        roots.append((original_number, heading))
    prefix_end = front_matter_prefix_end(parsed)
    excluded_prefix_block_ids = [block.block_id for block in parsed.blocks[:prefix_end]]
    roots = [(number, heading) for number, heading in roots if heading.index >= prefix_end]
    # A top-level heading with no body before the next top-level heading is a
    # structural artifact (often a repeated TOC/chapter title), not a lesson.
    # Drop it; contiguous spans below retain its source block with a neighboring
    # substantive lesson, so the reviewer still verifies complete source coverage.
    substantive_roots: list[tuple[int, _Heading]] = []
    for root_index, item in enumerate(roots):
        stop = roots[root_index + 1][1].index if root_index + 1 < len(roots) else len(parsed.blocks)
        heading = item[1]
        if any(block.type in _BODY_TYPES and block.text.strip()
               for block in parsed.blocks[heading.index + 1:stop]):
            substantive_roots.append(item)
    roots = substantive_roots
    if not roots:
        raise OutlineUnavailable("the parsed book contains headings but no substantive instructional content")
    modules: list[Module] = []
    previous_lesson_ids: list[str] = []

    def last_index(start: int, stop: int) -> int:
        return max(start, stop - 1)

    for root_position, (_, root) in enumerate(roots):
        module_number = root_position + 1
        next_root_index = roots[root_position + 1][1].index if root_position + 1 < len(roots) else len(parsed.blocks)
        descendants = [h for h in headings if root.index < h.index < next_root_index and h.level > root_level]
        child_level = min((h.level for h in descendants), default=None)
        children = [h for h in descendants if h.level == child_level] if child_level is not None else []
        module_id = f"m{module_number:02d}-{_slug(root.title)}"
        lesson_heads = children or [root]
        if children:
            substantive_children = []
            for child_index, child in enumerate(children):
                child_end = children[child_index + 1].index if child_index + 1 < len(children) else next_root_index
                if any(block.type in _BODY_TYPES and block.text.strip()
                       for block in parsed.blocks[child.index + 1:child_end]):
                    substantive_children.append(child)
            lesson_heads = substantive_children or [root]
        lessons: list[Lesson] = []

        for lesson_number, head in enumerate(lesson_heads, start=1):
            # Include any module introduction before its first subsection in lesson 1.
            start = (
                prefix_end if module_number == 1 and lesson_number == 1
                else root.index if bool(children and lesson_heads != [root]) and lesson_number == 1 else head.index
            )
            next_head_index = lesson_heads[lesson_number].index if lesson_number < len(lesson_heads) else next_root_index
            end = last_index(start, next_head_index)
            start_block = parsed.blocks[start]
            end_block = parsed.blocks[end]
            pages = [ref.page_no for block in parsed.blocks[start:end + 1] for ref in block.refs]
            lesson_id = f"{module_id}.l{lesson_number:02d}"
            lessons.append(Lesson(
                lesson_id=lesson_id, title=head.title, order=lesson_number,
                spine_span=LessonSpan(
                    start_block=start_block.block_id, end_block=end_block.block_id,
                    pages=(min(pages), max(pages)),
                ),
                provenance=parsed.toc_source,
                prior_lessons=previous_lesson_ids.copy(),
            ))
            previous_lesson_ids.append(lesson_id)

        modules.append(Module(module_id=module_id, title=root.title, order=module_number, lessons=lessons))

    outline = CourseOutline(
        course_id=course_id, spine_book_id=parsed.book_id,
        supplementary_book_ids=supplementary_book_ids or [], modules=modules,
        requires_review=True, excluded_prefix_block_ids=excluded_prefix_block_ids,
        excluded_prefix_policy="publisher-front-matter-v1" if excluded_prefix_block_ids else None,
    )
    validate_outline_coverage(parsed, outline)
    return outline


_BODY_TYPES = {"paragraph", "list_item", "table", "figure", "caption", "equation", "code", "footnote"}


def validate_outline_coverage(
    parsed: ParsedBook, outline: CourseOutline, *, minimum: float = 0.98,
) -> OutlineCoverage:
    """Require ordered, non-overlapping lesson spans covering >=98% of body text.

    Coverage is character-weighted over non-empty content blocks; page furniture and
    headings are excluded. Span endpoints must resolve to source blocks and lesson
    intervals may neither overlap nor leave gaps in the block sequence.
    """
    positions = {block.block_id: i for i, block in enumerate(parsed.blocks)}
    excluded_ids = outline.excluded_prefix_block_ids
    prefix_ids = [block.block_id for block in parsed.blocks[:len(excluded_ids)]]
    if excluded_ids != prefix_ids:
        raise OutlineUnavailable("excluded front matter must be an exact leading source prefix")
    first_course_block = len(excluded_ids)
    intervals: list[tuple[int, int]] = []
    for module in outline.modules:
        for lesson in module.lessons:
            span = lesson.spine_span
            if span.start_block not in positions or span.end_block not in positions:
                raise OutlineUnavailable(f"lesson {lesson.lesson_id} has an endpoint outside parsed source blocks")
            start, end = positions[span.start_block], positions[span.end_block]
            if end < start:
                raise OutlineUnavailable(f"lesson {lesson.lesson_id} has a reversed span")
            intervals.append((start, end))
    previous_end = first_course_block - 1
    covered_ids: set[str] = set()
    for start, end in intervals:
        if start <= previous_end:
            raise OutlineUnavailable("lesson spans overlap or are not ordered by source position")
        if start != previous_end + 1:
            raise OutlineUnavailable("lesson spans are not consecutive after the excluded front matter")
        covered_ids.update(block.block_id for block in parsed.blocks[start:end + 1])
        previous_end = end

    body = [b for b in parsed.blocks if b.block_id not in set(excluded_ids)
            and b.type in _BODY_TYPES and b.text.strip()]
    total = sum(len(b.text.strip()) for b in body)
    covered = sum(len(b.text.strip()) for b in body if b.block_id in covered_ids)
    ratio = covered / total if total else 1.0
    result = OutlineCoverage(total, covered, ratio)
    if ratio < minimum:
        raise OutlineUnavailable(f"outline covers {ratio:.2%} of body text; minimum is {minimum:.0%}")
    return result


def _outline_from_inferred_sections(
    parsed: ParsedBook, sections: list[InferredSection], *, course_id: str,
    supplementary_book_ids: list[str],
) -> CourseOutline:
    """Materialize provider-authored sections; never invent an LLM result locally."""
    if not sections:
        raise OutlineUnavailable("outline inference provider returned no sections")
    positions = {block.block_id: i for i, block in enumerate(parsed.blocks)}
    modules: list[Module] = []
    previous: list[str] = []
    for index, section in enumerate(sections, start=1):
        title = section.title.strip()
        if not title or section.start_block not in positions or section.end_block not in positions:
            raise OutlineUnavailable("inferred section has an empty title or unknown source block")
        start = 0 if index == 1 else positions[section.start_block]
        end = positions[section.end_block]
        if end < start:
            raise OutlineUnavailable("inferred section has a reversed source span")
        selected = parsed.blocks[start:end + 1]
        pages = [ref.page_no for block in selected for ref in block.refs]
        if not pages:
            raise OutlineUnavailable("inferred section has no page provenance")
        module_id = f"m{index:02d}-{_slug(title)}"
        lesson_id = f"{module_id}.l01"
        lesson = Lesson(
            lesson_id=lesson_id, title=title, order=1,
            spine_span=LessonSpan(
                start_block=parsed.blocks[start].block_id, end_block=section.end_block,
                pages=(min(pages), max(pages)),
            ),
            provenance="llm_inferred", prior_lessons=previous.copy(),
        )
        modules.append(Module(module_id=module_id, title=title, order=index, lessons=[lesson]))
        previous.append(lesson_id)
    return CourseOutline(
        course_id=course_id, spine_book_id=parsed.book_id,
        supplementary_book_ids=supplementary_book_ids, modules=modules,
        requires_review=True,
    )
