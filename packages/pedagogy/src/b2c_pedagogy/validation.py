"""Fail-closed structural validators and a deliberately narrow MCQ linter."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from b2c_contracts import GlossaryTerm, LessonDraft, LessonSummary, Question

@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    location: str


@dataclass(frozen=True)
class ValidationReport:
    findings: tuple[Finding, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.findings

_BLOOM_ORDER = {name: index for index, name in enumerate(
    ("remember", "understand", "apply", "analyze", "evaluate", "create")
)}
_ALL_NONE_ABOVE = re.compile(
    r"\b(?:all|none)\s+of\s+(?:the\s+)?(?:above|these|the\s+following)\b|"
    r"\b(?:all|none)\s+(?:apply|are\s+correct)\b",
    re.IGNORECASE,
)
_COMBINATION_OPTION = re.compile(
    r"\b(?:both\s+.+\s+and\s+.+|[a-d]\s+and\s+[a-d])\b", re.IGNORECASE
)
_NEGATIVE_STEM = re.compile(r"\b(?:not|except)\b", re.IGNORECASE)


def _normalize_option(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split()).strip(" .!?;:")


def lint_mcq(question: Question) -> ValidationReport:
    """Check deterministic MCQ construction defects; semantic quality needs separate evaluation."""
    if question.type != "mcq":
        return ValidationReport()

    findings: list[Finding] = []
    options = question.options or []
    normalized = [_normalize_option(option) for option in options]
    duplicates = sorted(option for option, count in Counter(normalized).items() if count > 1)
    for duplicate in duplicates:
        findings.append(
            Finding("mcq.duplicate_option", f"Repeated answer option: {duplicate}", question.qid)
        )
    if any(_ALL_NONE_ABOVE.search(option) for option in options):
        findings.append(
            Finding(
                "mcq.all_none_of_above",
                "Avoid all/none combination options.",
                question.qid,
            )
        )
    if any(_COMBINATION_OPTION.search(option) for option in options):
        findings.append(
            Finding("mcq.combination_option", "Avoid combination answer options", question.qid)
        )
    if _NEGATIVE_STEM.search(question.stem):
        findings.append(
            Finding("mcq.negative_stem", "Avoid negatively worded MCQ stems", question.qid)
        )

    lengths = [len(" ".join(option.split()).strip(" .!?;:")) for option in options if option.strip()]
    if lengths and max(lengths) - min(lengths) > 8:
        findings.append(
            Finding("mcq.option_length_cue", "Option lengths differ by more than eight characters", question.qid)
        )
    return ValidationReport(tuple(findings))


def lint_bloom_distribution(questions: list[Question], *, minimum_ratio: float = 0.80) -> ValidationReport:
    """Require enough apply/analyze items in a lesson batch for transfer practice."""
    if not questions:
        return ValidationReport((Finding("question.empty_batch", "Question batch is empty", "questions"),))
    eligible = sum(question.bloom in {"apply", "analyze"} for question in questions)
    required = (len(questions) * int(minimum_ratio * 100) + 99) // 100
    if eligible < required:
        return ValidationReport((Finding(
            "question.bloom_distribution",
            f"Need at least {required}/{len(questions)} apply/analyze questions; found {eligible}",
            "questions",
        ),))
    return ValidationReport()


def validate_lesson_assets(
    draft: LessonDraft,
    summary: LessonSummary,
    glossary: list[GlossaryTerm],
    questions: list[Question],
) -> ValidationReport:
    """Check lesson IDs, source sentence references, coverage, and basic MCQ defects."""
    findings: list[Finding] = []
    lesson_id = draft.lesson_id
    sentence_ids = {sentence.sid for sentence in draft.sentences}

    if summary.lesson_id != lesson_id:
        findings.append(Finding("summary.lesson_mismatch", "Summary belongs to another lesson", lesson_id))
    if len(summary.points) != len(summary.point_sids):
        findings.append(
            Finding("summary.evidence_count", "Each summary point needs its own evidence SID list", lesson_id)
        )
    for index, source_ids in enumerate(summary.point_sids):
        if not source_ids:
            findings.append(Finding("summary.missing_evidence", "Summary point has no evidence", f"point[{index}]"))
        for sid in source_ids:
            if sid not in sentence_ids:
                findings.append(Finding("summary.unknown_sid", f"Unknown sentence ID: {sid}", f"point[{index}]"))

    for term in glossary:
        if term.lesson_id != lesson_id:
            findings.append(Finding("glossary.lesson_mismatch", "Glossary term belongs to another lesson", term.term_en))
        if term.first_sid not in sentence_ids:
            findings.append(Finding("glossary.unknown_sid", f"Unknown first_sid: {term.first_sid}", term.term_en))

    valid_questions: list[Question] = []
    for question in questions:
        if question.lesson_id != lesson_id:
            findings.append(Finding("question.lesson_mismatch", "Question belongs to another lesson", question.qid))
        for sid in question.evidence_sids:
            if sid not in sentence_ids:
                findings.append(Finding("question.unknown_sid", f"Unknown evidence SID: {sid}", question.qid))
        if question.option_evidence_sids is not None:
            if len(question.option_evidence_sids) != len(question.options or []):
                findings.append(Finding("question.option_evidence_count", "Each option needs aligned source evidence", question.qid))
            for option_index, source_ids in enumerate(question.option_evidence_sids):
                if not source_ids:
                    findings.append(Finding("question.option_evidence_missing", "Option has no source evidence", f"{question.qid}:option[{option_index}]"))
                for sid in source_ids:
                    if sid not in sentence_ids:
                        findings.append(Finding("question.option_unknown_sid", f"Unknown option evidence SID: {sid}", f"{question.qid}:option[{option_index}]"))
        findings.extend(lint_mcq(question).findings)
        if question.lesson_id == lesson_id and all(sid in sentence_ids for sid in question.evidence_sids):
            valid_questions.append(question)

    if not any(question.bloom != "remember" for question in valid_questions):
        findings.append(
            Finding("question.no_higher_bloom", "Lesson needs at least one supported question above remember", lesson_id)
        )
    return ValidationReport(tuple(findings))
