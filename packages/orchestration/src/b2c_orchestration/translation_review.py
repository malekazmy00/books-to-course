"""Human review of rebuilt-path unit results (D-028 principle 8; T-TR-04/05).

A reviewer either accepts the current text as-is or submits an edit. Either way the text
must still pass the prompt-leakage and footnote-marker checks: a human cannot approve an
instruction leak or a lost marker. Passing text is stored as ``origin="human"``,
``decided_by="human"``, ``accepted`` (the store keeps it over any later policy result) and
written to the translation memory so the same source is not mistranslated again. The
reviewer id and every check finding are kept in the unit's draft history.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from b2c_contracts import QAIssue, TranslationUnit, UnitTranslation
from b2c_translation.prompts import ALL_TEMPLATES
from b2c_translation.qa import CheckContext
from b2c_translation.qa.checks import run_checks
from b2c_translation.qa.leakage import fingerprints_from_templates

from .translation_store import StoredUnitResult, TranslationResultStore

REVIEW_GATE_CHECKS = frozenset({"prompt_leakage", "footnote_markers"})
_LEAK_FINGERPRINTS = fingerprints_from_templates(t.text for t in ALL_TEMPLATES)


class ReviewRejected(ValueError):
    """The reviewed text fails a check a human decision cannot override."""

    def __init__(self, issues: list[QAIssue]) -> None:
        super().__init__("reviewed text fails the leakage/footnote checks")
        self.issues = issues


class ReviewConflict(RuntimeError):
    """The unit is not in a state that can be reviewed."""


def review_checks(unit: TranslationUnit, text: str, *, source_language: str,
                  target_language: str) -> tuple[list[QAIssue], list[QAIssue]]:
    """(blocking gate issues, all deterministic findings) for a human text."""
    issues = run_checks(CheckContext(unit=unit, target=text, source_language=source_language,
                                     target_language=target_language, leak_fingerprints=_LEAK_FINGERPRINTS))
    return [i for i in issues if i.check_id in REVIEW_GATE_CHECKS], issues


def _accept_text(current: StoredUnitResult) -> str:
    if current.translation.target_text.strip():
        return current.translation.target_text
    for draft in reversed(current.drafts):
        text = draft.get("text") if isinstance(draft, dict) else None
        if isinstance(text, str) and text.strip():
            return text
    raise ReviewConflict("there is no draft text to accept; submit an edit")


def apply_human_review(*, store: TranslationResultStore, memory: Any | None, run_id: str, unit: TranslationUnit,
                       action: Literal["accept", "edit"], text: str | None, reviewer_id: str,
                       source_language: str, target_language: str,
                       clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> StoredUnitResult:
    if action not in ("accept", "edit"):
        raise ValueError("action must be 'accept' or 'edit'")
    current = store.fetch(run_id, unit.unit_id)
    if current is None:
        raise ReviewConflict("the unit has no result yet")
    if current.decision == "dropped":
        raise ReviewConflict("dropped units carry no translation")
    if action == "edit":
        if text is None or not text.strip():
            raise ValueError("an edit needs non-blank text")
        final = text.strip()
    else:
        final = _accept_text(current).strip()
    blocking, findings = review_checks(unit, final, source_language=source_language,
                                       target_language=target_language)
    if blocking:
        raise ReviewRejected(blocking)
    decided_at = clock()
    translation = UnitTranslation(
        unit_id=unit.unit_id, target_text=final, origin="human", engine=None,
        attempts=current.translation.attempts, issues=[i for i in findings if i.severity == "minor"],
        decision="accepted", decided_by="human", decided_at=decided_at)
    history = [*current.drafts, {
        "lever": "human", "action": action, "text": final, "reviewer_id": reviewer_id,
        "decided_at": decided_at.isoformat(), "previous_decision": current.decision,
        "issues": [i.model_dump(mode="json") for i in findings]}]
    store.upsert(run_id, unit.book_id, translation, drafts=history)
    if memory is not None:
        memory.store(unit, translation, source_language=source_language, target_language=target_language)
    stored = store.fetch(run_id, unit.unit_id)
    assert stored is not None
    return stored


__all__ = ["REVIEW_GATE_CHECKS", "ReviewConflict", "ReviewRejected", "apply_human_review", "review_checks"]
