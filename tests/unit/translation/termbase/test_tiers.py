"""Entry tiers (term extraction v3, D-039): locked / hint / dropped.

* ``locked`` — an approved entry checked strictly (the only tier before v3; an approved
  entry without a tier is locked).
* ``hint``   — an approved entry injected into the prompt as a preferred rendering and
  checked only with a minor-severity, inflection-tolerant match.
* ``dropped`` — noise: a rejected entry (status ``rejected``).

Compatibility: an artifact and an approved snapshot written before v3 load unchanged, keep
their SHA-256 and keep their ``approved_digest`` (the frozen translation runs depend on it).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from b2c_contracts import TermbaseEntry
from b2c_orchestration.termbase_review import approved_digest, approved_snapshot
from b2c_translation.termbase import BookTermbase, Proposal, TermbaseError, TermCandidate

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/termbases/v1-compat"
REVIEWER = "ai-provisional-claude-2026-10-25"


def _cand(source, kind="term", **extra):
    return TermCandidate(source=source, kind=kind, frequency=3, sections=2, unit_ids=("b:u00001",), **extra)


def _tb():
    tb = BookTermbase.new("b", source_language="en", target_language="ar")
    tb.add_candidate(_cand("Signal Mapping"), [Proposal(target="خريطة الإشارات", provenance="glossary")])
    tb.add_candidate(_cand("analyst"), [Proposal(target="المحلل", provenance="gateway_proposer")])
    tb.add_candidate(_cand("Bolshevik"), [Proposal(target="البلشفي", provenance="gateway_proposer")])
    tb.add_candidate(_cand("Lena Vasquez", kind="person"), [Proposal(target="لينا فاسكيز", provenance="transliteration")])
    return tb


# ------------------------------------------------------------------ compatibility
def test_pre_v3_artifact_loads_with_the_same_sha256_and_digest():
    raw = (FIXTURES / "book-termbase-v1-approved.json").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "book-termbase-v1-approved.digest.json").read_text(encoding="utf-8"))
    tb = BookTermbase.from_json(raw)
    assert tb.sha256() == expected["termbase_sha256"]
    assert tb.to_json() == raw                       # byte-identical re-serialisation
    snapshot = approved_snapshot(tb)
    assert snapshot.sha256() == expected["snapshot_sha256"]
    assert approved_digest(snapshot) == expected["approved_digest"]
    frozen = BookTermbase.from_json(expected["snapshot_json"])
    assert approved_digest(frozen) == expected["approved_digest"]


def test_pre_v3_approved_entries_are_locked():
    tb = BookTermbase.from_json((FIXTURES / "book-termbase-v1-approved.json").read_text(encoding="utf-8"))
    assert all(r.tier is None and r.effective_tier == "locked" for r in tb.entries if r.status == "approved")
    assert tb.locked_terms() == {"ARH": "تحليل الفرضيات المتنافسة", "Signal Mapping": "خريطة الإشارات",
                                 "social group": "جماعة اجتماعية"}
    assert tb.hint_terms() == {}
    assert {r.effective_tier for r in tb.entries if r.status == "rejected"} == {"dropped"}


def test_new_fields_left_unset_are_not_serialised():
    tb = _tb()
    doc = json.loads(tb.to_json())
    for entry in doc["entries"]:
        assert "tier" not in entry
        assert "termhood" not in entry["candidate"] and "termhood_reason" not in entry["candidate"]


# ------------------------------------------------------------------ tiers
def test_hint_is_approved_but_not_locked():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", REVIEWER)
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    tb.approve("Lena Vasquez", "لينا فاسكيز", REVIEWER)
    assert tb.locked_terms() == {"Signal Mapping": "خريطة الإشارات"}
    assert tb.hint_terms() == {"analyst": "المحلل"}
    assert tb.entity_table() == {"Lena Vasquez": "لينا فاسكيز"}
    rec = tb.get("analyst")
    assert rec.status == "approved" and rec.tier == "hint" and rec.effective_tier == "hint"


def test_explicit_locked_tier_is_stored_as_the_default():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", REVIEWER, tier="locked")
    assert tb.get("Signal Mapping").tier is None      # same bytes as a pre-v3 approval
    assert "tier" not in json.loads(tb.to_json())["entries"][-1]


def test_an_entity_can_be_a_hint():
    tb = _tb()
    tb.approve("Lena Vasquez", "لينا فاسكيز", REVIEWER, tier="hint")
    assert tb.entity_table() == {} and tb.hint_terms() == {"Lena Vasquez": "لينا فاسكيز"}


def test_hint_takes_no_forbidden_variants():
    tb = _tb()
    with pytest.raises(TermbaseError):
        tb.approve("analyst", "المحلل", REVIEWER, tier="hint", forbidden_variants=["المحلّلة"])
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    assert tb.forbidden_variants() == {}


def test_dropped_is_not_an_approval_tier():
    tb = _tb()
    with pytest.raises(TermbaseError):
        tb.approve("analyst", "المحلل", REVIEWER, tier="dropped")


def test_retiering_needs_replace_and_rejection_clears_the_tier():
    tb = _tb()
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    with pytest.raises(TermbaseError):
        tb.approve("analyst", "المحلل", REVIEWER)            # hint -> locked silently
    tb.approve("analyst", "المحلل", REVIEWER, replace=True)
    assert tb.get("analyst").tier is None and tb.locked_terms() == {"analyst": "المحلل"}
    tb.reject("analyst", REVIEWER, "general vocabulary")
    assert tb.get("analyst").tier is None and tb.get("analyst").effective_tier == "dropped"


def test_hint_and_locked_entries_cannot_share_a_target_silently():
    tb = _tb()
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    with pytest.raises(TermbaseError):
        tb.approve("Bolshevik", "المحلل", REVIEWER)


def test_tier_survives_the_artifact_roundtrip():
    tb = _tb()
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    again = BookTermbase.from_json(tb.to_json())
    assert again.hint_terms() == {"analyst": "المحلل"} and again.sha256() == tb.sha256()


def test_candidate_termhood_verdict_roundtrips():
    tb = BookTermbase.new("b", source_language="en", target_language="ar")
    tb.add_candidate(_cand("analyst", termhood="hint", termhood_reason="general vocabulary"), [])
    again = BookTermbase.from_json(tb.to_json())
    assert again.get("analyst").candidate.termhood == "hint"
    assert again.get("analyst").candidate.termhood_reason == "general vocabulary"


# ------------------------------------------------------------------ digest
def test_digest_changes_when_a_hint_is_added_but_not_for_locked_only_snapshots():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", REVIEWER)
    locked_only = approved_digest(approved_snapshot(tb))
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    with_hint = approved_digest(approved_snapshot(tb))
    assert with_hint != locked_only
    assert approved_snapshot(tb).hint_terms() == {"analyst": "المحلل"}


# ------------------------------------------------------------------ contract
def test_contract_entries_carry_the_tier():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", REVIEWER)
    tb.approve("analyst", "المحلل", REVIEWER, tier="hint")
    entries = {e.term_en: e for e in tb.to_contract_entries()}
    assert entries["Signal Mapping"].tier == "locked" and entries["analyst"].tier == "hint"
    assert TermbaseEntry(term_en="x", term_ar="س", definition_en="x").tier == "locked"
