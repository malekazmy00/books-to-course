"""Generate and validate cited lesson drafts, summaries, glossary, and MCQs."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, create_model

from b2c_contracts import (
    CourseOutline,
    GlossaryTerm,
    LessonDraft,
    LessonSentence,
    LessonSummary,
    ParsedBook,
    Question,
    StructuredChunk,
    SupplementCandidate,
)
from b2c_core.provenance_checks import detect_explicit_polarity_conflicts, validate_citations
from .qa_generation import StructuredLLMGateway, _derive_scopes
from .validation import lint_bloom_distribution, lint_mcq, validate_lesson_assets


# Bumped from v2 when the garbled generation system prompt was repaired (two
# sentences had been inserted inside "Every summary, glossary item, and MCQ must
# reference sentence indices"). v3 is skipped because orchestration already uses
# "phase5-lesson-enrichment-v3" as its course-QA stage-key prompt version.
LESSON_ENRICHMENT_PROMPT_VERSION = "phase5-lesson-enrichment-v4"


class _Sentence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_en: str = Field(min_length=12, max_length=600)
    citations: list[str] = Field(min_length=1)
    kind: Literal["primary", "supplementary", "connective"]


class _SummaryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=12, max_length=400,
                      validation_alias=AliasChoices("text", "text_en"))
    sentence_indices: list[int] = Field(min_length=1)


class _GlossaryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term_en: str = Field(min_length=1, max_length=120)
    definition_en: str = Field(min_length=8, max_length=500)
    sentence_index: int = Field(ge=0)


class _MCQCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stem: str = Field(min_length=8, max_length=500)
    bloom: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    options: list[str] = Field(min_length=3, max_length=6)
    answer_index: int = Field(ge=0, le=5)
    rationale: str = Field(min_length=8, max_length=700)
    sentence_indices: list[int] = Field(min_length=1)
    option_sentence_indices: list[list[int]] | None = None


class _MCQStrictCandidate(_MCQCandidate):
    options: list[str] = Field(min_length=3, max_length=5)
    answer_index: int = Field(ge=0, le=4)
    rationale: str = Field(min_length=8, max_length=500)


class _AssetsCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentences: list[_Sentence] = Field(min_length=3, max_length=20)
    summary_points: list[_SummaryPoint] = Field(min_length=3, max_length=10)
    glossary: list[_GlossaryCandidate] = Field(max_length=30)
    questions: list[_MCQCandidate] = Field(min_length=3, max_length=20)


class _MCQProposal(BaseModel):
    """Model-suggested question; the system derives all options from cited source facts."""
    model_config = ConfigDict(extra="forbid")
    stem: str = Field(min_length=8, max_length=500)
    bloom: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    answer_text: str = Field(min_length=2, max_length=180)
    rationale: str = Field(min_length=8, max_length=500)
    sentence_indices: list[int] = Field(min_length=1)


class _AssetsProposalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentences: list[_Sentence] = Field(min_length=3, max_length=20)
    summary_points: list[_SummaryPoint] = Field(min_length=3, max_length=10)
    glossary: list[_GlossaryCandidate] = Field(max_length=30)
    questions: list[_MCQProposal] = Field(min_length=3, max_length=20)


class _QuestionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[_MCQCandidate] = Field(min_length=1, max_length=20)


class _QualityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(ge=0)
    supported_by_source: bool


class _LessonEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    support: list[_QualityItem] = Field(min_length=1, max_length=100)
    with_lesson_choices: list[int] = Field(min_length=3, max_length=20)


class _BlindSolver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: list[int] = Field(min_length=3, max_length=20)


class LessonEnrichment(BaseModel):
    lesson_id: str
    status: Literal["final"] = "final"
    draft: LessonDraft
    summary: LessonSummary
    glossary: list[GlossaryTerm]
    questions: list[Question]
    automated_evaluation: dict[str, float | int | bool]


def _exact_list_schema(base: type[BaseModel], field: str, item: type[BaseModel], count: int):
    return create_model(
        f"Exact{field.title()}",
        __base__=base,
        **{field: (list[item], Field(min_length=count, max_length=count))},
    )


def _normalized_question_stem(stem: str) -> str:
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    return " ".join(re.findall(r"\w+", normalized))


def _merge_question_top_up(
    existing: Sequence[_MCQCandidate],
    additions: Sequence[_MCQCandidate],
    *,
    expected_total: int,
    excluded_stems: Sequence[str] = (),
) -> list[_MCQCandidate]:
    """Merge one bounded top-up; reject wrong counts and duplicate stems."""
    merged = [*existing, *additions]
    if len(merged) != expected_total:
        raise ValueError(f"question top-up count mismatch: expected {expected_total}, got {len(merged)}")
    seen: set[str] = set()
    for excluded in excluded_stems:
        key = _normalized_question_stem(excluded)
        if key:
            seen.add(key)
    for item in merged:
        key = _normalized_question_stem(item.stem)
        if not key or key in seen:
            raise ValueError("question top-up contains a duplicate or empty normalized stem")
        seen.add(key)
    return merged


def _top_up_question_batch(
    candidate: _AssetsCandidate,
    gateway: StructuredLLMGateway,
    *,
    scope_id: str,
    scope_title: str,
    sources: list[dict[str, object]],
    allowed_chunk_ids: Sequence[str],
    question_count: int,
    prompt_version: str,
    max_tokens: int,
    excluded_question_stems: Sequence[str] = (),
    max_top_up_questions: int | None = None,
) -> _AssetsCandidate:
    """Request only the missing questions once, preserving all accepted initial items."""
    existing = candidate.questions
    missing = question_count - len(existing)
    if missing <= 0:
        return candidate.model_copy(update={
            "questions": _merge_question_top_up(
                existing, (), expected_total=question_count, excluded_stems=excluded_question_stems
            )
        })
    _merge_question_top_up(
        existing, (), expected_total=len(existing), excluded_stems=excluded_question_stems
    )
    if max_top_up_questions is not None and missing > max_top_up_questions:
        raise ValueError(f"question top-up deficit {missing} exceeds bounded limit {max_top_up_questions}")
    schema = _exact_list_schema(_QuestionBatch, "questions", _MCQStrictCandidate, missing)
    replacement_count = min(len(excluded_question_stems), missing)
    used_stems = [item.stem for item in existing]
    messages = [
        {"role": "system", "content": (
            "Generate only the missing lesson MCQs. Return exactly the requested count, in the requested ordinal slots. "
            "Replace the first replacement_count requested ordinals one-for-one with the excluded invalid items, in listed order; "
            "all subsequent ordinals are new additions. Never retain or repeat an excluded invalid stem. Do not rewrite, replace, "
            "renumber, or repeat any other existing question stem. New questions must be source-grounded, "
            "each fact must cite exact zero-based sentence indices from the supplied existing lesson sentences, and every "
            "question must have plausible source-supported options and a supported rationale. At least 80% of the FINAL "
            "combined batch must be Bloom apply/analyze; therefore label each new question apply or analyze and actually "
            "require transfer, comparison, or inference from cited source details. Do not use recall or merely relabel it. "
            "Prefer a changed condition, comparison of procedural steps, or combination of source facts. Avoid duplicates of "
            "the listed stems and duplicate source fact targets. Return only the missing questions."
        )},
        {"role": "user", "content": json.dumps({
            "lesson_id": scope_id, "lesson_title": scope_title,
            "requested_question_count": missing,
            "question_ordinals": list(range(len(existing) + 1, question_count + 1)),
            "existing_question_stems": used_stems,
            "invalid_question_stems_to_replace": list(excluded_question_stems),
            "replacement_count": replacement_count,
            "existing_lesson_sentences": [
                {"sentence_index": i, "text": sentence.text_en, "source_chunk_ids": sentence.citations}
                for i, sentence in enumerate(candidate.sentences)
            ],
            "allowed_citation_ids": list(allowed_chunk_ids),
            "source_chunks": sources,
        }, ensure_ascii=False)},
    ]
    additions = gateway.structured(
        messages, schema, prompt_version=prompt_version + ":top-up-1",
        temperature=0.0, max_tokens=min(max_tokens, max(1200, 700 * missing)),
    )
    merged = _merge_question_top_up(
        existing, additions.questions, expected_total=question_count,
        excluded_stems=excluded_question_stems,
    )
    return candidate.model_copy(update={"questions": merged})


def _normalize_option_label(option: str) -> str:
    """Remove model-supplied A./B./1. labels; clients render their own positions."""
    return re.sub(r"^\s*(?:[A-Ea-e]|[1-5])\s*[.)]\s*", "", option).strip()


def _resolved_sid(indices: list[int], sentence_ids: list[str], field: str) -> list[str]:
    if any(index < 0 or index >= len(sentence_ids) for index in indices):
        raise ValueError(f"{field} references a sentence outside the generated lesson")
    result = [sentence_ids[index] for index in indices]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} contains duplicate sentence references")
    return result



_CODE_CALL = re.compile(r"\b(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*\s*\([^()]*\)")
_NUMERIC_OPTION = re.compile(r"^\s*[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?\s*$")
_NUMERIC_LITERAL = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?%?")

def _numeric_value(value: str) -> Decimal | None:
    if not _NUMERIC_OPTION.fullmatch(value):
        return None
    normalized = value.strip().replace(",", "").removesuffix("%")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None

_ANSWER_STOPWORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is", "it", "of", "on", "or", "the", "to", "with", "must", "should", "does", "do", "what", "which", "that", "alternate", "correct", "fact", "source", "step", "steps"}


def _detail_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.casefold())
    result = set()
    for word in words:
        if word in _ANSWER_STOPWORDS:
            continue
        variants = {word}
        if len(word) > 4 and word.endswith("s"):
            variants.add(word[:-1])
        if len(word) > 5 and word.endswith("ing"):
            stem = word[:-3]
            variants.update((stem, stem + "e"))
            if len(stem) > 2 and stem[-1] == stem[-2]:
                variants.add(stem[:-1])
        if len(word) > 4 and word.endswith("ed"):
            stem = word[:-2]
            variants.update((stem, stem + "e"))
        result.update(variants)
    return result


def _source_detail_supported(answer: str, source: str) -> bool:
    numeric = _NUMERIC_LITERAL.findall(answer)
    source_compact = re.sub(r"\s+", "", source).casefold()
    if numeric:
        if any(re.sub(r"\s+", "", value).casefold() in source_compact for value in numeric):
            return True
    answer_tokens = _detail_tokens(answer)
    if not answer_tokens:
        return False
    source_tokens = _detail_tokens(source)
    # Two coincidental anchors must not admit an invented action, material, or qualifier.
    if not answer_tokens <= source_tokens:
        return False
    overlap = answer_tokens & source_tokens
    if len(answer_tokens) == 1:
        # Single-word named tools/materials can be source-specific facts too.
        token = next(iter(answer_tokens))
        return len(token) >= 4 and token in overlap
    return len(overlap) >= 2


def _build_source_grounded_mcqs(
    proposals: Sequence[_MCQProposal], sentences: Sequence[_Sentence], question_count: int,
) -> list[_MCQCandidate]:
    """Build options only from distinct model-proposed facts that pass exact source anchors."""
    if len(proposals) != question_count:
        raise ValueError(f"expected {question_count} MCQ proposals, received {len(proposals)}")
    facts: list[dict[str, object]] = []
    seen_facts: set[str] = set()
    for ordinal, proposal in enumerate(proposals, start=1):
        if any(index < 0 or index >= len(sentences) for index in proposal.sentence_indices):
            raise ValueError(f"question proposal {ordinal} cites an unknown lesson sentence")
        source = " ".join(sentences[index].text_en for index in proposal.sentence_indices)
        answer = " ".join(proposal.answer_text.split())
        if not _source_detail_supported(answer, source):
            raise ValueError(f"question proposal {ordinal} answer is not anchored in its cited sentence")
        key = _normalized_question_stem(answer)
        if key in seen_facts:
            raise ValueError(f"question proposal {ordinal} repeats a target fact")
        seen_facts.add(key)
        facts.append({"text": answer, "sentence_indices": proposal.sentence_indices, "tokens": _detail_tokens(answer)})

    built: list[_MCQCandidate] = []
    for ordinal, target in enumerate(facts):
        answer = str(target["text"])
        answer_words = len(answer.split())
        target_tokens = target["tokens"]
        alternatives = []
        for index, fact in enumerate(facts):
            if index == ordinal or fact["text"] == answer:
                continue
            text = str(fact["text"])
            words = len(text.split())
            if abs(words - answer_words) > max(2, round(answer_words * 0.5)):
                continue
            tokens = fact["tokens"]
            overlap = len(target_tokens & tokens) / max(1, len(target_tokens | tokens))
            # Prefer facts with shared subject vocabulary and similar answer shape.
            alternatives.append((abs(words - answer_words), -overlap,
                                 hashlib.sha256(f"{ordinal}:{text}".encode()).hexdigest(), index, fact))
        alternatives.sort(key=lambda item: (item[0], item[1], item[2]))
        option_count = min(4, len(facts))
        selected = alternatives[:option_count - 1]
        if len(selected) != option_count - 1 or option_count < 3:
            raise ValueError(f"question proposal {ordinal + 1} lacks enough source-backed distractor facts")
        option_texts = [answer, *(str(item[4]["text"]) for item in selected)]
        counts = [len(value.split()) for value in option_texts]
        median = sorted(counts)[len(counts) // 2]
        if max(counts) - min(counts) > max(2, round(median * 0.5)):
            raise ValueError(f"question proposal {ordinal + 1} has no length-balanced source-backed distractors")
        option_indices = [list(target["sentence_indices"]),
                          *(list(item[4]["sentence_indices"]) for item in selected)]
        built.append(_MCQCandidate(
            stem=proposals[ordinal].stem, bloom=proposals[ordinal].bloom,
            options=option_texts, answer_index=0, rationale=proposals[ordinal].rationale,
            sentence_indices=list(proposals[ordinal].sentence_indices),
            option_sentence_indices=option_indices,
        ))
    return built


_NUMBERED_STEP = re.compile(r"\bStep\s+(\d+)\s*\.\s*", re.IGNORECASE)
_ORDINAL_LEAK = re.compile(r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|next|previous|preceding|following)\b", re.IGNORECASE)


def _build_numbered_procedure_mcqs(
    sentences: Sequence[_Sentence], question_count: int,
) -> list[_MCQCandidate] | None:
    """Build balanced choices from explicit step labels only when source support suffices."""
    labels: dict[int, int] = {}
    facts: list[tuple[int, str, int]] = []
    current_label: int | None = None
    seen: set[str] = set()
    for sentence_index, sentence in enumerate(sentences):
        for fragment in re.split(r"(?<=[.!?])\s+", sentence.text_en):
            fragment = fragment.strip()
            if not fragment:
                continue
            matches = list(_NUMBERED_STEP.finditer(fragment))
            if matches:
                for match_index, match in enumerate(matches):
                    current_label = int(match.group(1))
                    labels.setdefault(current_label, sentence_index)
                    start = match.end()
                    end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(fragment)
                    detail = fragment[start:end].strip(" \t:;,.—-")
                    if (len(detail) >= 36 and not _ORDINAL_LEAK.search(detail)
                            and not re.search(r"\b(?:not|except)\b", detail, re.IGNORECASE)
                            and not re.search(rf"\b{current_label}\b", detail)):
                        key = _normalized_question_stem(detail)
                        if key not in seen:
                            facts.append((current_label, detail, sentence_index))
                            seen.add(key)
                continue
            if (current_label is not None and len(fragment) >= 36 and not _ORDINAL_LEAK.search(fragment)
                    and not re.search(r"\b(?:not|except)\b", fragment, re.IGNORECASE)
                    and not re.search(rf"\b{current_label}\b", fragment)):
                key = _normalized_question_stem(fragment)
                if key not in seen:
                    facts.append((current_label, fragment, sentence_index))
                    seen.add(key)

    if len(labels) < 4 or len(facts) < question_count:
        return None
    labels_sorted = sorted(labels)
    built: list[_MCQCandidate] = []
    for answer_label, detail, source_index in facts[:question_count]:
        if re.search(rf"\bStep\s+{answer_label}\b", detail, re.IGNORECASE):
            continue
        alternatives = sorted(
            (label for label in labels_sorted if label != answer_label),
            key=lambda label: (abs(label - answer_label), label),
        )[:3]
        if len(alternatives) != 3:
            return None
        stem = (
            "A learner must carry out this instruction in the source procedure: "
            f"{detail} Which numbered step should the learner follow?"
        )
        refs = list(dict.fromkeys([source_index, labels[answer_label]]))
        option_refs = [[labels[label]] for label in [answer_label, *alternatives]]
        built.append(_MCQCandidate(
            stem=stem,
            bloom="apply",
            options=[f"Step {label}" for label in [answer_label, *alternatives]],
            answer_index=0,
            rationale=sentences[source_index].text_en,
            sentence_indices=refs,
            option_sentence_indices=option_refs,
        ))
    if len(built) < question_count:
        return None
    return built[:question_count]


def _normalize_proven_one_based_references(candidate: _AssetsCandidate | _AssetsProposalCandidate) -> tuple[_AssetsCandidate | _AssetsProposalCandidate, dict[str, bool]]:
    """Shift a field only when its references prove a one-based convention (contains N, no zero)."""
    count = len(candidate.sentences)
    provenance: dict[str, bool] = {"summary": False, "glossary": False, "questions": False}
    summary_refs = [index for point in candidate.summary_points for index in point.sentence_indices]
    if summary_refs and 0 not in summary_refs and max(summary_refs) == count and min(summary_refs) >= 1:
        candidate = candidate.model_copy(update={"summary_points": [
            point.model_copy(update={"sentence_indices": [index - 1 for index in point.sentence_indices]})
            for point in candidate.summary_points
        ]})
        provenance["summary"] = True
    glossary_refs = [item.sentence_index for item in candidate.glossary]
    if glossary_refs and 0 not in glossary_refs and max(glossary_refs) == count and min(glossary_refs) >= 1:
        candidate = candidate.model_copy(update={"glossary": [
            item.model_copy(update={"sentence_index": item.sentence_index - 1}) for item in candidate.glossary
        ]})
        provenance["glossary"] = True
    question_refs = [index for item in candidate.questions for index in item.sentence_indices]
    if question_refs and 0 not in question_refs and max(question_refs) == count and min(question_refs) >= 1:
        candidate = candidate.model_copy(update={"questions": [
            item.model_copy(update={"sentence_indices": [index - 1 for index in item.sentence_indices]})
            for item in candidate.questions
        ]})
        provenance["questions"] = True
    return candidate, provenance

def _validate_mcq_evidence_consistency(
    draft: LessonDraft, questions: Sequence[Question], source_chunks: Sequence[StructuredChunk] = (),
) -> list[dict[str, bool]]:
    """Fail closed on mismatched numeric keys, omitted code premises, and ungrounded MCQ answers."""
    sentences = {sentence.sid: sentence for sentence in draft.sentences}
    chunks = {chunk.chunk_id: chunk for chunk in source_chunks}
    checks: list[dict[str, bool]] = []
    for question in questions:
        cited_sentences = [sentences[sid] for sid in question.evidence_sids if sid in sentences]
        if len(cited_sentences) != len(question.evidence_sids):
            raise ValueError(f"{question.qid}: question references an unknown evidence sentence")
        cited_text = " ".join(sentence.text_en for sentence in cited_sentences)
        cited_chunks = {chunk_id for sentence in cited_sentences for chunk_id in sentence.citations}
        source_text = " ".join([cited_text, *(chunks[cid].text for cid in cited_chunks if cid in chunks)])
        evidence_compact = re.sub(r"\s+", "", cited_text).casefold()
        for call in _CODE_CALL.findall(question.stem):
            if re.sub(r"\s+", "", call).casefold() not in evidence_compact:
                raise ValueError(
                    f"{question.qid}: code premise {call!r} is absent from its cited lesson sentence"
                )
        if not question.options or question.answer_index is None:
            checks.append({"source_detail_anchor_pass": True, "distractors_anchored_pass": True})
            continue
        if question.option_evidence_sids is not None:
            if len(question.option_evidence_sids) != len(question.options):
                raise ValueError(f"{question.qid}: per-option evidence count does not match its options")
            for option, option_sids in zip(question.options, question.option_evidence_sids, strict=True):
                if not option_sids or any(sid not in sentences for sid in option_sids):
                    raise ValueError(f"{question.qid}: option has missing or unknown source evidence")
                option_sentences = [sentences[sid] for sid in option_sids]
                option_chunk_ids = {cid for sentence in option_sentences for cid in sentence.citations}
                option_source = " ".join([*(sentence.text_en for sentence in option_sentences),
                                          *(chunks[cid].text for cid in option_chunk_ids if cid in chunks)])
                if not _source_detail_supported(option, option_source):
                    raise ValueError(f"{question.qid}: option is not supported by its own cited source evidence")
        answer_text = question.options[question.answer_index]
        answer_tokens = _detail_tokens(answer_text)
        stem_tokens = _detail_tokens(question.stem)
        if answer_tokens and len(answer_tokens & stem_tokens) / len(answer_tokens) >= 0.80:
            raise ValueError(f"{question.qid}: stem exposes answer through the keyed option")
        if not _source_detail_supported(answer_text, source_text):
            raise ValueError(f"{question.qid}: correct answer lacks a source-specific detail anchor")
        answer = _numeric_value(answer_text)
        if answer is not None:
            arithmetic_stem = bool(_CODE_CALL.search(question.stem)) or bool(re.search(
                r"\b(?:calculate|result|value|probability|total|fraction|percentage|mean)\b",
                question.stem, flags=re.IGNORECASE,
            ))
            if arithmetic_stem:
                rationale_numbers = _NUMERIC_LITERAL.findall(question.rationale)
                rationale_value = _numeric_value(rationale_numbers[-1]) if rationale_numbers else None
                if rationale_value is None or rationale_value != answer:
                    raise ValueError(
                        f"{question.qid}: selected numeric answer disagrees with the final numeric value in its rationale"
                    )
        distractors = [option for index, option in enumerate(question.options) if index != question.answer_index]
        # Distractors may be grounded in a different approved source chunk than the
        # answer. The answer remains tied to the question's cited chunks above; only
        # distractor plausibility is checked against the complete approved lesson scope.
        distractor_source_text = " ".join(chunk.text for chunk in source_chunks) or source_text
        if not all(_source_detail_supported(option, distractor_source_text) for option in distractors):
            unsupported = [option for option in distractors
                           if not _source_detail_supported(option, distractor_source_text)]
            raise ValueError(f"{question.qid}: distractors lack a source-grounded detail: {unsupported!r}")
        checks.append({"source_detail_anchor_pass": True, "distractors_anchored_pass": True})
    return checks


def _validate_mcq_set_balance(questions: Sequence[Question]) -> list[dict[str, bool]]:
    """Reject answer-length clues and skewed answer positions on meaningful quiz sets."""
    length_checks: list[bool] = []
    for question in questions:
        if not question.options or question.answer_index is None or len(question.options) < 3:
            length_checks.append(True)
            continue
        normalized_lengths = [len(" ".join(option.split()).strip(" .!?;:")) for option in question.options]
        # Keep choices within a tight length band so answer length cannot cue the key.
        length_checks.append(max(normalized_lengths) - min(normalized_lengths) <= 8)
    option_counts = {len(question.options or []) for question in questions}
    position_pass = True
    if len(questions) >= 8 and len(option_counts) == 1:
        option_count = next(iter(option_counts))
        if option_count >= 3:
            counts = [sum(question.answer_index == index for question in questions)
                      for index in range(option_count)]
            position_pass = max(counts) - min(counts) <= 2
    if not all(length_checks):
        offenders = [question.qid for question, passed in zip(questions, length_checks, strict=True) if not passed]
        raise ValueError(f"MCQs expose the answer through option length: {offenders}")
    if not position_pass:
        raise ValueError("MCQ correct-answer positions are imbalanced")
    return [{"option_length_balance_pass": True, "answer_position_balance_pass": position_pass}
            for _ in questions]

def _make_lesson_assets(scope_id: str, candidate: _AssetsCandidate,
                        source_chunks: Sequence[StructuredChunk],
                        *, prompt_version: str,
                        contradictions: list[SupplementCandidate] | None = None) -> LessonEnrichment:
    source_ids = {chunk.chunk_id: chunk for chunk in source_chunks}
    normalized_sentences = []
    for sentence in candidate.sentences:
        if any(chunk_id not in source_ids for chunk_id in sentence.citations):
            raise ValueError("lesson sentence cites a source outside its approved lesson scope")
        cited_roles = {source_ids[c].role for c in sentence.citations}
        # Lane is deterministically implied when every exact cited source has one role.
        # Correct only the two unambiguous mismatches; mixed-role content remains fail-closed.
        if cited_roles == {"spine"} and sentence.kind == "supplementary":
            sentence = sentence.model_copy(update={"kind": "primary"})
        elif cited_roles == {"supplementary"} and sentence.kind == "primary":
            sentence = sentence.model_copy(update={"kind": "supplementary"})
        if sentence.kind == "primary" and any(source_ids[c].role != "spine" for c in sentence.citations):
            raise ValueError("primary lesson sentence cites a supplementary source")
        if sentence.kind == "supplementary" and any(source_ids[c].role != "supplementary" for c in sentence.citations):
            raise ValueError("supplementary lesson sentence cites a spine source")
        normalized_sentences.append(sentence)
    candidate = candidate.model_copy(update={"sentences": normalized_sentences})

    sentence_ids = [f"{scope_id}:s{index:03d}" for index in range(1, len(candidate.sentences) + 1)]
    draft = LessonDraft(
        lesson_id=scope_id,
        version=1,
        sentences=[LessonSentence(sid=sid, text_en=item.text_en, citations=item.citations, kind=item.kind)
                   for sid, item in zip(sentence_ids, candidate.sentences, strict=True)],
        contradictions=contradictions or [],
        faithfulness={},
    )
    summary = LessonSummary(
        lesson_id=scope_id,
        points=[point.text for point in candidate.summary_points],
        point_sids=[_resolved_sid(point.sentence_indices, sentence_ids, "summary")
                    for point in candidate.summary_points],
    )
    glossary = [GlossaryTerm(term_en=item.term_en, definition_en=item.definition_en,
                             first_sid=sentence_ids[item.sentence_index], lesson_id=scope_id)
                if item.sentence_index < len(sentence_ids) else
                (_ for _ in ()).throw(ValueError("glossary references an unknown sentence"))
                for item in candidate.glossary]
    questions: list[Question] = []
    answer_position_counts: dict[int, int] = {}
    overflow_trimmed: set[str] = set()
    for index, item in enumerate(candidate.questions, start=1):
        if item.answer_index >= len(item.options):
            raise ValueError("MCQ answer index is outside its options")
        if len(item.options) > 5:
            if len(item.options) != 6:
                raise ValueError("MCQ option overflow exceeds the bounded six-option adapter")
            distractor_indices = [i for i in range(len(item.options)) if i != item.answer_index]
            approved_text = " ".join(chunk.text for chunk in source_chunks)
            unsupported = [i for i in distractor_indices
                           if not _source_detail_supported(item.options[i], approved_text)]
            drop_index = unsupported[0] if unsupported else distractor_indices[-1]
            options = [value for i, value in enumerate(item.options) if i != drop_index]
            answer_index = item.answer_index - int(drop_index < item.answer_index)
            item = item.model_copy(update={"options": options, "answer_index": answer_index})
            overflow_trimmed.add(f"{scope_id}:q{index:03d}")
        # Seeded, count-balanced option shuffling avoids both position patterns and
        # accidental imbalance in small quiz batches.
        qid = f"{scope_id}:q{index:03d}"
        normalized_options = [_normalize_option_label(option) for option in item.options]
        answer_index = min(range(len(normalized_options)), key=lambda position: (
            answer_position_counts.get(position, 0),
            hashlib.sha256(f"{qid}:answer-position:{position}".encode("utf-8")).digest(),
        ))
        if item.option_sentence_indices is not None and len(item.option_sentence_indices) != len(item.options):
            raise ValueError(f"{qid}: option source evidence count does not match option count")
        option_refs = item.option_sentence_indices or [item.sentence_indices for _ in item.options]
        distractors = [(original, option, option_refs[original])
                       for original, option in enumerate(normalized_options) if original != item.answer_index]
        distractors.sort(key=lambda pair: hashlib.sha256(
            f"{qid}:distractor-order:{pair[1]}".encode("utf-8")
        ).digest())
        options = [""] * len(normalized_options)
        options[answer_index] = normalized_options[item.answer_index]
        remaining_positions = [position for position in range(len(options)) if position != answer_index]
        shuffled_refs = [[] for _ in options]
        shuffled_refs[answer_index] = option_refs[item.answer_index]
        for position, (_, option, refs) in zip(remaining_positions, distractors, strict=True):
            options[position] = option
            shuffled_refs[position] = refs
        answer_position_counts[answer_index] = answer_position_counts.get(answer_index, 0) + 1
        questions.append(Question(
            qid=qid, lesson_id=scope_id,
            purpose="end_of_lesson", type="mcq", bloom=item.bloom,
            stem=item.stem, options=options, answer_index=answer_index,
            rationale=item.rationale,
            evidence_sids=_resolved_sid(item.sentence_indices, sentence_ids, "question"),
            option_evidence_sids=[_resolved_sid(refs, sentence_ids, "option") for refs in shuffled_refs]
                if item.option_sentence_indices is not None else None,
            validation={"extra_distractor_removed": True} if qid in overflow_trimmed else {},
        ))

    answer_quality_checks = _validate_mcq_evidence_consistency(draft, questions, source_chunks)
    balance_checks = _validate_mcq_set_balance(questions)
    questions = [question.model_copy(update={"validation": {**question.validation, **evidence, **balance}})
                 for question, evidence, balance in zip(questions, answer_quality_checks,
                                                        balance_checks, strict=True)]
    citation_report = validate_citations(draft, source_chunks)
    asset_report = validate_lesson_assets(draft, summary, glossary, questions)
    lint_findings = [finding for question in questions for finding in lint_mcq(question).findings]
    bloom_findings = list(lint_bloom_distribution(questions).findings)
    findings = [*citation_report.findings, *asset_report.findings, *lint_findings, *bloom_findings]
    if findings:
        codes = sorted({finding.code for finding in findings})
        raise ValueError("lesson asset validation failed: " + ",".join(codes))
    return LessonEnrichment(lesson_id=scope_id, draft=draft, summary=summary,
                            glossary=glossary, questions=questions, automated_evaluation={})


def generate_lesson_enrichments(
    outline: CourseOutline,
    spine_book: ParsedBook,
    chunks: list[StructuredChunk],
    gateway: StructuredLLMGateway,
    *,
    question_count: int = 5,
    max_tokens: int = 4096,
    prompt_version: str = LESSON_ENRICHMENT_PROMPT_VERSION,
    lesson_ids: list[str] | None = None,
    supplementary_chunks_by_lesson: dict[str, list[StructuredChunk]] | None = None,
    excluded_question_stems: list[str] | None = None,
    question_repair_limit: int = 3,
    max_question_top_up: int | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    precompleted: Sequence[LessonEnrichment] = (),
    checkpoint_callback: Callable[[LessonEnrichment], None] | None = None,
) -> list[LessonEnrichment]:
    """Generate source-linked L5/L6 assets for every accepted lesson, fail closed."""
    scopes, _ = _derive_scopes(outline, spine_book, chunks, lesson_question_count=question_count,
                               group_question_count=1, course_question_count=1,
                               outline_approved=True)
    if lesson_ids is not None:
        requested = set(lesson_ids)
        scopes = [scope for scope in scopes if scope.kind == "lesson" and scope.scope_id in requested]
        if {scope.scope_id for scope in scopes} != requested:
            raise ValueError("lesson enrichment request contains an unknown approved lesson")
    supplementary_chunks_by_lesson = supplementary_chunks_by_lesson or {}
    scope_ids = {item.scope_id for item in scopes if item.kind == "lesson"}
    if set(supplementary_chunks_by_lesson) - scope_ids:
        raise ValueError("supplementary contradiction candidates include an unknown lesson")
    approved_supplementary = set(outline.supplementary_book_ids)
    for candidates in supplementary_chunks_by_lesson.values():
        if any(chunk.role != "supplementary" or chunk.book_id not in approved_supplementary
               for chunk in candidates):
            raise ValueError("contradiction check includes an unapproved supplementary source")
    lesson_scopes = [item for item in scopes if item.kind == "lesson"]
    scope_by_id = {item.scope_id: item for item in lesson_scopes}
    precompleted_by_id: dict[str, LessonEnrichment] = {}
    for saved in precompleted:
        scope = scope_by_id.get(saved.lesson_id)
        if scope is None or saved.lesson_id in precompleted_by_id:
            raise ValueError("precompleted enrichment is duplicate or outside the requested lessons")
        citations = validate_citations(saved.draft, scope.chunks)
        assets = validate_lesson_assets(saved.draft, saved.summary, saved.glossary, saved.questions)
        lint_findings = [finding for question in saved.questions for finding in lint_mcq(question).findings]
        bloom_findings = list(lint_bloom_distribution(saved.questions).findings) if saved.questions else []
        if citations.findings or assets.findings or lint_findings or bloom_findings:
            raise ValueError(f"precompleted enrichment failed revalidation: {saved.lesson_id}")
        precompleted_by_id[saved.lesson_id] = saved

    result: list[LessonEnrichment] = []
    for scope_index, scope in enumerate(lesson_scopes, start=1):
        if scope.scope_id in precompleted_by_id:
            result.append(precompleted_by_id[scope.scope_id])
            if progress_callback:
                progress_callback({"phase": "lesson_assets", "status": "running",
                                   "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                                   "current_scope_kind": "lesson", "scope_index": scope_index,
                                   "completed_scopes": len(result), "total_scopes": len(lesson_scopes),
                                   "lesson_asset_count": len(result), "resumed": True})
            continue
        if progress_callback:
            progress_callback({"phase": "lesson_assets", "status": "running",
                               "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                               "current_scope_kind": "lesson", "scope_index": scope_index,
                               "completed_scopes": len(result), "total_scopes": len(lesson_scopes),
                               "lesson_asset_count": len(result)})
        contradiction_candidates = detect_explicit_polarity_conflicts(
            scope.chunks, supplementary_chunks_by_lesson.get(scope.scope_id, []),
            lesson_id=scope.scope_id,
        )
        sources = [{"chunk_id": chunk.chunk_id, "book_id": chunk.book_id, "role": chunk.role,
                    "pages": chunk.pages, "text": chunk.text} for chunk in scope.chunks]
        allowed_chunk_ids = tuple(dict.fromkeys(chunk.chunk_id for chunk in scope.chunks))
        if not allowed_chunk_ids:
            raise ValueError(f"{scope.scope_id}: no approved source chunks for lesson enrichment")
        citation_id = Literal.__getitem__(allowed_chunk_ids)
        scoped_sentence = create_model(
            "ScopedLessonSentence",
            __base__=_Sentence,
            citations=(list[citation_id], Field(min_length=1)),
        )
        scoped_assets = create_model(
            "ScopedLessonAssets",
            __base__=_AssetsProposalCandidate,
            sentences=(list[scoped_sentence], Field(min_length=3, max_length=20)),
        )
        bounded_sentence_index = Literal.__getitem__(tuple(range(0, 20)))
        scoped_proposal = create_model(
            "ScopedMCQProposal", __base__=_MCQProposal,
            sentence_indices=(list[bounded_sentence_index], Field(min_length=1)),
        )
        candidate_schema = create_model(
            "ExactQuestions", __base__=scoped_assets,
            questions=(list[scoped_proposal], Field(min_length=question_count, max_length=question_count)),
        )
        generation_messages = [
            {"role": "system", "content": (
                "Create a concise English lesson grounded only in the supplied chunks. Return 4-8 factual lesson sentences, "
                "at least three concise summary points, only glossary terms explicitly supported by those sentences, "
                "and exactly the requested number of MCQs. Every sentence must cite exact supplied chunk IDs and use primary "
                "for spine evidence, supplementary for supplementary evidence, connective only for transitions. "
                "Use primary whenever every cited source ID belongs to the spine; use supplementary whenever every cited source ID belongs to a supplementary book. "
                "Do not label spine-cited sentences as supplementary or supplementary-cited sentences as primary. "
                "Every summary, glossary item, and MCQ must reference sentence indices. These indices are zero-based positions in the returned "
                "sentences array, never page, paragraph, or source-chunk numbers. If there are N returned sentences, every index "
                "must be an integer from 0 through N-1; for example, with four sentences only 0, 1, 2, and 3 are valid. "
                "Before returning, check that all summary, glossary, and question references point to a sentence that exists. "
                "Keep the sentence list compact and make each sentence carry one independently citable source fact. For each MCQ, return only a stem, Bloom level, exact answer_text copied from one cited lesson sentence, a concise rationale, and zero-based sentence_indices. "
                "Do not return options or an answer index: the application builds every option from separately source-validated answer facts. At least 80% MUST be labeled apply or analyze, and each stem must require transfer or reasoning, not recall with a higher label. Use source-specific formats: apply a stated rule to a changed condition, compare two cited procedure steps to select the correct next action, or combine two cited details to infer the outcome. Every fact needed to answer must be cited by the exact sentence indices; rationale must show the application/comparison. Do not ask definition, name, or direct fact-recall questions and do not relabel them apply/analyze. The stem must not repeat or reveal answer_text. "
                "No unsupported facts, answer-leaking stems, or duplicate questions/target facts. "
                "Prefer a distinct, non-obvious source-specific fact: an exact name, number, sequence, material, condition, exception, or qualification. "
                "Ask a transfer/comparison question that makes the association between the fact and its stated step/condition necessary. "
                "Copy answer_text exactly from the cited lesson sentence; keep it concise and do not include extra detail not present there. "
                "The application will create distractors from other answer facts in this same generated lesson, so choose a target fact "
                "whose answer has at least three distinct, similarly shaped source facts available elsewhere in the lesson. "
                "Do not return any distractor, option list, or answer index. Do not invent answer text. "
                "The cited lesson sentence must preserve every "
                "condition needed to answer the question. Avoid calculation questions; if a numeric result is not explicitly "
                "stated with the exact operation and premises in the cited sentence, do not ask it. "
                "Each correct answer must be directly supported by one cited lesson sentence; do not require outside "
                "knowledge or a generic textbook formula. Copy an exact source phrase or an exact source value/name into "
                "the correct option, and verify its meaningful tokens occur in that cited sentence before returning. "
                "If a question cannot pass that check, replace it with a different source-specific detail question. "
                "Return exactly requested_question_count questions; count them before returning and never stop early. "
                "Build distractors from nearby but "
                "different claims in these chunks, not obviously absurd alternatives. Keep every option within 2 words of the other options and in comparable grammatical form; distribute correct positions across the quiz instead of repeating a pattern. Avoid stems that repeat the "
                "correct option or reveal it by phrasing."
            )}, {"role": "user", "content": json.dumps({
                "lesson_id": scope.scope_id, "lesson_title": scope.scope_title,
                "requested_question_count": question_count, "question_count": question_count,
                "allowed_citation_ids": list(allowed_chunk_ids),
                "source_chunks": sources,
            }, ensure_ascii=False)}]
        proposal_candidate = gateway.structured(
            generation_messages, candidate_schema,
            prompt_version=prompt_version + ":generate:attempt-1",
            temperature=0.0, max_tokens=max_tokens,
        )
        proposal_candidate, reference_normalization = _normalize_proven_one_based_references(proposal_candidate)
        proposals = list(proposal_candidate.questions)
        assets = None
        repair_limit = min(max(0, question_repair_limit), question_count)
        for repair_attempt in range(repair_limit + 1):
            try:
                built_questions = _build_numbered_procedure_mcqs(
                    proposal_candidate.sentences, question_count
                ) or _build_source_grounded_mcqs(
                    proposals, proposal_candidate.sentences, question_count
                )
                candidate = _AssetsCandidate(
                    sentences=proposal_candidate.sentences,
                    summary_points=proposal_candidate.summary_points,
                    glossary=proposal_candidate.glossary,
                    questions=built_questions,
                )
                assets = _make_lesson_assets(
                    scope.scope_id, candidate, scope.chunks, prompt_version=prompt_version,
                    contradictions=contradiction_candidates,
                )
                break
            except ValueError as exc:
                if repair_attempt >= repair_limit:
                    raise
                proposal_match = re.search(r"question proposal (\d+)", str(exc))
                question_match = re.search(re.escape(scope.scope_id) + r":q(\d{3})", str(exc))
                if proposal_match:
                    question_index = int(proposal_match.group(1)) - 1
                elif question_match:
                    question_index = int(question_match.group(1)) - 1
                else:
                    raise
                if question_index < 0 or question_index >= len(proposals):
                    raise
                bounded_sentence_index = Literal.__getitem__(tuple(range(len(proposal_candidate.sentences))))
                repair_schema = create_model(
                    "RepairLessonQuestion", __base__=_MCQProposal,
                    sentence_indices=(list[bounded_sentence_index], Field(min_length=1)),
                )
                failed_proposal = proposals[question_index]
                repair_messages = [
                    {"role": "system", "content": (
                        "Return exactly one replacement question proposal: stem, Bloom level, exact answer_text, rationale, and zero-based sentence_indices. "
                        "Do not return options or distractors; those are built by the system from cited source facts. "
                        "The prior proposal failed this validation: " + str(exc) + ". Preserve apply/analyze task requirements and never reveal answer_text in the stem."
                    )},
                    {"role": "user", "content": json.dumps({
                        "lesson_id": scope.scope_id, "lesson_title": scope.scope_title,
                        "source_chunks": sources,
                        "lesson_sentences": [{"index": i, "text": sentence.text_en}
                                             for i, sentence in enumerate(proposal_candidate.sentences)],
                        "rejected_proposal": failed_proposal.model_dump(mode="json"),
                        "replace_only_question_number": question_index + 1,
                        "other_target_facts_to_avoid": [p.answer_text for i, p in enumerate(proposals) if i != question_index],
                    }, ensure_ascii=False)},
                ]
                proposals[question_index] = gateway.structured(
                    repair_messages, repair_schema,
                    prompt_version=prompt_version + f":repair-question:{question_index + 1}:{repair_attempt + 1}",
                    temperature=0.0, max_tokens=min(max_tokens, 768),
                )
        if assets is None:
            raise ValueError(f"{scope.scope_id}: bounded question repair did not produce valid lesson assets")

        evidence_items: list[dict[str, object]] = []
        for sentence in assets.draft.sentences:
            evidence_items.append({"kind": "sentence", "text": sentence.text_en,
                                  "citations": sentence.citations})
        for index, point in enumerate(assets.summary.points):
            evidence_items.append({"kind": "summary", "text": point,
                                  "sentence_ids": assets.summary.point_sids[index]})
        for term in assets.glossary:
            evidence_items.append({"kind": "glossary", "text": term.definition_en,
                                  "sentence_id": term.first_sid})
        for question in assets.questions:
            evidence_items.append({"kind": "mcq", "stem": question.stem,
                                  "answer": question.options[question.answer_index],
                                  "rationale": question.rationale,
                                  "evidence_sids": question.evidence_sids})
        question_count_for_eval = len(assets.questions)
        evaluation_schema = create_model(
            "ExactLessonEvaluation", __base__=_LessonEvaluation,
            support=(list[_QualityItem], Field(min_length=len(evidence_items), max_length=len(evidence_items))),
            with_lesson_choices=(list[int], Field(min_length=question_count_for_eval, max_length=question_count_for_eval)),
        )
        evaluation = gateway.structured(
            [{"role": "system", "content": (
                "Evaluate each lesson asset against its cited source evidence. Mark supported_by_source true only if the "
                "content is entailed by the referenced source sentences/chunks. Then solve every MCQ using only "
                "the complete lesson/source context. Return zero-based option indices in the same question order. "
                "The MCQ payload contains no answer index. Return exactly one support record for each indexed asset "
                "in source-support order, then one choice per MCQ in with-context order."
            )}, {"role": "user", "content": json.dumps({
                "lesson_id": scope.scope_id, "assets": evidence_items,
                "source_chunks": sources,
                "mcqs": [{"stem": q.stem, "options": q.options} for q in assets.questions],
            }, ensure_ascii=False)}],
            evaluation_schema, prompt_version=prompt_version + ":evaluate-and-solve",
            temperature=0.0, max_tokens=max_tokens,
        )
        indexed_support = {item.index: item.supported_by_source for item in evaluation.support}
        if set(indexed_support) != set(range(len(evidence_items))):
            raise ValueError(f"{scope.scope_id}: evaluator omitted or duplicated asset indices")
        support_rate = sum(indexed_support.values()) / len(indexed_support)
        if support_rate < 0.90:
            raise ValueError(f"{scope.scope_id}: automated source support {support_rate:.3f} below 0.90")
        if len(evaluation.with_lesson_choices) != question_count_for_eval:
            raise ValueError(f"{scope.scope_id}: with-lesson solver response count mismatch")
        if any(choice < 0 or choice >= len(question.options or [])
               for choice, question in zip(evaluation.with_lesson_choices, assets.questions, strict=True)):
            raise ValueError(f"{scope.scope_id}: with-lesson solver returned an invalid option index")
        with_accuracy = sum(actual == question.answer_index for actual, question in
                            zip(evaluation.with_lesson_choices, assets.questions, strict=True)) / question_count_for_eval
        blind_schema = create_model(
            "ExactBlindSolver", __base__=_BlindSolver,
            choices=(list[int], Field(min_length=question_count_for_eval, max_length=question_count_for_eval)),
        )
        blind = gateway.structured(
            [{"role": "system", "content": (
                "Answer each multiple-choice question from the question and options alone. You have no lesson, "
                "source, or evidence. If you do not know, choose uniformly among options. Return only the "
                "zero-based option index for each item in order."
            )}, {"role": "user", "content": json.dumps({
                "mcqs": [{"stem": q.stem, "options": q.options} for q in assets.questions],
            }, ensure_ascii=False)}],
            blind_schema, prompt_version=prompt_version + ":blind-solver", temperature=0.0,
            max_tokens=max_tokens,
        )
        if any(choice < 0 or choice >= len(question.options or [])
               for choice, question in zip(blind.choices, assets.questions, strict=True)):
            raise ValueError(f"{scope.scope_id}: blind solver returned an invalid option index")
        without_accuracy = sum(actual == question.answer_index for actual, question in
                               zip(blind.choices, assets.questions, strict=True)) / question_count_for_eval
        if with_accuracy < 0.95 or without_accuracy > 0.30:
            raise ValueError(f"{scope.scope_id}: solver gate failed (with={with_accuracy:.3f}, without={without_accuracy:.3f})")
        assets = assets.model_copy(update={"automated_evaluation": {
            "source_support_rate": support_rate,
            "with_lesson_solver_accuracy": with_accuracy,
            "without_lesson_solver_accuracy": without_accuracy,
            "summary_refs_one_based_normalized": reference_normalization["summary"],
            "glossary_refs_one_based_normalized": reference_normalization["glossary"],
            "question_refs_one_based_normalized": reference_normalization["questions"],
            "citation_resolution_pass": True,
            "mcq_linter_pass": True,
        }})
        assets = assets.model_copy(update={"draft": assets.draft.model_copy(update={
            "faithfulness": {"automated_support_judge": support_rate,
                             "citation_support_rate": 1.0},
        })})
        result.append(assets)
        if checkpoint_callback:
            checkpoint_callback(assets)
        if progress_callback:
            progress_callback({"phase": "lesson_assets", "status": "running",
                               "current_scope_id": scope.scope_id, "current_scope_title": scope.scope_title,
                               "current_scope_kind": "lesson", "scope_index": scope_index,
                               "completed_scopes": len(result), "total_scopes": len(lesson_scopes),
                               "lesson_asset_count": len(result)})
    return result
