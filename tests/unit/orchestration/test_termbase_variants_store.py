"""Accepted variants of approved names in the review state and the translate activities (D-047)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import b2c_orchestration.termbase_store as tbs
from b2c_orchestration.termbase_review import (TermbaseDecisionRejected, TermSelection, approve_terms,
                                               approved_snapshot, reject_term)
from b2c_orchestration.termbase_store import TermbaseScope, TermbaseStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB
from tests.unit.orchestration.test_book_termbase_activities import (  # noqa: F401 - fixtures
    FakeBuilder, TermEngine, artifacts, batch, build_input, check_input, results, setup, terms)
from tests.unit.orchestration.test_termbase_tiers_store import termbase

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SCOPE = TermbaseScope("c1", "bk", "en", "ar")
ROWS = [("University of Groningen", "organization", 2, ["جامعة خرونينغن"], None),
        ("Signal Mapping", "term", 5, ["رسم الإشارات"], None)]


@pytest.fixture
def store():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TermbaseStore("postgresql://unused", connect=FakeTermbaseDB().connect, clock=lambda: next(clock))


def test_the_variants_column_is_added_and_never_written_by_extraction():
    ddl = " ".join(" ".join(tbs._DDL).split())
    assert "ADD COLUMN IF NOT EXISTS accepted_variants_json jsonb NOT NULL DEFAULT '[]'::jsonb" in ddl
    assert "accepted_variants" not in " ".join(tbs._UPSERT_EXTRACTED.split())


def test_variants_are_stored_listed_and_loaded(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    [entry] = approve_terms(store, SCOPE, [TermSelection("University of Groningen",
                                                         accepted_variants=("جامعة جرونينجن",))], "aly")
    assert entry.accepted_variants == ["جامعة جرونينجن"]
    assert entry.as_json()["accepted_variants"] == ["جامعة جرونينجن"]
    loaded = approved_snapshot(store.load(SCOPE))
    assert loaded.entity_variants() == {"University of Groningen": ("جامعة جرونينجن",)}
    reject_term(store, SCOPE, "University of Groningen", "aly", "dup")
    rejected = next(e for e in store.entries(SCOPE) if e.source == "University of Groningen")
    assert rejected.status == "rejected" and rejected.accepted_variants == []


def test_variants_on_a_term_are_refused(store):
    store.merge_extraction(SCOPE, termbase(ROWS))
    with pytest.raises(TermbaseDecisionRejected):
        approve_terms(store, SCOPE, [TermSelection("Signal Mapping", accepted_variants=("خريطة الإشارات",))], "aly")


def test_the_translate_activity_sends_names_and_checks_variants(artifacts, results, terms):  # noqa: F811
    builder = FakeBuilder([("Rice", "organization", 2), ("Wheat", "term", 1)])
    engine = TermEngine()
    inp, acts, prepared = setup(artifacts, results, terms, engine=engine, builder=builder)
    acts.build_termbase(build_input(inp, prepared))
    approve_terms(terms, SCOPE, [TermSelection("Rice", "الأرز", accepted_variants=("ارز",))], "aly")
    gate = acts.check_termbase(check_input(inp, require_approved=False))
    run = batch(inp, prepared, prepared.unit_ids).model_copy(update={"termbase_ref": gate.snapshot,
                                                                     "termbase_digest": gate.digest})
    acts.translate_batch(run)
    rice = next(c for c in engine.calls if c.unit.source_text.startswith("Rice"))
    assert dict(rice.entities) == {"Rice": "الأرز"} and dict(rice.terms) == {}
    assert "ارز" not in str(rice.entities)
