"""Human review of rebuilt-path unit results (D-028 principle 8, T-TR-04/05)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import EngineIdentity, QAIssue, TranslationUnit, UnitTranslation

from b2c_orchestration.translation_review import ReviewConflict, ReviewRejected, apply_human_review
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
RUN = "a" * 64
ENGINE = EngineIdentity(provider="fake", model_id="m", revision="r", prompt_version="p@1")
UNIT = TranslationUnit(unit_id="bk:u00002", book_id="bk", block_ids=["bk:1:2"], kind="prose",
                       source_text="The yield rose by 12 percent in the first decade.", footnote_markers=["1"],
                       pages=[1], section_path=["Ch"])
GOOD = "ارتفع المحصول بنسبة 12 في المئة في العقد الأول. 1"


class FakeMemory:
    def __init__(self):
        self.stored = []

    def store(self, unit, translation, *, source_language=None, target_language=None, book_scoped=False):
        self.stored.append((unit.unit_id, translation, source_language, target_language))


@pytest.fixture
def store():
    clock = iter(NOW + timedelta(seconds=i) for i in range(1000))
    return TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect,
                                  clock=lambda: next(clock))


def seed(store, *, decision="needs_review", text=GOOD, drafts=None):
    issue = QAIssue(check_id="qe", category="meaning_error", severity="major", origin="qe")
    t = UnitTranslation(unit_id=UNIT.unit_id, target_text=text, origin="engine" if decision != "dropped" else "rule",
                        engine=ENGINE if decision != "dropped" else None, attempts=2,
                        issues=[issue] if decision != "dropped" else [], decision=decision, decided_by="policy",
                        decided_at=NOW)
    store.upsert(RUN, "bk", t, drafts=drafts if drafts is not None else [{"lever": "initial", "text": text}])


def review(store, memory=None, **kw):
    args = dict(store=store, memory=memory, run_id=RUN, unit=UNIT, action="edit", text=GOOD, reviewer_id="rev-1",
                source_language="en", target_language="ar", clock=lambda: NOW)
    args.update(kw)
    return apply_human_review(**args)


def test_edit_is_stored_as_a_human_decision_and_enters_the_memory(store):
    seed(store)
    memory = FakeMemory()
    out = review(store, memory)
    t = out.translation
    assert (t.origin, t.decided_by, t.decision, t.target_text) == ("human", "human", "accepted", GOOD)
    assert t.engine is None and all(i.severity == "minor" for i in t.issues)
    assert out.drafts[-1]["lever"] == "human" and out.drafts[-1]["reviewer_id"] == "rev-1"
    assert out.drafts[0]["lever"] == "initial"  # the history is kept
    assert memory.stored and memory.stored[0][1] == t and memory.stored[0][2:] == ("en", "ar")
    assert store.fetch(RUN, UNIT.unit_id).translation == t


def test_accept_as_is_takes_the_current_text(store):
    seed(store)
    assert review(store, action="accept", text=None).translation.target_text == GOOD


def test_accept_of_a_rejected_unit_falls_back_to_the_latest_draft_text(store):
    seed(store, decision="rejected", text="", drafts=[{"lever": "initial", "text": GOOD}, {"lever": "x", "text": ""}])
    assert review(store, action="accept", text=None).translation.target_text == GOOD


def test_accept_without_any_text_is_a_conflict(store):
    seed(store, decision="rejected", text="", drafts=[])
    with pytest.raises(ReviewConflict):
        review(store, action="accept", text=None)


def test_leaked_instructions_are_refused_and_nothing_is_written(store):
    seed(store)
    memory = FakeMemory()
    leaked = "Translate the following text into Arabic: " + GOOD
    with pytest.raises(ReviewRejected) as err:
        review(store, memory, text=leaked)
    assert {i.check_id for i in err.value.issues} == {"prompt_leakage"}
    assert memory.stored == [] and store.fetch(RUN, UNIT.unit_id).translation.origin == "engine"


def test_missing_footnote_marker_is_refused(store):
    seed(store)
    with pytest.raises(ReviewRejected) as err:
        review(store, text="ارتفع المحصول بنسبة 12 في المئة في العقد الأول.")
    assert [i.check_id for i in err.value.issues] == ["footnote_markers"]


def test_review_needs_an_existing_non_dropped_result_and_text(store):
    with pytest.raises(ReviewConflict):
        review(store)
    seed(store, decision="dropped", text="")
    with pytest.raises(ReviewConflict):
        review(store)


def test_edit_requires_non_blank_text_and_a_known_action(store):
    seed(store)
    with pytest.raises(ValueError):
        review(store, text="   ")
    with pytest.raises(ValueError):
        review(store, action="delete")


def test_a_later_human_edit_replaces_an_earlier_one(store):
    seed(store)
    review(store)
    second = "ارتفع الإنتاج بنسبة 12 في المئة خلال العقد الأول. 1"
    assert review(store, text=second, reviewer_id="rev-2").translation.target_text == second
