"""BookTermbase: G3 approval, consistency checks and the versioned JSON artifact."""
from __future__ import annotations

import json

import pytest
from b2c_contracts import TermbaseEntry
from b2c_translation.termbase import (
    BookTermbase,
    Proposal,
    TermbaseError,
    TermCandidate,
)


def _cand(source, kind="term", context=None):
    return TermCandidate(source=source, kind=kind, frequency=3, sections=2, unit_ids=("b:u00001",),
                         definition_context=context)


def _tb():
    tb = BookTermbase.new("b", source_language="en", target_language="ar")
    tb.add_candidate(_cand("Signal Mapping", context="Signal Mapping is a technique."),
                     [Proposal(target="رسم الإشارات", provenance="gateway_proposer", reference="m@v1"),
                      Proposal(target="خريطة الإشارات", provenance="glossary", reference="g@sha")])
    tb.add_candidate(_cand("Lena Vasquez", kind="person"),
                     [Proposal(target="لينا فاسكيز", provenance="transliteration", reference="r")])
    tb.add_candidate(_cand("Premortem"), [])
    return tb


def test_new_entries_are_pending_and_proposals_ordered_by_priority():
    tb = _tb()
    assert {r.source for r in tb.pending()} == {"Signal Mapping", "Lena Vasquez", "Premortem"}
    rec = tb.get("signal mapping")
    assert [p.provenance for p in rec.proposals] == ["glossary", "gateway_proposer"]
    assert rec.approved_by is None and rec.target is None
    assert tb.locked_terms() == {} and tb.entity_table() == {}


def test_approve_feeds_locked_terms_and_entity_table_only():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="reviewer-a")
    tb.approve("Lena Vasquez", "لينا فاسكيز", reviewer="reviewer-a")
    assert tb.locked_terms() == {"Signal Mapping": "خريطة الإشارات"}
    assert tb.entity_table() == {"Lena Vasquez": "لينا فاسكيز"}
    assert {r.source for r in tb.pending()} == {"Premortem"}
    rec = tb.get("Signal Mapping")
    assert rec.status == "approved" and rec.approved_by == "reviewer-a" and rec.approved_at is not None


def test_approval_requires_human_reviewer_and_target():
    tb = _tb()
    for reviewer in ("", "  ", "g3-policy-wikidata-agrovoc-context-v1"):
        with pytest.raises(TermbaseError):
            tb.approve("Signal Mapping", "خريطة الإشارات", reviewer=reviewer)
    with pytest.raises(TermbaseError):
        tb.approve("Signal Mapping", "  ", reviewer="r")
    with pytest.raises(TermbaseError):
        tb.approve("Unknown Term", "س", reviewer="r")


def test_one_target_per_source_reapproval_needs_replace():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r")
    with pytest.raises(TermbaseError):
        tb.approve("Signal Mapping", "رسم الإشارات", reviewer="r")
    tb.approve("Signal Mapping", "رسم الإشارات", reviewer="r2", replace=True)
    assert tb.locked_terms() == {"Signal Mapping": "رسم الإشارات"}


def test_target_reuse_for_two_sources_rejected_unless_intended():
    tb = _tb()
    tb.add_candidate(_cand("Signal Map"), [])
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r")
    with pytest.raises(TermbaseError):
        tb.approve("Signal Map", "خريطة الإشارات", reviewer="r")
    tb.approve("Signal Map", "خريطة الإشارات", reviewer="r", allow_shared_target=True)
    assert tb.consistency_issues() == []


def test_forbidden_variants():
    tb = _tb()
    with pytest.raises(TermbaseError):
        tb.approve("Premortem", "ما قبل الموت", reviewer="r", forbidden_variants=["ما قبل الموت"])
    tb.approve("Premortem", "تحليل ما قبل الإخفاق", reviewer="r", forbidden_variants=["ما قبل الموت"])
    assert tb.forbidden_variants() == {"Premortem": ("ما قبل الموت",)}
    hits = tb.forbidden_hits("Run a Premortem now.", "أجرِ تحليل ما قبل الموت الآن.")
    assert hits == [("Premortem", "ما قبل الموت")]
    assert tb.forbidden_hits("Nothing here.", "ما قبل الموت") == []


def test_forbid_other_proposals_on_approval():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r", forbid_other_proposals=True)
    assert tb.forbidden_variants()["Signal Mapping"] == ("رسم الإشارات",)


def test_reject_and_reviewer_required():
    tb = _tb()
    with pytest.raises(TermbaseError):
        tb.reject("Premortem", reviewer="", reason="not a term")
    tb.reject("Premortem", reviewer="r", reason="not a term")
    rec = tb.get("Premortem")
    assert rec.status == "rejected" and rec.rejection_reason == "not a term"
    assert "Premortem" not in tb.locked_terms()
    assert {r.source for r in tb.pending()} == {"Signal Mapping", "Lena Vasquez"}


def test_duplicate_candidate_rejected():
    tb = _tb()
    with pytest.raises(TermbaseError):
        tb.add_candidate(_cand("signal mapping"), [])


def test_do_not_translate_keeps_source():
    tb = _tb()
    tb.add_candidate(_cand("ARH", kind="acronym"), [])
    tb.approve("ARH", "ARH", reviewer="r", do_not_translate=True)
    assert tb.locked_terms()["ARH"] == "ARH"
    with pytest.raises(TermbaseError):
        tb.approve("Premortem", "Premortem", reviewer="r")  # Latin target without do_not_translate


def test_artifact_roundtrip_with_sha256_and_version():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r")
    raw = tb.to_json()
    doc = json.loads(raw)
    assert doc["format"] == "b2c.book-termbase" and doc["format_version"] == 1
    assert len(doc["sha256"]) == 64
    back = BookTermbase.from_json(raw)
    assert back.locked_terms() == tb.locked_terms()
    assert back.to_json() == raw
    doc["entries"][0]["target"] = "شيء آخر"
    with pytest.raises(TermbaseError):
        BookTermbase.from_json(json.dumps(doc, ensure_ascii=False))
    doc2 = json.loads(raw)
    doc2["format_version"] = 99
    with pytest.raises(TermbaseError):
        BookTermbase.from_json(json.dumps(doc2, ensure_ascii=False))


def test_consistency_issues_on_tampered_artifact_with_valid_hash():
    tb = _tb()
    tb.add_candidate(_cand("Signal Map"), [])
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r")
    tb.approve("Signal Map", "خريطة الإشارات", reviewer="r", allow_shared_target=True)
    rec = tb.get("Signal Map")
    rec.allow_shared_target = False
    kinds = {i.kind for i in tb.consistency_issues()}
    assert kinds == {"shared_target"}


def test_contract_entries_only_approved_terms():
    tb = _tb()
    tb.approve("Signal Mapping", "خريطة الإشارات", reviewer="r", forbidden_variants=["رسم الإشارات"])
    entries = tb.to_contract_entries()
    assert entries == [TermbaseEntry(term_en="Signal Mapping", term_ar="خريطة الإشارات",
                                     definition_en="Signal Mapping is a technique.",
                                     forbidden_variants_ar=["رسم الإشارات"], approved_by="r")]
