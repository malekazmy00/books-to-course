"""Human decisions on the per-book termbase and the approval gate (D-028 principle 5, Gate G3).

Every decision is validated by ``BookTermbase.approve`` / ``reject`` (human reviewer, no Latin
letters in a target, one locked target per source, no silent shared targets) on the scope's
current state, then only the decided rows are written. A bulk approval is all-or-nothing.

Only APPROVED entries ever reach translation: ``approved_snapshot`` keeps the approved
entries of the scope, and ``approved_digest`` hashes exactly what translation uses (locked
terms, entity table, forbidden variants and, since v3, hint entries), so the digest changes
when, and only when, those change. The ``hints`` key is added only when a hint exists, so the
digest of every termbase approved before v3 (D-039) is unchanged.

Tiers (v3, D-039): an approval is ``locked`` (default) or ``hint``; noise is rejected
("dropped"). A pending entry the termhood classifier marked ``dropped`` does not block the
gate: pending entries never reach translation anyway.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from b2c_translation.termbase import BookTermbase, TermbaseError
from b2c_translation.termbase.model import source_key, target_key
from b2c_translation.termbase.vote import TermDecision, decide, reviewer_id

from .termbase_store import StoredTermEntry, TermbaseScope, TermbaseStore

# Arabic letters (base block + supplements + presentation forms), Arabic-Indic and ASCII
# digits, spaces and light punctuation. At least one Arabic letter is required.
_ARABIC_LETTER = re.compile(r"[ء-يٱ-ۓۺ-ۼݐ-ݿࢠ-ࣿ"
                            r"ﭐ-﷿ﹰ-ﻼ]")
_ARABIC_TEXT = re.compile(r"^[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
                          r"0-9\s\-‐-—().,'’،/]+$")
MAX_TARGET_CHARS = 120


class TermbaseNotFound(LookupError):
    """No termbase entry with this source form in the scope."""


class TermbaseDecisionRejected(ValueError):
    """The decision breaks a G3 rule (reviewer, Arabic target, one target per source, ...)."""


def check_arabic_target(target: str | None) -> str:
    text = " ".join(unicodedata.normalize("NFC", target or "").split())
    if not text:
        raise TermbaseDecisionRejected("the Arabic target must not be empty")
    if len(text) > MAX_TARGET_CHARS:
        raise TermbaseDecisionRejected(f"the Arabic target is longer than {MAX_TARGET_CHARS} characters")
    if not _ARABIC_TEXT.match(text) or not _ARABIC_LETTER.search(text):
        raise TermbaseDecisionRejected("the target must be Arabic text (Arabic letters, digits, spaces, hyphens)")
    return text


@dataclass(frozen=True)
class TermSelection:
    source: str
    target: str | None = None  # None -> the entry's first (highest-priority) proposal
    forbidden_variants: tuple[str, ...] = ()
    tier: str = "locked"       # "locked" or "hint" (D-039)
    accepted_variants: tuple[str, ...] = ()  # other acceptable spellings of a name (D-047)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _find(termbase: BookTermbase, source: str):
    try:
        return termbase.get(source)
    except TermbaseError as exc:
        raise TermbaseNotFound(f"no termbase entry for {source!r}") from exc


def approve_terms(store: TermbaseStore, scope: TermbaseScope, selections: Sequence[TermSelection], reviewer: str,
                  *, proposals_only: bool = False, replace: bool = False, allow_shared_target: bool = False,
                  clock: Callable[[], datetime] = _now) -> list[StoredTermEntry]:
    """Approve one or more entries (all or nothing).

    ``proposals_only`` (bulk approval of selected proposals): every target must be one of the
    entry's own proposals. Otherwise an edited target is allowed.
    """
    if not selections:
        raise TermbaseDecisionRejected("nothing to approve")
    keys = [source_key(s.source) for s in selections]
    if len(set(keys)) != len(keys):
        raise TermbaseDecisionRejected("one target per source: a source appears twice")
    termbase = store.load(scope)
    now = clock()
    decided = []
    for selection in selections:
        record = _find(termbase, selection.source)
        if selection.target is None:
            if not record.proposals:
                raise TermbaseDecisionRejected(f"{record.source!r} has no proposal; give an Arabic target")
            target = record.proposals[0].target
        else:
            target = selection.target
        target = check_arabic_target(target)
        if proposals_only and target_key(target) not in {target_key(p.target) for p in record.proposals}:
            raise TermbaseDecisionRejected(f"{target!r} is not a proposal of {record.source!r}")
        try:
            decided.append(termbase.approve(record.source, target, reviewer, replace=replace,
                                            forbidden_variants=selection.forbidden_variants,
                                            allow_shared_target=allow_shared_target, tier=selection.tier,
                                            accepted_variants=selection.accepted_variants, now=now))
        except TermbaseError as exc:
            raise TermbaseDecisionRejected(str(exc)) from exc
    store.write_decisions(scope, decided)
    by_key = {e.source_key: e for e in store.entries(scope)}
    return [by_key[k] for k in keys]


def reject_term(store: TermbaseStore, scope: TermbaseScope, source: str, reviewer: str, reason: str = "",
                *, clock: Callable[[], datetime] = _now) -> StoredTermEntry:
    termbase = store.load(scope)
    record = _find(termbase, source)
    try:
        decided = termbase.reject(record.source, reviewer, reason or "rejected", now=clock())
    except TermbaseError as exc:
        raise TermbaseDecisionRejected(str(exc)) from exc
    store.write_decisions(scope, [decided])
    return next(e for e in store.entries(scope) if e.source_key == source_key(record.source))


# ------------------------------------------------------------------ gate
@dataclass(frozen=True)
class GateCounts:
    pending_blocking: int   # pending, still extracted, frequency >= threshold, not suggested "dropped"
    pending_total: int
    approved: int
    rejected: int
    absent: int             # no longer found by the latest extraction
    min_frequency: int
    pending_dropped: int = 0   # pending entries the termhood classifier marked "dropped" (D-039)
    approved_hint: int = 0     # approved entries of the hint tier (D-039)

    def as_json(self) -> dict[str, int]:
        return {"pending_blocking": self.pending_blocking, "pending_total": self.pending_total,
                "pending_dropped": self.pending_dropped, "approved": self.approved,
                "approved_hint": self.approved_hint, "rejected": self.rejected, "absent": self.absent,
                "min_frequency": self.min_frequency}


def gate_counts(entries: Sequence[StoredTermEntry], min_frequency: int) -> GateCounts:
    if min_frequency < 1:
        raise ValueError("min_frequency must be >= 1")
    pending = [e for e in entries if e.status == "pending"]
    dropped = [e for e in pending if e.candidate.termhood == "dropped"]
    return GateCounts(
        pending_blocking=sum(1 for e in pending if e.present and e.frequency >= min_frequency
                             and e.candidate.termhood != "dropped"),
        pending_total=len(pending), approved=sum(1 for e in entries if e.status == "approved"),
        rejected=sum(1 for e in entries if e.status == "rejected"),
        absent=sum(1 for e in entries if not e.present), min_frequency=min_frequency,
        pending_dropped=len(dropped),
        approved_hint=sum(1 for e in entries if e.status == "approved" and e.tier == "hint"))


# ------------------------------------------------------------------ review by model vote (D-033)
UNAVAILABLE_REVIEWER = "ai-vote-unavailable"


def _blocking(entry: StoredTermEntry, min_frequency: int) -> bool:
    return (entry.status == "pending" and entry.present and entry.frequency >= min_frequency
            and entry.candidate.termhood != "dropped")


def _apply(store: TermbaseStore, scope: TermbaseScope, entry: StoredTermEntry, decision: TermDecision,
           reviewer: str, clock: Callable[[], datetime]) -> TermDecision:
    """Write one decision. A target the G3 rules refuse as locked (shared with another entry) is
    kept as a shared hint; one they refuse altogether falls back to the best proposal as a hint,
    else a rejection. Returns what was written."""
    if decision.action == "reject":
        reject_term(store, scope, entry.source, reviewer, decision.reason, clock=clock)
        return decision
    attempts = [(decision.target, decision.tier, False), (decision.target, "hint", True)]
    if entry.proposals:
        attempts.append((entry.proposals[0].target, "hint", True))
    for target, tier, shared in attempts:
        try:
            approve_terms(store, scope, [TermSelection(entry.source, target, tier=tier)], reviewer,
                          allow_shared_target=shared, clock=clock)
            return TermDecision("approve", target, tier, decision.agreement, decision.reason)
        except TermbaseDecisionRejected:
            continue
    reason = f"{decision.reason}; no Arabic target passed the termbase rules"
    reject_term(store, scope, entry.source, reviewer, reason, clock=clock)
    return TermDecision("reject", None, None, decision.agreement, reason)


def review_by_models(store: TermbaseStore, scope: TermbaseScope, voter: Any, *, min_frequency: int,
                     clock: Callable[[], datetime] = _now) -> dict[str, Any]:
    """Decide every blocking pending entry by model vote (``b2c_translation.termbase.vote``).

    Entries already decided (by a person or an earlier vote) are left alone. A judge failure
    never leaves an entry pending: production does not wait on a person (D-033)."""
    counts = {"locked": 0, "hint": 0, "rejected": 0, "judge_unavailable": 0}
    decisions = []
    for entry in sorted(store.entries(scope), key=lambda e: (-e.frequency, e.source_key)):
        if not _blocking(entry, min_frequency):
            continue
        record = entry.record()
        try:
            verdict = voter.review(record)
        except Exception as exc:  # unavailable or invalid vote: decided without it, never left pending
            verdict, error = None, f"{type(exc).__name__}: {exc}"[:300]
        else:
            error = None
        reviewer = reviewer_id(verdict.model) if verdict is not None else UNAVAILABLE_REVIEWER
        written = _apply(store, scope, entry, decide(record, verdict), reviewer, clock)
        if verdict is None:
            counts["judge_unavailable"] += 1
        counts["rejected" if written.action == "reject" else written.tier] += 1
        decisions.append({"source": entry.source, "kind": entry.kind, "frequency": entry.frequency,
                          "action": written.action, "target": written.target, "tier": written.tier,
                          "agreement": written.agreement, "reason": written.reason, "reviewer": reviewer,
                          "judge_tier": None if verdict is None else verdict.tier,
                          "judge_choice": None if verdict is None else verdict.choice, "error": error})
    return {"counts": counts, "decisions": decisions}


def approved_snapshot(termbase: BookTermbase) -> BookTermbase:
    """Only the approved entries: pending ones are never used, never auto-approved."""
    return termbase.model_copy(update={"entries": [r.model_copy(deep=True) for r in termbase.entries
                                                   if r.status == "approved" and r.approved_by and r.target]})


def approved_digest(termbase: BookTermbase) -> str:
    payload = {"terms": termbase.locked_terms(), "entities": termbase.entity_table(),
               "forbidden": {k: list(v) for k, v in termbase.forbidden_variants().items()},
               "languages": [termbase.source_language, termbase.target_language]}
    hints = termbase.hint_terms()
    if hints:  # v3 (D-039): only present when used, so every pre-v3 digest is unchanged
        payload["hints"] = hints
    variants = termbase.entity_variants()
    if variants:  # D-047: likewise, a termbase without accepted variants keeps its digest
        payload["entity_variants"] = {k: list(v) for k, v in variants.items()}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["GateCounts", "MAX_TARGET_CHARS", "TermSelection", "TermbaseDecisionRejected", "TermbaseNotFound",
           "UNAVAILABLE_REVIEWER", "approve_terms", "approved_digest", "approved_snapshot", "check_arabic_target",
           "gate_counts", "reject_term", "review_by_models"]
