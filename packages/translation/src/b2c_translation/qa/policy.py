"""Decision policy for a translated unit (D-028 §2 principles 7-8).

``decide`` combines deterministic issues and the semantic QE outcome into one decision and
the next action. The outcomes are built to satisfy the ``UnitTranslation`` invariants:

* nothing with a major/critical issue (deterministic or QE) is accepted;
* engine output is accepted by policy only with a passing semantic QE; without QE the
  unit goes to review (``needs_review``), never to ``accepted``;
* human translations are never accepted by policy (a human decision accepts them).

Action table (``attempts`` = engine attempts already made for this unit):

=================================================  ============  ===============
situation                                          decision      next_action
=================================================  ============  ===============
no blocking issue, QE passed (or rule/memory)      accepted      accept
no blocking issue, no QE (engine/human)            needs_review  review
blocking, attempts left, all repairable, repair    needs_review  repair
blocking or QE failed, attempts left, otherwise    needs_review  retranslate
attempts exhausted, some critical issue            rejected      reject
attempts exhausted, otherwise                      needs_review  review
=================================================  ============  ===============

``reject`` means the text is not worth post-editing (e.g. leaked instructions): the unit
goes to human translation from scratch. ``review`` means a human post-edits the draft.
Repair is only chosen for categories a targeted edit can fix (catalogue: terms, entities,
numbers, markers, punctuation); meaning-level errors (leak, omission, addition, meaning,
polarity) are retranslated, never patched.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from b2c_contracts import EngineIdentity, QACategory, QAIssue, QEResult, UnitTranslation

from .types import QEOutcome

NextAction = Literal["accept", "repair", "retranslate", "review", "reject"]
PolicyDecision = Literal["accepted", "needs_review", "rejected"]
Origin = Literal["engine", "rule", "memory", "human"]

REPAIRABLE_CATEGORIES: frozenset[QACategory] = frozenset({
    "terminology", "named_entity", "number_error", "date_error", "footnote_marker", "quotation",
    "structure_formatting", "bibliographic", "grammar", "fluency_style", "repetition",
    "untranslated_english",
})
BLOCKING_SEVERITIES = frozenset({"major", "critical"})


@dataclass(frozen=True)
class Decision:
    decision: PolicyDecision
    next_action: NextAction
    reasons: tuple[str, ...]
    issues: tuple[QAIssue, ...] = ()
    qe: QEResult | None = None
    origin: Origin = "engine"

    def to_unit_translation(self, *, unit_id: str, target_text: str, origin: Origin | None = None,
                            engine: EngineIdentity | None, attempts: int,
                            decided_at: datetime) -> UnitTranslation:
        """Persistable record of this decision (validated by the contract invariants)."""
        return UnitTranslation(unit_id=unit_id, target_text=target_text, origin=origin or self.origin,
                               engine=engine, attempts=attempts, issues=list(self.issues), qe=self.qe,
                               decision=self.decision, decided_by="policy", decided_at=decided_at)


def decide(issues: Iterable[QAIssue], qe: QEOutcome | None, *, attempts: int, max_attempts: int,
           engine_supports_repair: bool, origin: Origin = "engine") -> Decision:
    if attempts < 0 or max_attempts < 1:
        raise ValueError("attempts must be >= 0 and max_attempts >= 1")
    det = list(issues)
    qe_issues = list(qe.issues) if qe is not None else []
    all_issues = tuple(det + qe_issues)
    blocking = [i for i in all_issues if i.severity in BLOCKING_SEVERITIES]
    det_pass = not any(i.severity in BLOCKING_SEVERITIES for i in det)
    qe_failed = qe is not None and not qe.passed
    exhausted = attempts >= max_attempts

    def result(decision: PolicyDecision, action: NextAction, *reasons: str) -> Decision:
        route = {"accept": "auto_accept", "repair": "retranslate", "retranslate": "retranslate",
                 "review": "human_review", "reject": "human_review"}[action]
        qe_result = None if qe is None else qe.result.model_copy(
            update={"deterministic_pass": det_pass, "route": route})
        return Decision(decision=decision, next_action=action, reasons=tuple(reasons), issues=all_issues,
                        qe=qe_result, origin=origin)

    if not blocking and not qe_failed:
        if origin == "human":
            return result("needs_review", "review", "human translation needs a human decision")
        if qe is not None or origin in ("rule", "memory"):
            return result("accepted", "accept", "all checks passed" + ("" if qe is None else " and QE passed"))
        return result("needs_review", "review", "no semantic QE configured: engine output is unverified")

    reasons = [f"{i.severity} {i.category} ({i.check_id})" for i in blocking]
    if qe_failed:
        reasons.append("semantic QE failed")
    if origin != "engine" or exhausted:
        if any(i.severity == "critical" for i in blocking):
            return result("rejected", "reject", *reasons, "attempts exhausted with a critical issue")
        return result("needs_review", "review", *reasons, "attempts exhausted" if exhausted else
                      f"{origin} output cannot be regenerated")
    repairable = (engine_supports_repair and not qe_failed and blocking
                  and all(i.category in REPAIRABLE_CATEGORIES and i.severity == "major" for i in blocking))
    if repairable:
        return result("needs_review", "repair", *reasons)
    return result("needs_review", "retranslate", *reasons)


__all__ = ["BLOCKING_SEVERITIES", "Decision", "NextAction", "REPAIRABLE_CATEGORIES", "decide"]
