"""Promotion gates of a KnowledgeDossier (docs/02 §11.3). Thresholds are provisional policy
(2026-10-25), recorded in every GateResult through the gate definition; they are not calibrated
against human judgements (there is no human reviewer in the product, D-033).

* ``knowledge.citations`` (semantic: decided with the independent judge's verdicts) — at least
  ``min_published_claims`` published claims; the raw citation support rate (claims whose quote was
  found AND judged supporting / all extracted claims) at least ``min_raw_support_rate``; every
  published claim valid (the contract already enforces it; re-checked here); judge family differs.
* ``knowledge.tier-coverage`` (deterministic) — the book card and the author sections each have a
  published claim backed by a tier-1 source; at most ``max_tier3_only_share`` of published claims
  rest on tier-3 evidence only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import KnowledgeDossier

CITATIONS_GATE_ID = "knowledge.citations"
TIER_GATE_ID = "knowledge.tier-coverage"
CITATIONS_DEFINITION: dict[str, Any] = {"min_published_claims": 5, "min_raw_support_rate": 0.5,
                                        "published_claims_valid": True, "judge_family_differs": True,
                                        "rule": "fact = one tier-1 source or two distinct sources"}
TIER_DEFINITION: dict[str, Any] = {"tier1_sections": ["book_card", "author"], "max_tier3_only_share": 0.25}


@dataclass(frozen=True)
class GateOutcome:
    gate_id: str
    kind: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    definition: dict[str, Any] = field(default_factory=dict)


def evaluate_dossier(d: KnowledgeDossier, *, min_published_claims: int | None = None) -> list[GateOutcome]:
    cdef = dict(CITATIONS_DEFINITION)
    if min_published_claims is not None:
        cdef["min_published_claims"] = min_published_claims
    published = d.published()
    raw = int(d.metrics.get("raw_claims", 0))
    supported = int(d.metrics.get("judged_supported", 0))
    rate = supported / raw if raw else 0.0
    invalid = sum(not all(e.valid for e in c.evidence) for c in published)
    citations = GateOutcome(CITATIONS_GATE_ID, "semantic", passed=(
        len(published) >= cdef["min_published_claims"] and rate >= cdef["min_raw_support_rate"] and invalid == 0
        and d.extractor_family != d.judge_family), metrics={
        "published_claims": len(published), "raw_claims": raw, "citation_support_rate": round(rate, 4),
        "quote_found_rate": d.metrics.get("quote_found_rate"), "invalid_published": invalid,
        "verified": d.metrics.get("verified"), "attributed": d.metrics.get("attributed"),
        "rejected_claims": d.metrics.get("rejected_claims"), "extractor": d.extractor.model_id,
        "judge": d.judge.model_id}, definition=cdef)
    tier1 = {s: any(c.section == s and any(e.tier == 1 for e in c.evidence) for c in published)
             for s in TIER_DEFINITION["tier1_sections"]}
    tier3_only = sum(all(e.tier == 3 for e in c.evidence) for c in published)
    share = tier3_only / len(published) if published else 1.0
    tiers = GateOutcome(TIER_GATE_ID, "deterministic", passed=(
        all(tier1.values()) and share <= TIER_DEFINITION["max_tier3_only_share"]), metrics={
        "tier1_sections": tier1, "tier3_only_share": round(share, 4),
        "source_tiers": d.metrics.get("source_tiers", {}),
        "published_by_best_tier": d.metrics.get("published_by_best_tier", {})}, definition=dict(TIER_DEFINITION))
    return [citations, tiers]


__all__ = ["CITATIONS_DEFINITION", "CITATIONS_GATE_ID", "GateOutcome", "TIER_DEFINITION", "TIER_GATE_ID",
           "evaluate_dossier"]
