"""Termbase review state (D-028 principle 5, Gate G3): merge by source form, human decisions,
the approval gate counts and the approved-only snapshot. Postgres is the in-memory fake."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate

import b2c_orchestration.termbase_store as tbs
from b2c_orchestration.termbase_review import (TermbaseDecisionRejected, TermbaseNotFound, TermSelection,
                                               approve_terms, approved_digest, approved_snapshot,
                                               check_arabic_target, gate_counts, reject_term)
from b2c_orchestration.termbase_store import TermbaseScope, TermbaseStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
SCOPE = TermbaseScope("c1", "bk", "en", "ar")


def termbase(rows) -> BookTermbase:
    tb = BookTermbase.new("bk", source_language="en", target_language="ar", extractor_version="x")
    for source, kind, freq, targets in rows:
        tb.add_candidate(TermCandidate(source=source, kind=kind, frequency=freq, sections=1),
                         [Proposal(target=t, provenance="glossary") for t in targets])
    return tb


ROWS = [("Signal Mapping", "term", 5, ["رسم الإشارات"]),
        ("Lena Vasquez", "person", 3, ["لينا فاسكيز"]),
        ("ARH", "acronym", 1, []),
        ("Vantor University", "organization", 2, ["جامعة فانتور"])]


@pytest.fixture
def db():
    return FakeTermbaseDB()


@pytest.fixture
def store(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TermbaseStore("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def test_schema_is_created_once(store, db):
    store.entries(SCOPE)
    store.entries(SCOPE)
    assert db.ddl == len(tbs._DDL)


def test_extraction_merge_never_writes_human_columns():
    sql = " ".join(tbs._UPSERT_EXTRACTED.split()).split("DO UPDATE SET", 1)[1]
    for column in ("status", "target", "reviewer", "decided_at", "rejection_reason"):
        assert f"{column} =" not in sql


def test_merge_keeps_human_decisions_and_marks_vanished_entries(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    approve_terms(store, SCOPE, [TermSelection("signal mapping")], "aly")
    reject_term(store, SCOPE, "ARH", "aly", "not a term")
    # re-extraction: new frequency and proposals, ARH gone, a new candidate
    again = termbase([("Signal  Mapping", "term", 9, ["تخطيط الإشارات"]), ("Lena Vasquez", "person", 3, []),
                      ("Vantor University", "organization", 2, []), ("Quiet Ledger", "work", 2, [])])
    outcome = store.merge_extraction(SCOPE, again)
    assert outcome.extracted == 4 and outcome.absent == 1
    by_key = {e.source_key: e for e in store.entries(SCOPE)}
    sm = by_key["signal mapping"]
    assert sm.status == "approved" and sm.target == "رسم الإشارات" and sm.reviewer == "aly"
    assert sm.frequency == 9 and sm.proposals[0].target == "تخطيط الإشارات"
    assert by_key["arh"].status == "rejected" and not by_key["arh"].present
    assert by_key["quiet ledger"].status == "pending" and by_key["quiet ledger"].present
    loaded = store.load(SCOPE)
    assert loaded.locked_terms() == {"Signal  Mapping": "رسم الإشارات"}
    assert loaded.get("ARH").rejected_by == "aly"


def test_approve_validates_reviewer_arabic_and_one_target_per_source(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH")], "aly")  # no proposal, no target
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH", "ARH method")], "aly")
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH", "   ")], "aly")
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH", "تحليل")], "g3-policy-auto")  # machine reviewer
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH", "تحليل"), TermSelection("arh", "تحليل آخر")], "aly")
    with pytest.raises(TermbaseNotFound):
        approve_terms(store, SCOPE, [TermSelection("Nope", "لا")], "aly")
    [arh] = approve_terms(store, SCOPE, [TermSelection("ARH", "تحليل الفرضيات المتنافسة")], "aly")
    assert arh.status == "approved" and arh.target == "تحليل الفرضيات المتنافسة"
    # a different target for an approved source needs replace=True
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("ARH", "تحليل")], "aly")
    [arh] = approve_terms(store, SCOPE, [TermSelection("ARH", "تحليل")], "aly", replace=True)
    assert arh.target == "تحليل"
    # the same target locked for another source is refused unless explicitly shared
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("Signal Mapping", "تحليل")], "aly")


def test_bulk_approval_is_all_or_nothing_and_proposals_only(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("Signal Mapping", "رسم الإشارات"),
                                     TermSelection("Lena Vasquez", "اسم آخر")], "aly", proposals_only=True)
    assert all(e.status == "pending" for e in store.entries(SCOPE))
    done = approve_terms(store, SCOPE, [TermSelection("Signal Mapping"), TermSelection("Lena Vasquez")], "aly",
                         proposals_only=True)
    assert [e.status for e in done] == ["approved", "approved"]


def test_gate_counts_only_present_pending_entries_at_or_above_the_threshold(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    counts = gate_counts(store.entries(SCOPE), 1)
    assert counts.pending_blocking == 4 and counts.approved == 0
    assert gate_counts(store.entries(SCOPE), 3).pending_blocking == 2
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping")], "aly")
    reject_term(store, SCOPE, "Lena Vasquez", "aly")
    counts = gate_counts(store.entries(SCOPE), 3)
    assert counts.as_json() == {"pending_blocking": 0, "pending_total": 2, "pending_dropped": 0, "approved": 1,
                                "approved_hint": 0, "rejected": 1, "absent": 0, "min_frequency": 3}


def test_snapshot_and_digest_use_approved_entries_only(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    empty = approved_digest(approved_snapshot(store.load(SCOPE)))
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping"), TermSelection("Lena Vasquez")], "aly")
    snap = approved_snapshot(store.load(SCOPE))
    assert {r.source for r in snap.entries} == {"Signal Mapping", "Lena Vasquez"}
    assert snap.locked_terms() == {"Signal Mapping": "رسم الإشارات"}
    assert snap.entity_table() == {"Lena Vasquez": "لينا فاسكيز"}
    digest = approved_digest(snap)
    assert digest != empty
    # rejecting or re-extracting a pending entry does not change what translation uses
    reject_term(store, SCOPE, "ARH", "aly")
    assert approved_digest(approved_snapshot(store.load(SCOPE))) == digest
    approve_terms(store, SCOPE, [TermSelection("Signal Mapping", "تخطيط الإشارات")], "aly", replace=True)
    assert approved_digest(approved_snapshot(store.load(SCOPE))) != digest
    assert BookTermbase.from_json(snap.to_json()).locked_terms() == snap.locked_terms()


def test_arabic_target_check():
    assert check_arabic_target("  رسم   الإشارات ") == "رسم الإشارات"
    assert check_arabic_target("القرن 19") == "القرن 19"
    for bad in ("", "  ", "Signal", "رسم Mapping", "123", "<b>رسم</b>", "ر" * 200):
        with pytest.raises(TermbaseDecisionRejected):
            check_arabic_target(bad)


def test_approved_for_book_spans_courses(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    other = TermbaseScope("c2", "bk", "en", "ar")
    store.merge_extraction(other, termbase(ROWS))
    approve_terms(store, other, [TermSelection("Signal Mapping")], "aly")
    rows = store.approved_for_book("bk", "en", "ar")
    assert [(r.course_id, r.source) for r in rows] == [("c2", "Signal Mapping")]
