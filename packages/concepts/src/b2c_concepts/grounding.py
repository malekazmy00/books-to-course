"""Grounding validator: every author-voice statement points to a book passage that exists.

A ``BookSpan`` is valid when its block is part of the lesson text and its quote occurs in that
block's text (case, whitespace, quote-mark and dash style normalized). The concept layer builder
uses ``span_valid`` before accepting a model proposal; the gate re-runs ``validate_layer`` on the
stored artifact against the parsed book.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from b2c_contracts import BookSpan, ConceptLayer

from .book import BookIndex

_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "‚": "'",
                         "–": "-", "—": "-", "―": "-", "‐": "-", "‑": "-", "…": "..."})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    text = re.sub(r"\s*-\s*", " - ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_block(quote: str, block_text: str) -> bool:
    q = normalize(quote).strip(" .\"'")
    return bool(q) and q in normalize(block_text)


def span_valid(span: BookSpan, index: BookIndex) -> bool:
    block = index.by_id.get(span.block_id)
    return block is not None and block.idx in index.lesson_of and quote_in_block(span.quote, block.text)


@dataclass
class GroundingReport:
    total_spans: int = 0
    statements: int = 0
    invalid: list[str] = field(default_factory=list)

    @property
    def grounding_rate(self) -> float:
        return (self.total_spans - len(self.invalid)) / self.total_spans if self.total_spans else 0.0


def validate_layer(layer: ConceptLayer, index: BookIndex) -> GroundingReport:
    report = GroundingReport()

    def check(where: str, spans: list[BookSpan]) -> None:
        report.statements += 1
        for i, s in enumerate(spans):
            report.total_spans += 1
            if not span_valid(s, index):
                report.invalid.append(f"{where}[{i}] {s.block_id}")

    for x in layer.concepts:
        check(f"{x.concept_id}.introduced_at", [x.introduced_at])
        if x.author_definition:
            check(f"{x.concept_id}.definition", list(x.author_definition.spans))
        for r in x.relations:
            check(f"{x.concept_id}.relation:{r.target_id}", list(r.evidence))
    for lesson in layer.lessons:
        for m in lesson.first_mentions:
            block = index.by_id.get(m.block_id)
            report.total_spans += 1
            if block is None or index.lesson_of.get(block.idx) is None or \
                    index.lesson_of[block.idx].lesson_id != lesson.lesson_id:
                report.invalid.append(f"{lesson.lesson_id}.mention {m.block_id}")
    return report


__all__ = ["GroundingReport", "normalize", "quote_in_block", "span_valid", "validate_layer"]
