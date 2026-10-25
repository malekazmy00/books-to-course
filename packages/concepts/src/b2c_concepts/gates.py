"""Promotion gates of a ConceptLayer (docs/02 §11.3). Provisional policy (2026-10-25), recorded in
every GateResult through the definition.

* ``concepts.grounding`` (deterministic) — every span of every author-voice statement (introduced
  at, definitions, relations) and every lesson mention re-validated against the parsed book:
  grounding rate must be 1.0, with at least one statement.
* ``concepts.coverage`` (deterministic) — share of the target termbase concepts (approved entries,
  or the selected candidates when no termbase is approved) that have an entry, i.e. occur in the
  lesson text with a recorded first mention: at least ``min_coverage``.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import ConceptLayer

from .book import BookIndex
from .grounding import validate_layer

GROUNDING_GATE_ID = "concepts.grounding"
COVERAGE_GATE_ID = "concepts.coverage"
GROUNDING_DEFINITION: dict[str, Any] = {"min_grounding_rate": 1.0, "min_statements": 1,
                                        "spans": ["introduced_at", "definition", "relation", "lesson_mention"]}
COVERAGE_DEFINITION: dict[str, Any] = {"min_coverage": 0.9,
                                       "target": "approved termbase terms/acronyms, else selected candidates"}


@dataclass(frozen=True)
class GateOutcome:
    gate_id: str
    kind: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    definition: dict[str, Any] = field(default_factory=dict)


def evaluate_concept_layer(layer: ConceptLayer, index: BookIndex, *, target_terms: Sequence[str]) -> list[GateOutcome]:
    report = validate_layer(layer, index)
    grounding = GateOutcome(GROUNDING_GATE_ID, "deterministic", passed=(
        report.statements >= GROUNDING_DEFINITION["min_statements"]
        and report.grounding_rate >= GROUNDING_DEFINITION["min_grounding_rate"]), metrics={
        "grounding_rate": round(report.grounding_rate, 4), "spans": report.total_spans,
        "statements": report.statements, "invalid": report.invalid[:20], "invalid_count": len(report.invalid),
        "model_grounding_rate": layer.metrics.get("model_grounding_rate"),
        "rejected_statements": len(layer.rejected)}, definition=dict(GROUNDING_DEFINITION))
    present = {x.term.casefold() for x in layer.concepts}
    targets = list(dict.fromkeys(t for t in target_terms if t.strip()))
    covered = [t for t in targets if t.casefold() in present]
    coverage = len(covered) / len(targets) if targets else 0.0
    cov = GateOutcome(COVERAGE_GATE_ID, "deterministic", passed=bool(targets) and coverage >= COVERAGE_DEFINITION[
        "min_coverage"], metrics={
        "coverage": round(coverage, 4), "targets": len(targets), "covered": len(covered),
        "missing": [t for t in targets if t.casefold() not in present][:50],
        "definitions": sum(x.author_definition is not None for x in layer.concepts),
        "termbase_basis": layer.termbase_basis}, definition=dict(COVERAGE_DEFINITION))
    return [grounding, cov]


__all__ = ["COVERAGE_DEFINITION", "COVERAGE_GATE_ID", "GROUNDING_DEFINITION", "GROUNDING_GATE_ID", "GateOutcome",
           "evaluate_concept_layer"]
