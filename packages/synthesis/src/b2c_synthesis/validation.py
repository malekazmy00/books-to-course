"""Deterministic structural/provenance checks for synthesized lessons.

These checks verify that citations resolve and respect the declared source role.
They do not claim semantic entailment; faithfulness still needs a separately
calibrated evaluator and human-reviewed evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from b2c_contracts import LessonDraft, StructuredChunk, SupplementCandidate


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


def validate_citations(
    draft: LessonDraft, chunks: Iterable[StructuredChunk]
) -> ValidationReport:
    """Reject citations absent from the supplied index or mismatched to sentence role."""
    by_id: dict[str, StructuredChunk] = {}
    findings: list[Finding] = []
    for chunk in chunks:
        if chunk.chunk_id in by_id:
            findings.append(
                Finding("citation.duplicate_chunk_id", f"Duplicate chunk ID: {chunk.chunk_id}", chunk.chunk_id)
            )
        by_id[chunk.chunk_id] = chunk

    for sentence in draft.sentences:
        for chunk_id in sentence.citations:
            cited = by_id.get(chunk_id)
            location = f"{sentence.sid}:{chunk_id}"
            if cited is None:
                findings.append(
                    Finding("citation.unknown_chunk", f"Citation does not resolve: {chunk_id}", location)
                )
                continue
            expected_role = {
                "primary": "spine",
                "supplementary": "supplementary",
            }.get(sentence.kind)
            if expected_role is not None and cited.role != expected_role:
                findings.append(
                    Finding(
                        "citation.role_mismatch",
                        f"{sentence.kind} sentence cites a {cited.role} chunk",
                        location,
                    )
                )
    return ValidationReport(tuple(findings))


_NEGATION_TOKENS = {"not", "no", "never", "without", "cannot"}


def detect_explicit_polarity_conflicts(
    spine_chunks: Iterable[StructuredChunk],
    supplementary_chunks: Iterable[StructuredChunk],
    *,
    lesson_id: str,
) -> list[SupplementCandidate]:
    """Find only exact-source statements differing by explicit negation.

    This high-confidence heuristic is a narrow negative control, not a semantic
    contradiction system: paraphrases and numeric conflicts require a separate judge.
    """
    import re
    import unicodedata

    def tokenized(text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return re.findall(r"[\w']+", normalized, flags=re.UNICODE)

    primary_rows = []
    for chunk in spine_chunks:
        tokens = tokenized(chunk.text)
        polarity = frozenset(tokens) & _NEGATION_TOKENS
        content = tuple(token for token in tokens if token not in _NEGATION_TOKENS)
        if content:
            primary_rows.append((chunk, content, polarity))
    candidates: list[SupplementCandidate] = []
    for chunk in supplementary_chunks:
        if chunk.role != "supplementary":
            continue
        tokens = tokenized(chunk.text)
        polarity = frozenset(tokens) & _NEGATION_TOKENS
        content = tuple(token for token in tokens if token not in _NEGATION_TOKENS)
        if not content:
            continue
        if any(content == primary_content and bool(polarity) != bool(primary_polarity)
               for _, primary_content, primary_polarity in primary_rows):
            candidates.append(SupplementCandidate(
                lesson_id=lesson_id,
                chunk_id=chunk.chunk_id,
                book_id=chunk.book_id,
                pages=chunk.pages,
                kind="contradiction",
                novelty=1.0,
                rerank_score=1.0,
                anchor_block_id=next((primary.block_ids[0] for primary, primary_content, primary_polarity in primary_rows
                                      if primary_content == content and bool(primary_polarity) != bool(polarity)), None),
            ))
    return candidates
