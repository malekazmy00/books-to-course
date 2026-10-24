"""Durable per-unit translation results in Postgres (D-028 part D, audit 08 §2.2).

Results live in ``translation_unit_results`` (not in overwritable JSON under ``_system/``);
a human decision is never overwritten by a policy result.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import EngineIdentity, QAIssue, QEResult, UnitTranslation

import b2c_orchestration.translation_store as ts
from b2c_orchestration.translation_store import TranslationResultStore, unit_seq
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
RUN = "a" * 64
ENGINE = EngineIdentity(provider="fake", model_id="m", revision="r", prompt_version="p@1")


def tr(uid, decision="needs_review", *, origin="engine", decided_by="policy", text="نص."):
    qe = QEResult(deterministic_pass=True, route="auto_accept") if decision == "accepted" else None
    return UnitTranslation(unit_id=uid, target_text="" if decision == "dropped" else text,
                           origin=origin if decision != "dropped" else "rule",
                           engine=ENGINE if origin == "engine" and decision != "dropped" else None,
                           qe=qe, decision=decision, decided_by=decided_by, decided_at=NOW)


@pytest.fixture
def db():
    return FakeTranslationDB()


@pytest.fixture
def store(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(10_000))
    return TranslationResultStore("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def test_unit_seq_parses_the_contract_unit_id():
    assert unit_seq("book-1:u00042") == 42
    with pytest.raises(ValueError):
        unit_seq("book-1:x00042")


def test_schema_is_created_once_per_instance(store, db):
    store.upsert(RUN, "b", tr("b:u00001"))
    store.fetch(RUN, "b:u00001")
    store.counts(RUN)
    assert db.ddl == len(ts._DDL)


def test_schema_creation_is_thread_safe(db):
    store = TranslationResultStore("postgresql://unused", connect=db.connect)
    threads = [threading.Thread(target=store.counts, args=(RUN,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.ddl == len(ts._DDL)


def test_upsert_roundtrip_keeps_translation_drafts_and_seq(store):
    issue = QAIssue(check_id="numbers", category="number_error", severity="major", origin="deterministic")
    t = tr("b:u00007").model_copy(update={"issues": [issue]})
    assert store.upsert(RUN, "b", t, drafts=[{"text": "x", "lever": "initial"}]) is True
    got = store.fetch(RUN, "b:u00007")
    assert got.translation == t and got.drafts == [{"text": "x", "lever": "initial"}]
    assert got.seq == 7 and got.book_id == "b" and got.decision == "needs_review"
    assert store.fetch(RUN, "b:u00008") is None
    assert store.fetch("b" * 64, "b:u00007") is None


def test_upsert_rejects_a_unit_from_another_book(store):
    with pytest.raises(ValueError):
        store.upsert(RUN, "other", tr("b:u00001"))


def test_policy_result_never_overwrites_a_human_result(store, db):
    store.upsert(RUN, "b", tr("b:u00001"))
    human = tr("b:u00001", "accepted", origin="human", decided_by="human", text="بشري.")
    assert store.upsert(RUN, "b", human) is True
    assert store.upsert(RUN, "b", tr("b:u00001", "accepted", text="آلي.")) is False
    assert store.fetch(RUN, "b:u00001").translation.target_text == "بشري."
    # a later human correction replaces an earlier one
    assert store.upsert(RUN, "b", human.model_copy(update={"target_text": "بشري ثان."})) is True
    assert store.fetch(RUN, "b:u00001").translation.target_text == "بشري ثان."
    # the guard is in SQL, not only in Python
    assert "decided_by <> 'human' OR EXCLUDED.decided_by = 'human'" in " ".join(ts._UPSERT.split())


def test_list_by_decision_counts_and_final_ids(store):
    store.upsert(RUN, "b", tr("b:u00003", "rejected", text=""))
    store.upsert(RUN, "b", tr("b:u00001", "accepted"))
    store.upsert(RUN, "b", tr("b:u00002"))
    store.upsert(RUN, "b", tr("b:u00004", "dropped"))
    store.upsert(RUN, "b", tr("b:u00005"))
    store.upsert(RUN, "b", tr("b:u00006", "accepted", origin="human", decided_by="human"))
    queue = store.list_by_decision(RUN, ["needs_review", "rejected"], limit=10)
    assert [r.unit_id for r in queue] == ["b:u00002", "b:u00003", "b:u00005"]
    assert [r.unit_id for r in store.list_by_decision(RUN, ["needs_review", "rejected"], limit=1, offset=1)] \
        == ["b:u00003"]
    assert store.count_by_decision(RUN, ["needs_review", "rejected"]) == 3
    assert store.counts(RUN) == {"accepted": 2, "needs_review": 2, "rejected": 1, "dropped": 1}
    ids = [f"b:u0000{i}" for i in range(1, 8)]
    assert store.final_unit_ids(RUN, ids) == {"b:u00001", "b:u00003", "b:u00004", "b:u00006"}
    assert set(store.fetch_many(RUN, ["b:u00002", "b:u00009"])) == {"b:u00002"}
    assert [r.unit_id for r in store.fetch_run(RUN)] == [f"b:u0000{i}" for i in range(1, 7)]


def test_list_by_decision_validates_arguments(store):
    with pytest.raises(ValueError):
        store.list_by_decision(RUN, ["maybe"])
    with pytest.raises(ValueError):
        store.list_by_decision(RUN, ["needs_review"], limit=0)
    with pytest.raises(ValueError):
        store.list_by_decision(RUN, ["needs_review"], offset=-1)


def test_revision_changes_when_a_result_changes(store):
    store.upsert(RUN, "b", tr("b:u00001"))
    first = store.revision(RUN)
    assert first == store.revision(RUN) and len(first) == 64
    store.upsert(RUN, "b", tr("b:u00001", "accepted", origin="human", decided_by="human"))
    assert store.revision(RUN) != first


def test_run_registry_is_idempotent_and_records_units_and_assembly(store):
    assert store.get_run(RUN) is None
    assert store.register_run(RUN, course_id="c", workflow_id="wf", input_json={"a": 1}) is True
    assert store.register_run(RUN, course_id="c", workflow_id="wf", input_json={"a": 2}) is False
    run = store.get_run(RUN)
    assert run.input_json == {"a": 1} and run.units_ref is None and run.assembly is None
    store.set_units(RUN, units_ref={"stage": "translation-units"}, unit_count=3, book_id="b")
    run = store.get_run(RUN)
    assert run.units_ref == {"stage": "translation-units"} and run.unit_count == 3 and run.book_id == "b"
    late = NOW + timedelta(hours=1)
    assert store.set_assembly(RUN, {"key": "new"}, snapshot_at=late) is True
    # an assembly of an older snapshot never replaces a newer one
    assert store.set_assembly(RUN, {"key": "old"}, snapshot_at=NOW) is False
    assert store.get_run(RUN).assembly == {"key": "new"}


def test_invalid_run_id_is_refused(store):
    with pytest.raises(ValueError):
        store.fetch("not-a-run", "b:u00001")
