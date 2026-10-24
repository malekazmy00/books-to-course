"""Generate source-grounded questions and answers for lessons and course scopes.

Product content generation belongs to Phase 5. It is deliberately separate from
Phase 3 retrieval benchmark qrels, which require independent human adjudication.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any, Literal, Protocol

from b2c_contracts import CourseOutline, ParsedBook, StructuredChunk
from b2c_llm_gateway import LLMRequestRejected, OpenRouterQuotaExceeded, FreeTokenQuotaExceeded, OpenRouterRateLimited
from pydantic import BaseModel, ConfigDict, Field, create_model


class _Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=8, max_length=600)
    answer: str = Field(min_length=4, max_length=1000)
    evidence_chunk_id: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=12, max_length=700)


class _CandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_Candidate] = Field(min_length=1, max_length=20)


class _JudgeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_index: int = Field(ge=0)
    answer_supported: bool
    question_clear_and_answerable: bool
    relevant_to_scope: bool
    rationale: str = Field(min_length=4, max_length=500)


class _JudgeSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgments: list[_JudgeItem] = Field(min_length=1, max_length=20)


def _candidate_set_schema(exact_count: int) -> type[_CandidateSet]:
    """Constrain structured decoding to the actual batch size, not the global maximum."""
    return create_model("_CandidateSet", __base__=_CandidateSet,
        items=(list[_Candidate], Field(min_length=exact_count, max_length=exact_count)))


def _judge_set_schema(exact_count: int) -> type[_JudgeSet]:
    return create_model("_JudgeSet", __base__=_JudgeSet,
        judgments=(list[_JudgeItem], Field(min_length=exact_count, max_length=exact_count)))


class StructuredLLMGateway(Protocol):
    model_id: str

    def structured(self, messages, schema, *, prompt_version: str, temperature: float = 0.0,
                   max_tokens: int | None = None): ...


class ScopeInput(BaseModel):
    """One lesson, chapter/group, or whole-course generation request."""
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)
    scope_id: str = Field(min_length=1)
    kind: Literal["lesson", "group", "course", "assessment"]
    scope_title: str = Field(min_length=1)
    lesson_ids: list[str] = Field(min_length=1)
    lesson_titles: list[str] = Field(min_length=1)
    chunks: list[StructuredChunk] = Field(min_length=1)
    question_count: int = Field(default=5, ge=1, le=20)


class GroundedQA(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    qa_id: str
    question: str
    answer: str
    evidence_chunk_ids: list[str] = Field(min_length=1)
    book_id: str
    role: str
    pages: list[int] = Field(min_length=1)
    evidence_quote: str
    model_id: str
    prompt_version: str
    automated_checks: dict[str, object]
    status: Literal["final"] = "final"


class ScopeCheckpointError(RuntimeError):
    """A validated scope could not be persisted for a safe workflow retry."""


class ScopeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope_id: str
    kind: str
    lesson_ids: list[str]
    status: Literal["final", "failed"]
    items: list[GroundedQA] = []
    errors: list[str] = []
    attempts: int = 0


class CourseQAResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["final", "failed", "paused"]
    expected_lesson_ids: list[str]
    covered_lesson_ids: list[str]
    scopes: list[ScopeResult]
    errors: list[str] = []
    model_id: str
    prompt_version: str
    approved_outline_sha256: str | None = None
    spine_parsed_sha256: str | None = None
    spine_book_id: str | None = None
    provider_usage: dict[str, int | float | None] = Field(default_factory=dict)
    retrieval_mode: Literal["lexical", "hybrid"] = "lexical"
    retrieval_top_k: int = Field(default=20, ge=1, le=100)
    lesson_assets: list[dict[str, Any]] = Field(default_factory=list)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def _is_title_meta_question(question: str) -> bool:
    """Reject recall about a book/lesson label while allowing questions about its topic."""
    value = unicodedata.normalize("NFKC", question).casefold()
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    english = (
        r"\bwhat\s+(?:is|was)\s+the\s+(?:title|name|heading)\s+of\b",
        r"\bwhat\s+(?:is|was)\s+(?:the\s+)?(?:book|chapter|section|lesson|module)\s+(?:called|named|titled)\b",
        r"\b(?:name|identify)\s+(?:the\s+)?(?:title|heading|(?:book|chapter|section|lesson|module)\s+name)\b",
        r"\bwhat\s+is\s+this\s+(?:chapter|section|lesson|module)\s+about\b",
    )
    arabic = (
        r"(?:ما|ماذا)\s+(?:هو\s+)?(?:عنوان|اسم)\s+(?:هذا\s+|هذه\s+)?(?:الكتاب|الفصل|الدرس|القسم|الوحدة|المقرر)",
        r"(?:بماذا|بما)\s+(?:يسمى|سمي)\s+(?:هذا\s+|هذه\s+)?(?:الفصل|الدرس|القسم|الكتاب|الوحدة)",
        r"(?:عنوان|اسم)\s+(?:الفصل|الدرس|القسم|الكتاب|الوحدة)\s+(?:ما|؟|\?)",
        r"عن\s+ماذا\s+يتحدث\s+(?:هذا\s+|هذه\s+)?(?:الفصل|الدرس|القسم|الوحدة)",
    )
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in (*english, *arabic))


def _quote_matches_source(quote: str, source: str) -> bool:
    """Exact contiguous match after Unicode compatibility and whitespace normalization."""
    normalize = lambda value: " ".join(unicodedata.normalize("NFKC", value).split())
    return bool(normalize(quote)) and normalize(quote) in normalize(source)


def _quote_is_declarative(quote: str) -> bool:
    """Reject questions and exercise prompts as answer evidence."""
    cleaned = unicodedata.normalize("NFKC", quote).strip()
    if "?" in cleaned:
        return False
    return not re.match(r"^(?:\d+[.)]?\s*)?(?:what|which|how|why|find|calculate|compute|determine|define|explain|show|solve|prove|state|list|identify|select|choose|suppose)\b",
                        cleaned, flags=re.IGNORECASE)



def _quote_options(text: str, *, limit: int = 8) -> list[str]:
    # Provide exact source sentences for reliable verbatim citation copying.
    candidates = re.split(r"(?<=[.!?])\s+|[\r\n]+", text)
    selected: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        quote = candidate.strip()
        normalized = " ".join(unicodedata.normalize("NFKC", quote).split())
        if (24 <= len(normalized) <= 500 and _quote_is_declarative(quote)
                and any(ch.isalpha() for ch in quote) and normalized not in seen):
            selected.append(quote)
            seen.add(normalized)
            if len(selected) == limit:
                break
    if not selected:
        quote = text.strip()
        if len(quote) <= 500 and _quote_is_declarative(quote) and any(ch.isalpha() for ch in quote):
            selected.append(quote)
    return selected

def _bounded_source(chunks: list[StructuredChunk], max_input_chars: int) -> tuple[list[StructuredChunk], str]:
    visible: list[StructuredChunk] = []
    rows: list[dict[str, object]] = []
    size = 0
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.chunk_id in seen:
            raise ValueError(f"duplicate chunk ID in scope: {chunk.chunk_id}")
        seen.add(chunk.chunk_id)
        if not chunk.text.strip():
            continue
        row = {"chunk_id": chunk.chunk_id, "book_id": chunk.book_id, "role": chunk.role,
               "section_path": chunk.section_path, "pages": chunk.pages, "text": chunk.text,
               "quote_options": _quote_options(chunk.text)}
        encoded = json.dumps(row, ensure_ascii=False)
        if len(encoded) > max_input_chars:
            raise ValueError(f"source chunk {chunk.chunk_id} exceeds max_input_chars; rechunk it first")
        if size + len(encoded) > max_input_chars:
            break
        visible.append(chunk)
        rows.append(row)
        size += len(encoded)
    if not visible:
        raise ValueError("no non-empty source chunks fit within the configured input limit")
    return visible, json.dumps(rows, ensure_ascii=False)


def _partition_chunks(chunks: list[StructuredChunk], max_input_chars: int) -> list[list[StructuredChunk]]:
    """Partition every eligible chunk into bounded batches; never silently drop tail chunks."""
    batches: list[list[StructuredChunk]] = []
    current: list[StructuredChunk] = []
    def serialized_size(batch: list[StructuredChunk]) -> int:
        rows = [{"chunk_id": chunk.chunk_id, "book_id": chunk.book_id, "role": chunk.role,
                 "section_path": chunk.section_path, "pages": chunk.pages, "text": chunk.text,
                 "quote_options": _quote_options(chunk.text)}
                for chunk in batch]
        return len(json.dumps(rows, ensure_ascii=False))
    for chunk in chunks:
        if serialized_size([chunk]) > max_input_chars:
            raise ValueError(f"source chunk {chunk.chunk_id} exceeds max_input_chars; rechunk it first")
        if current and serialized_size(current + [chunk]) > max_input_chars:
            batches.append(current)
            current = []
        current.append(chunk)
    if current:
        batches.append(current)
    return batches


def _allocate_batch_counts(batch_count: int, total: int) -> list[tuple[int, int]]:
    """Spread a scope's target count over batches, sampling batches evenly if needed."""
    if batch_count <= total:
        counts = [1] * batch_count
        for index in range(total - batch_count):
            counts[index % batch_count] += 1
        return list(enumerate(counts))
    chosen = []
    for n in range(total):
        # Start at the first source batch so lesson QA includes primary-spine evidence;
        # then spread remaining questions across the rest of the lesson's source batches.
        index = min(batch_count - 1, int(n * batch_count / total))
        chosen.append(index)
    return [(index, 1) for index in chosen]


