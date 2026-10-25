"""Entry tiers in the review state (D-039): the ``tier`` column, hint approvals, the classifier's
"dropped" suggestion in the gate, and digest compatibility for rows written before v3."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate

import b2c_orchestration.termbase_store as tbs
from b2c_orchestration.termbase_review import (TermbaseDecisionRejected, TermSelection, approve_terms,
                                               approved_digest, approved_snapshot, gate_counts, reject_term)
from b2c_orchestration.termbase_store import TermbaseScope, TermbaseStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SCOPE = TermbaseScope("c1", "bk", "en", "ar")


def termbase(rows) -> BookTermbase:
    tb = BookTermbase.new("bk", source_language="en", target_language="ar", extractor_version="x")
    for source, kind, freq, targets, termhood in rows:
        tb.add_candidate(TermCandidate(source=source, kind=kind, frequency=freq, sections=1, termhood=termhood,
                                       termhood_reason=None if termhood is None else "classifier"),
                         [Proposal(target=t, provenance="glossary") for t in targets])
    return tb


ROWS = [("Signal Mapping", "term", 5, ["رسم الإشارات"], None),
        ("analyst", "term", 9, ["المحلل"], "hint"),
        ("July", "person", 4, [], "dropped"),
        ("Lena Vasquez", "person", 3, ["لينا فاسكيز"], "locked")]


@pytest.fixture
def db():
    return FakeTermbaseDB()


@pytest.fixture
def store(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TermbaseStore("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def test_the_tier_column_is_added_without_rewriting_rows():
    ddl = " ".join(" ".join(tbs._DDL).split())
    assert "ADD COLUMN IF NOT EXISTS tier text" in ddl
    assert "tier =" not in " ".join(tbs._UPSERT_EXTRACTED.split()).split("DO UPDATE SET", 1)[1]


def test_hint_approval_is_stored_and_listed(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    [entry] = approve_terms(store, SCOPE, [TermSelection("analyst", tier="hint")], "ai-provisional-x")
    assert entry.status == "approved" and entry.tier == "hint"
    listed = {e.source: e.as_json() for e in store.entries(SCOPE)}
    assert listed["analyst"]["tier"] == "hint" and listed["Signal Mapping"]["tier"] is None
    assert listed["analyst"]["termhood"] == "hint" and listed["July"]["termhood"] == "dropped"
    assert listed["analyst"]["termhood_reason"] == "classifier"
    loaded = store.load(SCOPE)
    assert loaded.hint_terms() == {"analyst": "المحلل"} and loaded.locked_terms() == {}


def test_locked_approval_and_rejection_leave_the_tier_empty(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping")], "aly")
    reject_term(store, SCOPE, "July", "aly", "month name")
    by = {e.source: e for e in store.entries(SCOPE)}
    assert by["Signal Mapping"].tier is None and by["July"].tier is None
    assert store.load(SCOPE).locked_terms() == {"Signal Mapping": "رسم الإشارات"}


def test_a_hint_with_forbidden_variants_is_refused(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("analyst", forbidden_variants=("الباحث",), tier="hint")], "aly")


def test_classifier_dropped_suggestions_do_not_block_the_gate(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    counts = gate_counts(store.entries(SCOPE), 1)
    assert counts.pending_blocking == 3 and counts.pending_dropped == 1 and counts.pending_total == 4
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping"), TermSelection("Lena Vasquez")], "aly")
    approve_terms(store, SCOPE, [TermSelection("analyst", tier="hint")], "aly")
    counts = gate_counts(store.entries(SCOPE), 1)
    assert counts.as_json() == {"pending_blocking": 0, "pending_total": 1, "pending_dropped": 1, "approved": 3,
                                "approved_hint": 1, "rejected": 0, "absent": 0, "min_frequency": 1}


def test_digest_of_locked_only_rows_is_the_pre_v3_digest(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping"), TermSelection("Lena Vasquez")], "aly")
    snapshot = approved_snapshot(store.load(SCOPE))
    import hashlib
    import json
    pre_v3 = {"terms": {"Signal Mapping": "رسم الإشارات"}, "entities": {"Lena Vasquez": "لينا فاسكيز"},
              "forbidden": {}, "languages": ["en", "ar"]}
    raw = json.dumps(pre_v3, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert approved_digest(snapshot) == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    approve_terms(store, SCOPE, [TermSelection("analyst", tier="hint")], "aly")
    assert approved_digest(approved_snapshot(store.load(SCOPE))) != approved_digest(snapshot)
