"""Accepted spelling variants of approved names (D-047).

A variant is another acceptable Arabic spelling of an approved name (book 1: University of
Groningen approved as «جامعة خرونينغن», the common «جامعة جرونينجن» was a false alarm, D-046 F13).
The named-entity check accepts it; the engine never sees it; an artifact or digest without any
variant is byte-for-byte what it was before.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from b2c_contracts import TranslationUnit
from b2c_orchestration.termbase_review import approved_digest, approved_snapshot
from b2c_translation.qa import CheckContext
from b2c_translation.qa import checks as C
from b2c_translation.termbase import BookTermbase, Proposal, TermbaseError, TermCandidate

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/termbases/v1-compat"
REVIEWER = "ai-provisional-claude-2026-10-25"


def _tb() -> BookTermbase:
    tb = BookTermbase.new("b", source_language="en", target_language="ar")
    tb.add_candidate(TermCandidate(source="University of Groningen", kind="organization", frequency=2),
                     [Proposal(target="جامعة خرونينغن", provenance="wikidata")])
    tb.add_candidate(TermCandidate(source="Signal Mapping", kind="term", frequency=3),
                     [Proposal(target="خريطة الإشارات", provenance="glossary")])
    return tb


def test_an_artifact_without_variants_is_unchanged():
    raw = (FIXTURES / "book-termbase-v1-approved.json").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "book-termbase-v1-approved.digest.json").read_text(encoding="utf-8"))
    tb = BookTermbase.from_json(raw)
    assert tb.to_json() == raw and "accepted_variants" not in tb.to_json()
    assert approved_digest(approved_snapshot(tb)) == expected["approved_digest"]
    assert tb.entity_variants() == {}


def test_approval_stores_variants_for_a_name():
    tb = _tb()
    tb.approve("University of Groningen", "جامعة خرونينغن", REVIEWER, accepted_variants=["جامعة جرونينجن", " "])
    assert tb.entity_variants() == {"University of Groningen": ("جامعة جرونينجن",)}
    assert tb.entity_table() == {"University of Groningen": "جامعة خرونينغن"}   # the prompt form is unchanged
    again = BookTermbase.from_json(tb.to_json())
    assert again.entity_variants() == tb.entity_variants()


@pytest.mark.parametrize("variants,message", [
    (["Groningen"], "Latin"),
    (["جامعة خرونينغن"], "target"),
])
def test_invalid_variants_are_refused(variants, message):
    with pytest.raises(TermbaseError, match=message):
        _tb().approve("University of Groningen", "جامعة خرونينغن", REVIEWER, accepted_variants=variants)


def test_variants_are_for_names_only():
    with pytest.raises(TermbaseError, match="name"):
        _tb().approve("Signal Mapping", "خريطة الإشارات", REVIEWER, accepted_variants=["رسم الإشارات"])


def test_a_variant_cannot_be_a_forbidden_variant():
    tb = _tb()
    with pytest.raises(TermbaseError, match="forbidden"):
        tb.approve("University of Groningen", "جامعة خرونينغن", REVIEWER, forbidden_variants=["جامعة جرونينجن"],
                   accepted_variants=["جامعة جرونينجن"])


def test_the_digest_is_unchanged_without_variants_and_changes_with_them():
    tb = _tb()
    tb.approve("University of Groningen", "جامعة خرونينغن", REVIEWER)
    snapshot = approved_snapshot(tb)
    pre = {"terms": {}, "entities": {"University of Groningen": "جامعة خرونينغن"}, "forbidden": {},
           "languages": ["en", "ar"]}
    raw = json.dumps(pre, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert approved_digest(snapshot) == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tb.approve("University of Groningen", "جامعة خرونينغن", REVIEWER, replace=True,
               accepted_variants=["جامعة جرونينجن"])
    assert approved_digest(approved_snapshot(tb)) != approved_digest(snapshot)


def test_a_rejection_clears_the_variants():
    tb = _tb()
    tb.approve("University of Groningen", "جامعة خرونينغن", REVIEWER, accepted_variants=["جامعة جرونينجن"])
    tb.reject("University of Groningen", REVIEWER, "not a name")
    assert tb.get("University of Groningen").accepted_variants == [] and tb.entity_variants() == {}


# ------------------------------------------------------------------ the check
def _ctx(source, target, variants):
    unit = TranslationUnit(unit_id="b:u00019", book_id="b", block_ids=["b1"], kind="list_item", source_text=source,
                           footnote_markers=[], pages=[1], section_path=["Praise"])
    return CheckContext(unit=unit, target=target, entities={"University of Groningen": "جامعة خرونينغن"},
                        entity_variants=variants)


def test_the_check_accepts_an_approved_variant():
    # book-main:u00019
    source = "—Christopher K. Lamont, University of Groningen"
    target = "— كريستوفر ك. لامونت، جامعة جرونينجن"
    assert C.named_entity(_ctx(source, target, {})) != []
    assert C.named_entity(_ctx(source, target, {"University of Groningen": ("جامعة جرونينجن",)})) == []


def test_a_variant_gets_the_same_clitic_and_spelling_tolerance():
    source = "at the University of Groningen"
    assert C.named_entity(_ctx(source, "في جامعة جرونينجن", {"University of Groningen": ("جامعة جرونينجن",)})) == []
    assert C.named_entity(_ctx(source, "وبجامعة جرونينجن", {"University of Groningen": ("جامعة جرونينجن",)})) == []
    # an unrelated spelling is still an error
    assert C.named_entity(_ctx(source, "في جامعة جروننج", {"University of Groningen": ("جامعة جرونينجن",)}))