def _derive_scopes(outline: CourseOutline, spine_book: ParsedBook, chunks: list[StructuredChunk],
                   *, lesson_question_count: int, group_question_count: int,
                   course_question_count: int, outline_approved: bool = False,
                   include_review_assessments: bool = False,
                   assessment_question_count: int = 10) -> tuple[list[ScopeInput], list[str]]:
    if outline.requires_review and not outline_approved:
        raise ValueError("course outline is not approved; generate lesson assets only from an accepted outline")
    if outline.spine_book_id != spine_book.book_id or spine_book.role != "spine":
        raise ValueError("accepted outline and ParsedBook spine identity do not match")
    positions = {block.block_id: index for index, block in enumerate(spine_book.blocks)}
    supplementary_ids = set(outline.supplementary_book_ids)
    for chunk in chunks:
        if chunk.book_id != spine_book.book_id and chunk.book_id not in supplementary_ids:
            raise ValueError(f"chunk {chunk.chunk_id} belongs to a book outside the accepted outline")
    all_lesson_ids: list[str] = []
    lesson_chunks: dict[str, list[StructuredChunk]] = {}
    modules: list[tuple[str, str, list[str]]] = []
    used_spine_ids: set[str] = set()
    lesson_titles: dict[str, str] = {}
    for module in outline.modules:
        module_lessons: list[str] = []
        for lesson in module.lessons:
            if lesson.lesson_id in lesson_chunks:
                raise ValueError(f"duplicate lesson_id in outline: {lesson.lesson_id}")
            span = lesson.spine_span
            if span.start_block not in positions or span.end_block not in positions:
                raise ValueError(f"lesson {lesson.lesson_id} references missing spine blocks")
            start, end = positions[span.start_block], positions[span.end_block]
            if end < start:
                raise ValueError(f"lesson {lesson.lesson_id} has reversed span")
            ids = {block.block_id for block in spine_book.blocks[start:end + 1]}
            selected: list[StructuredChunk] = []
            for chunk in chunks:
                if chunk.book_id != spine_book.book_id:
                    continue
                intersection = ids.intersection(chunk.block_ids)
                if intersection and intersection != set(chunk.block_ids):
                    raise ValueError(f"chunk {chunk.chunk_id} crosses accepted lesson boundaries")
                if intersection:
                    selected.append(chunk)
                    used_spine_ids.add(chunk.chunk_id)
            if not selected:
                raise ValueError(f"lesson {lesson.lesson_id} has no indexed source chunks")
            # A supplementary book has no automatic lesson link in CourseOutline. Keep
            # lesson-level QA grounded in the accepted spine span; broad inclusion of every
            # supplemental chunk caused unrelated material to be assigned to lessons.
            lesson_chunks[lesson.lesson_id] = selected
            module_lessons.append(lesson.lesson_id)
            all_lesson_ids.append(lesson.lesson_id)
            lesson_titles[lesson.lesson_id] = lesson.title
        modules.append((module.module_id, module.title, module_lessons))
    scopes = [ScopeInput(scope_id=lesson_id, kind="lesson", scope_title=lesson_titles[lesson_id],
        lesson_ids=[lesson_id], lesson_titles=[lesson_titles[lesson_id]],
        chunks=lesson_chunks[lesson_id], question_count=lesson_question_count)
        for lesson_id in all_lesson_ids]
    for module_id, module_title, lesson_ids in modules:
        joined = []
        seen = set()
        for lesson_id in lesson_ids:
            for chunk in lesson_chunks[lesson_id]:
                if chunk.chunk_id not in seen:
                    joined.append(chunk); seen.add(chunk.chunk_id)
        scopes.append(ScopeInput(scope_id=module_id, kind="group", scope_title=module_title,
            lesson_ids=lesson_ids, lesson_titles=[lesson_titles[item] for item in lesson_ids],
            chunks=joined, question_count=group_question_count))
    course_chunks = [c for c in chunks if c.chunk_id in used_spine_ids or c.book_id in supplementary_ids]
    if not used_spine_ids:
        raise ValueError("outline scopes did not resolve any spine chunks")
    scopes.append(ScopeInput(scope_id=outline.course_id, kind="course", scope_title=outline.course_id,
        lesson_ids=all_lesson_ids, lesson_titles=[lesson_titles[item] for item in all_lesson_ids],
        chunks=course_chunks, question_count=course_question_count))
    if include_review_assessments and len(all_lesson_ids) >= 2:
        # Keep sequence and source provenance: split lessons contiguously into two
        # balanced terms, then build midterm, term and final scopes from those spans.
        term_split = (len(all_lesson_ids) + 1) // 2
        term_lesson_ids = [all_lesson_ids[:term_split], all_lesson_ids[term_split:]]
        lesson_scope_map = {scope.scope_id: scope for scope in scopes if scope.kind == "lesson"}
        periods: list[tuple[str, list[str]]] = []
        for term_index, term_ids in enumerate(term_lesson_ids, start=1):
            mid_split = max(1, (len(term_ids) + 1) // 2)
            periods.append((f"midterm-{term_index}", term_ids[:mid_split]))
            periods.append((f"term-{term_index}", term_ids))
        periods.append(("final", all_lesson_ids))
        labels = {"midterm-1": "Midterm 1", "term-1": "Term 1",
                  "midterm-2": "Midterm 2", "term-2": "Term 2", "final": "Final"}
        for scope_id, lesson_subset in periods:
            exam_chunks: list[StructuredChunk] = []
            seen_chunks: set[str] = set()
            for lesson_id in lesson_subset:
                for chunk in lesson_scope_map[lesson_id].chunks:
                    if chunk.chunk_id not in seen_chunks:
                        exam_chunks.append(chunk)
                        seen_chunks.add(chunk.chunk_id)
            if exam_chunks:
                scopes.append(ScopeInput(scope_id=f"assessment:{scope_id}", kind="assessment",
                    scope_title=labels[scope_id], lesson_ids=lesson_subset,
                    lesson_titles=[lesson_titles[item] for item in lesson_subset],
                    chunks=exam_chunks, question_count=assessment_question_count))
    return scopes, all_lesson_ids


def _prompt_avoid_questions(scope: ScopeInput, prior_scopes: Sequence[ScopeResult],
                            accepted_questions: set[str], *, max_questions: int = 24,
                            max_chars: int = 3000) -> set[str]:
    """Bound duplicate examples sent to the model; global rejection remains exact.

    Current-scope accepted questions have priority. Other examples come only from
    overlapping lesson scopes, so an unrelated course bank cannot grow each prompt.
    Never truncate a question: omitted examples are still rejected programmatically.
    """
    lessons = set(scope.lesson_ids)
    related = {_norm(item.question) for prior in prior_scopes
               if lessons.intersection(prior.lesson_ids) for item in prior.items}
    selected: set[str] = set()
    for question in [*sorted(accepted_questions), *sorted(related - accepted_questions)]:
        if len(selected) >= max_questions:
            break
        if len(json.dumps(sorted(selected | {question}), ensure_ascii=False)) <= max_chars:
            selected.add(question)
    return selected


def _generate_batch(scope: ScopeInput, chunks: list[StructuredChunk], gateway: StructuredLLMGateway,
                    *, count: int, prompt_version: str, max_tokens: int,
                    previous_errors: list[str], excluded_questions: set[str],
                    prompt_excluded_questions: set[str] | None = None) -> list[_Candidate]:
    source_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    system = (
        "Generate exactly the requested number of distinct factual question candidates from the source chunks. "
        "Each question must test a concept, explanation, relationship, evidence, or application in the instructional body. "
        "Never ask for a book, chapter, section, module, or lesson title/name/heading; titles are navigation metadata, not learning content. "
        "Each question must directly fit the supplied lesson/group/course scope. Use only those chunks. "
        "Write one concise answer in your own wording, entailed by the cited source span itself; do not infer beyond it. "
        "Prefer the spine chunks as the lesson's primary evidence. Use a supplementary chunk only when its content "
        "directly reinforces the exact lesson topic; ignore supplementary material about other topics. "
        "Each source chunk includes quote_options. Select one option and copy it verbatim as evidence_quote, "
        "including original spelling and punctuation; do not paraphrase, normalize, add ellipses, or combine spans. "
        "Use only an option from the cited chunk. It must "
        "be a declarative explanatory statement, not a question, exercise, instruction, or unanswered problem. "
        "Do not put the answer in the question. "
        "Return exact supplied chunk IDs. Do not create multiple-choice distractors."
    )
    user = json.dumps({"scope_id": scope.scope_id, "scope_kind": scope.kind,
        "scope_title": scope.scope_title,
        "lesson_contexts": list(zip(scope.lesson_ids, scope.lesson_titles)),
        "lesson_ids": scope.lesson_ids,
        "requested_count": count, "source_chunks": json.loads(_bounded_source(chunks, 10**9)[1]),
        "questions_to_avoid": sorted(excluded_questions if prompt_excluded_questions is None
                                     else prompt_excluded_questions),
        "previous_validation_errors": previous_errors},
        ensure_ascii=False)
    generated = gateway.structured(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        _candidate_set_schema(count), prompt_version=prompt_version + ":generate", temperature=0.0, max_tokens=max_tokens,
    )
    if len(generated.items) < count:
        raise ValueError(f"generator returned {len(generated.items)} items; requested {count}")
    chosen = generated.items[:count]
    normalized: set[str] = set()
    for item in chosen:
        if item.evidence_chunk_id not in source_by_id:
            raise ValueError(f"unknown/out-of-scope chunk citation: {item.evidence_chunk_id}")
        if not _quote_matches_source(item.evidence_quote, source_by_id[item.evidence_chunk_id].text):
            raise ValueError(f"evidence_quote is not an exact source substring in {item.evidence_chunk_id}")
        if not _quote_is_declarative(item.evidence_quote):
            raise ValueError("evidence_quote is a question or exercise prompt, not an answer-supporting statement")
        if len(_norm(item.question)) < 4:
            raise ValueError("question is too short after normalization")
        if _is_title_meta_question(item.question):
            raise ValueError("question asks for a title/name/heading instead of assessing the topic")
        qnorm = _norm(item.question)
        if qnorm in normalized or qnorm in excluded_questions:
            raise ValueError("duplicate question in this request or another requested scope")
        if qnorm in _norm(item.evidence_quote):
            raise ValueError("question contains its complete answer quote")
        normalized.add(qnorm)
    return chosen


def _judge_output_budget(candidate_count: int, caller_max_tokens: int) -> int:
    """Reserve generous JSON/rationale space without reusing the generation ceiling.

    Each judgment has three booleans, an index and up to 500 rationale characters.
    Keep at least 2048 tokens for small batches and 768 per candidate plus envelope
    space for larger batches. Never increase the caller's requested output limit.
    This is an output ceiling, not a target or measured token consumption.
    """
    return min(caller_max_tokens, max(2048, 256 + 768 * candidate_count))


def _judge_batch(candidates: list[_Candidate], chunks: list[StructuredChunk], scope: ScopeInput,
                 gateway: StructuredLLMGateway,
                 *, prompt_version: str, max_tokens: int) -> list[_JudgeItem]:
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    payload = [{"index": index, "scope_kind": scope.kind, "scope_title": scope.scope_title,
                "lesson_contexts": list(zip(scope.lesson_ids, scope.lesson_titles)),
                "question": candidate.question,
                "answer": candidate.answer,
                "evidence_quote": candidate.evidence_quote,
                "source_text": by_id[candidate.evidence_chunk_id].text}
               for index, candidate in enumerate(candidates)]
    result = gateway.structured(
        [{"role": "system", "content": (
            "Independently validate each generated study Q/A candidate against its source text. "
            "Mark answer_supported true only if the generated answer is entailed by the cited evidence span and source context. "
            "Mark question_clear_and_answerable false for ambiguity, triviality, or a question that "
            "cannot be answered from this source. Mark relevant_to_scope false if unrelated to the "
            "instructional topic. Treat lesson/module/chapter titles as navigation metadata, not answer targets. "
            "Return one judgment for each exact index; do not "
            "rewrite questions or answers.")},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        _judge_set_schema(len(candidates)), prompt_version=prompt_version + ":judge", temperature=0.0, max_tokens=max_tokens,
    )
    judgments = {item.item_index: item for item in result.judgments}
    if len(judgments) != len(candidates) or set(judgments) != set(range(len(candidates))):
        raise ValueError("judge did not return exactly one result per generated item")
    return [judgments[index] for index in range(len(candidates))]


def generate_course_qa(
    outline: CourseOutline, spine_book: ParsedBook, chunks: list[StructuredChunk],
    gateway: StructuredLLMGateway, *, lesson_question_count: int = 5,
    group_question_count: int = 5, course_question_count: int = 10,
    prompt_version: str = "phase5-grounded-qa-v4", max_regeneration_rounds: int = 2,
    max_input_chars: int = 18000, max_tokens: int = 4096,
    include_review_assessments: bool = False, assessment_question_count: int = 10,
    lesson_ids: list[str] | None = None,
    outline_approved: bool = False, precompleted_scopes: list[ScopeResult] | None = None,
    accepted_precompleted_model_ids: set[str] | None = None,
    accepted_precompleted_prompt_versions: set[str] | None = None,
    scope_retriever: Callable[[str, Sequence[StructuredChunk], int], Sequence[StructuredChunk]] | None = None,
    retrieval_top_k: int = 20,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    scope_checkpoint_callback: Callable[[ScopeResult], None] | None = None,
) -> CourseQAResult:
    """Generate final candidates per lesson and requested groups/course, or fail closed.

    Output is marked final only after exact-citation checks, exact-answer-quote checks,
    de-duplication, and a structured source-support/clarity judge pass. No provider is
    selected here: pass the project's configured LLMGateway instance.
    """
    # The Inception free endpoint rejects larger request bodies with a generic 400.
    # Bound its per-request source payload more tightly; all candidates are still checked locally.
    if getattr(gateway, "provider_name", None) == "inceptionlabs":
        max_input_chars = min(max_input_chars, 12_000)
    scopes, expected_lesson_ids = _derive_scopes(outline, spine_book, chunks,
        lesson_question_count=lesson_question_count, group_question_count=group_question_count,
        course_question_count=course_question_count, outline_approved=outline_approved,
        include_review_assessments=include_review_assessments,
        assessment_question_count=assessment_question_count)
    if lesson_ids is not None:
        if not lesson_ids or len(set(lesson_ids)) != len(lesson_ids):
            raise ValueError("lesson_ids must be a nonempty unique selection")
        unknown = set(lesson_ids) - set(expected_lesson_ids)
        if unknown:
            raise ValueError("lesson_ids contain unknown approved lessons")
        selected = set(lesson_ids)
        scopes = [scope for scope in scopes if scope.kind == "lesson" and scope.scope_id in selected]
        expected_lesson_ids = [lesson_id for lesson_id in expected_lesson_ids if lesson_id in selected]
    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be positive")
    if lesson_question_count < 1 or group_question_count < 1 or course_question_count < 1:
        raise ValueError("question counts must be positive")
    if not 0 <= max_regeneration_rounds <= 5:
        raise ValueError("max_regeneration_rounds must be between 0 and 5")
    expected = set(expected_lesson_ids)

    scope_by_id = {scope.scope_id: scope for scope in scopes}
    precompleted_by_id: dict[str, ScopeResult] = {}
    partial_by_id: dict[str, ScopeResult] = {}
    all_questions: set[str] = set()
    for result in precompleted_scopes or []:
        scope = scope_by_id.get(result.scope_id)
        if (scope is None or result.kind != scope.kind or result.lesson_ids != scope.lesson_ids
                or result.status not in {"final", "failed"}):
            raise ValueError("precompleted results must match requested scopes")
        if (result.scope_id in precompleted_by_id or result.scope_id in partial_by_id
                or (not result.items and result.status != "failed")
                or len(result.items) > scope.question_count):
            raise ValueError("precompleted scope result is duplicate or has an invalid item count")
        scope_source_ids = {chunk.chunk_id for chunk in scope.chunks}
        # Previously approved final lessons may cite supplemental sources. Reuse those
        # only after exact source and automated relevance validation, while new lesson
        # generation remains scoped to its mapped spine span.
        source_lookup = {chunk.chunk_id: chunk for chunk in chunks}
        for item in result.items:
            accepted_prompt_versions = {prompt_version, *(accepted_precompleted_prompt_versions or set())}
            if item.model_id not in {gateway.model_id, *(accepted_precompleted_model_ids or set())} or item.prompt_version not in accepted_prompt_versions:
                raise ValueError("precompleted QA model or prompt version does not match this run")
            if len(item.evidence_chunk_ids) != 1:
                raise ValueError("precompleted QA must cite exactly one source chunk")
            source = source_lookup.get(item.evidence_chunk_ids[0])
            if (source is None or (source.chunk_id not in scope_source_ids and source.role != "supplementary")
                    or source.book_id != item.book_id or source.role != item.role
                    or source.pages != item.pages or not _quote_matches_source(item.evidence_quote, source.text)
                    or not _quote_is_declarative(item.evidence_quote) or len(_norm(item.question)) < 4
                    or _is_title_meta_question(item.question)
                    or not all(item.automated_checks.get(key) is True for key in (
                        "exact_chunk_id", "exact_source_quote", "answer_supported",
                        "question_clear_and_answerable", "relevant_to_scope", "deduplicated"))):
                raise ValueError("precompleted QA failed source provenance validation")
            normalized = _norm(item.question)
            if normalized in all_questions and not any(
                    item.qa_id == old.qa_id and item == old
                    for prior in (precompleted_scopes or [])
                    if (scope.kind in {"group", "course", "assessment"} and prior.kind == "lesson" and prior.scope_id in scope.lesson_ids)
                    or (scope.kind == "lesson" and prior.kind in {"group", "course", "assessment"} and scope.scope_id in prior.lesson_ids)
                    for old in prior.items):
                raise ValueError("precompleted QA contains duplicate questions")
            all_questions.add(normalized)
        if len(result.items) == scope.question_count:
            precompleted_by_id[result.scope_id] = result.model_copy(update={"status": "final"})
        else:
            partial_by_id[result.scope_id] = result

    successful: list[ScopeResult] = [
        precompleted_by_id[scope.scope_id] for scope in scopes if scope.scope_id in precompleted_by_id
    ]
    failed: list[ScopeResult] = []
    for scope_index, scope in enumerate(scopes, start=1):
        if progress_callback:
            progress_callback({"phase": "questions", "status": "running",
                               "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                               "current_scope_kind": scope.kind, "scope_index": scope_index,
                               "completed_scopes": len(successful),
                               "succeeded_scopes": len(successful), "failed_scopes": len(failed),
                               "processed_scopes": len(successful) + len(failed),
                               "total_scopes": len(scopes), "question_count": len(all_questions),
                               "accepted_question_count": sum(len(result.items) for result in successful + failed),
                               "target_question_count": sum(item.question_count for item in scopes)})
        if scope.scope_id in precompleted_by_id:
            continue
        errors: list[str] = []
        accepted: list[GroundedQA] = []
        try:
            if scope.kind in {"group", "course"}:
                child_bank: dict[str, GroundedQA] = {}
                lesson_results = {result.scope_id: result for result in successful if result.kind == "lesson"}
                for lesson_id in scope.lesson_ids:
                    child_result = lesson_results.get(lesson_id)
                    if child_result is not None and child_result.status == "final":
                        for item in child_result.items:
                            child_bank.setdefault(_norm(item.question), item)
                all_children_final = (
                    all(lesson_id in lesson_results and lesson_results[lesson_id].status == "final"
                        for lesson_id in scope.lesson_ids)
                )
                if all_children_final and len(child_bank) >= scope.question_count:
                    inherited = list(child_bank.values())[:scope.question_count]
                    inherited_result = ScopeResult(scope_id=scope.scope_id, kind=scope.kind,
                        lesson_ids=scope.lesson_ids, status="final", items=inherited, attempts=0)
                    successful.append(inherited_result)
                    if scope_checkpoint_callback:
                        scope_checkpoint_callback(inherited_result)
                    continue
            if scope_retriever is not None:
                query = " ".join([scope.scope_title, *scope.lesson_titles]).strip()
                ranked = list(scope_retriever(query, scope.chunks, retrieval_top_k))
                eligible = {(chunk.book_id, chunk.role, chunk.chunk_id): chunk for chunk in scope.chunks}
                identities = [(chunk.book_id, chunk.role, chunk.chunk_id) for chunk in ranked]
                if (not ranked or len(identities) != len(set(identities))
                        or any(identity not in eligible for identity in identities)):
                    raise ValueError(f"retriever returned empty, duplicate, or out-of-scope sources for {scope.scope_id}")
                # Reuse only original source objects so retrieval cannot alter provenance.
                scope = scope.model_copy(update={"chunks": [eligible[key] for key in identities]})
            batches = _partition_chunks(scope.chunks, max_input_chars)
            source_lookup = {chunk.chunk_id: chunk for chunk in scope.chunks}
            seed = partial_by_id.get(scope.scope_id)
            accepted: list[GroundedQA] = list(seed.items) if seed else []
            accepted_questions: set[str] = {_norm(item.question) for item in accepted}
            completed = False
            for attempt in range(1, max_regeneration_rounds + 2):
                remaining = scope.question_count - len(accepted)
                if remaining <= 0:
                    completed = True
                    break
                allocation = _allocate_batch_counts(len(batches), remaining)
                round_added = 0
                for batch_index, batch_count in allocation:
                    source_batch = batches[batch_index]
                    try:
                        new_candidates = _generate_batch(scope, source_batch, gateway, count=batch_count,
                            prompt_version=prompt_version, max_tokens=max_tokens,
                            previous_errors=errors[-12:], excluded_questions=all_questions | accepted_questions,
                            prompt_excluded_questions=_prompt_avoid_questions(
                                scope, [*(precompleted_scopes or []), *successful, *failed],
                                accepted_questions))
                        judgments = _judge_batch(new_candidates, source_batch, scope, gateway,
                            prompt_version=prompt_version,
                            max_tokens=_judge_output_budget(len(new_candidates), max_tokens))
                    except (LLMRequestRejected, OpenRouterQuotaExceeded, FreeTokenQuotaExceeded, OpenRouterRateLimited):
                        # HTTP 400 means the request/schema is permanently invalid; fail this scope
                        # without repeating the same provider rejection across batches/regeneration rounds.
                        raise
                    except Exception as exc:  # noqa: BLE001 - provider/validation failures become persisted per-scope diagnostics
                        errors.append(f"round {attempt}, batch {batch_index}: {type(exc).__name__}: {exc}")
                        continue
                    for candidate, judgment in zip(new_candidates, judgments):
                        qnorm = _norm(candidate.question)
                        if (not judgment.answer_supported or not judgment.question_clear_and_answerable
                                or not judgment.relevant_to_scope):
                            errors.append(f"item rejected by model validation: {judgment.rationale}")
                            continue
                        if qnorm in all_questions or qnorm in accepted_questions:
                            errors.append("duplicate question across requested lesson/group/course scopes")
                            continue
                        source = source_lookup[candidate.evidence_chunk_id]
                        raw_id = f"{scope.scope_id}\0{source.chunk_id}\0{qnorm}\0{prompt_version}\0{gateway.model_id}"
                        qa_id = "qa:" + hashlib.sha256(raw_id.encode()).hexdigest()[:20]
                        accepted.append(GroundedQA(
                            qa_id=qa_id, question=candidate.question.strip(), answer=candidate.answer.strip(),
                            evidence_chunk_ids=[source.chunk_id], book_id=source.book_id, role=source.role,
                            pages=source.pages, evidence_quote=candidate.evidence_quote,
                            model_id=gateway.model_id, prompt_version=prompt_version,
                            automated_checks={"exact_chunk_id": True, "exact_source_quote": True,
                                "answer_supported": judgment.answer_supported,
                                "question_clear_and_answerable": judgment.question_clear_and_answerable,
                                "relevant_to_scope": judgment.relevant_to_scope,
                                "deduplicated": True},
                        ))
                        accepted_questions.add(qnorm)
                        round_added += 1
                    if progress_callback:
                        progress_callback({"phase": "questions", "status": "running",
                            "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                            "current_scope_kind": scope.kind, "scope_index": scope_index,
                            "completed_scopes": len(successful), "succeeded_scopes": len(successful),
                            "failed_scopes": len(failed), "processed_scopes": len(successful) + len(failed),
                            "total_scopes": len(scopes),
                            "question_count": len(all_questions | accepted_questions),
                            "accepted_question_count": sum(len(result.items) for result in successful + failed) + len(accepted),
                            "target_question_count": sum(item.question_count for item in scopes)})
                    if accepted and len(accepted) < scope.question_count and scope_checkpoint_callback:
                        scope_checkpoint_callback(ScopeResult(scope_id=scope.scope_id, kind=scope.kind,
                            lesson_ids=scope.lesson_ids, status="failed", items=list(accepted),
                            errors=list(errors), attempts=attempt))
                if len(accepted) == scope.question_count:
                    completed = True
                    all_questions.update(accepted_questions)
                    completed_result = ScopeResult(scope_id=scope.scope_id, kind=scope.kind,
                        lesson_ids=scope.lesson_ids, status="final", items=accepted, attempts=attempt)
                    successful.append(completed_result)
                    if scope_checkpoint_callback:
                        scope_checkpoint_callback(completed_result)
                    break
                errors.append(f"round {attempt}: accepted {len(accepted)}/{scope.question_count}; added {round_added}")
            if not completed:
                all_questions.update(accepted_questions)
                failed.append(ScopeResult(scope_id=scope.scope_id, kind=scope.kind,
                    lesson_ids=scope.lesson_ids, status="failed", items=accepted, errors=errors,
                    attempts=max_regeneration_rounds + 1))
        except (ScopeCheckpointError, OpenRouterQuotaExceeded, FreeTokenQuotaExceeded, OpenRouterRateLimited):
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed with a machine-readable scope failure
            failed.append(ScopeResult(scope_id=scope.scope_id, kind=scope.kind,
                lesson_ids=scope.lesson_ids, status="failed", items=accepted, errors=[f"{type(exc).__name__}: {exc}"],
                attempts=1))
        # Persist failures before the progress boundary can honor a pause request.
        # Empty accepted-item lists still carry diagnostic and retry identity.
        if failed and failed[-1].scope_id == scope.scope_id and scope_checkpoint_callback:
            scope_checkpoint_callback(failed[-1])
        if progress_callback:
            progress_callback({"phase": "questions", "status": "running",
                               "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                               "current_scope_kind": scope.kind, "scope_index": scope_index,
                               "completed_scopes": len(successful),
                               "succeeded_scopes": len(successful), "failed_scopes": len(failed),
                               "processed_scopes": len(successful) + len(failed),
                               "total_scopes": len(scopes), "question_count": len(all_questions),
                               "accepted_question_count": sum(len(result.items) for result in successful + failed),
                               "target_question_count": sum(item.question_count for item in scopes)})
    covered = sorted({lesson_id for result in successful for lesson_id in result.lesson_ids})
    all_results = successful + failed
    all_results.sort(key=lambda result: next(i for i, scope in enumerate(scopes) if scope.scope_id == result.scope_id))
    errors = [f"{result.scope_id}: {error}" for result in failed for error in result.errors]
    return CourseQAResult(status="final" if not failed and set(covered) == expected else "failed",
        expected_lesson_ids=expected_lesson_ids, covered_lesson_ids=covered, scopes=all_results,
        errors=errors, model_id=gateway.model_id, prompt_version=prompt_version,
        provider_usage=dict(getattr(gateway, "usage_totals", {})))


def persist_course_qa(result: CourseQAResult, path: Path) -> None:
    """Atomically persist the full result, including machine-readable failure details."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
