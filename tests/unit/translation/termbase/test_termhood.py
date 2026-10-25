"""Termhood classifier (term extraction v3, D-039): locked / hint / dropped per candidate,
through ``TextCompletionGateway`` only, before any proposal is asked for. Synthetic data."""
from __future__ import annotations

import json

import pytest
from b2c_contracts import EngineIdentity
from b2c_translation.engines.base import TextCompletion
from b2c_translation.termbase import (FEW_SHOT_EXAMPLES, TERMHOOD_TEMPLATE, GatewayTermProposer,
                                      TermCandidate, TermhoodClassifier, build_book_termbase,
                                      parse_termhood)

from .conftest import make_units

IDENTITY = EngineIdentity(provider="local", model_id="judge", revision=None, prompt_version="unset")


class ScriptedGateway:
    """Answers each batch with the verdicts in ``answers`` (by term); records the requests."""

    def __init__(self, answers, *, raw=None):
        self.answers, self.raw, self.calls = answers, raw, []

    def complete_text(self, messages, *, prompt_version, max_tokens, temperature=0.0):
        self.calls.append({"messages": messages, "prompt_version": prompt_version, "max_tokens": max_tokens})
        if self.raw is not None:
            return TextCompletion(text=self.raw, model="m")
        items = json.loads(messages[-1]["content"])["candidates"]
        out = [{"id": it["id"], "tier": self.answers.get(it["term"], "locked"), "reason": "r"} for it in items]
        return TextCompletion(text=json.dumps(out), model="m")


def _cand(source, kind="term", **kw):
    return TermCandidate(source=source, kind=kind, frequency=kw.pop("frequency", 4), sections=2, **kw)


# ------------------------------------------------------------------ prompt
def test_prompt_is_versioned_and_built_from_the_real_rejection_reasons():
    assert TERMHOOD_TEMPLATE.version_id == "termbase.termhood.system@v1"
    text = TERMHOOD_TEMPLATE.text
    for rule in ("locked", "hint", "dropped", "month", "nationality", "citation", "fragment",
                 "general vocabulary", "inflect"):
        assert rule in text, rule
    tiers = {e["tier"] for e in FEW_SHOT_EXAMPLES}
    assert tiers == {"locked", "hint", "dropped"}
    assert all(e["reason"] for e in FEW_SHOT_EXAMPLES)


def test_the_user_message_is_data_with_context_and_signals():
    gateway = ScriptedGateway({})
    clf = TermhoodClassifier(gateway, IDENTITY, book_text_allowed=True, batch_size=10)
    clf.classify([_cand("Signal Mapping", definition_context="Signal Mapping is a technique.",
                        signals=("capitalized", "defined"))],
                 contexts={"Signal Mapping": ["Teams use Signal Mapping."]})
    [call] = gateway.calls
    assert call["prompt_version"] == "termbase.termhood.system@v1"
    assert call["messages"][0]["role"] == "system"
    data = json.loads(call["messages"][-1]["content"])
    [item] = data["candidates"]
    assert item["term"] == "Signal Mapping" and item["kind"] == "term" and item["frequency"] == 4
    assert item["signals"] == ["capitalized", "defined"]
    assert item["definition"] == "Signal Mapping is a technique."
    assert item["contexts"] == ["Teams use Signal Mapping."]


# ------------------------------------------------------------------ verdicts
def test_batches_and_verdicts():
    gateway = ScriptedGateway({"analyst": "hint", "July": "dropped"})
    clf = TermhoodClassifier(gateway, IDENTITY, book_text_allowed=True, batch_size=2)
    verdicts = clf.classify([_cand("Signal Mapping"), _cand("analyst"), _cand("July", kind="person")])
    assert len(gateway.calls) == 2
    assert {k: v.tier for k, v in verdicts.items()} == {"Signal Mapping": "locked", "analyst": "hint",
                                                         "July": "dropped"}


@pytest.mark.parametrize("raw", ["not json", "[]", '[{"id": 0, "tier": "maybe"}]', '{"id": 0}'])
def test_unparseable_or_invalid_answers_leave_the_candidate_unclassified(raw):
    clf = TermhoodClassifier(ScriptedGateway({}, raw=raw), IDENTITY, book_text_allowed=True)
    assert clf.classify([_cand("Signal Mapping")]) == {}


def test_unanswered_candidates_are_asked_once_more_in_smaller_batches():
    class FlakyGateway(ScriptedGateway):
        def complete_text(self, messages, **kw):
            if not self.calls:
                self.calls.append({"messages": messages})
                return TextCompletion(text='[{"id": 0, "tier": "hint", "reason": "r"}, {"id": 1, "ti', model="m")
            return super().complete_text(messages, **kw)

    gateway = FlakyGateway({"July": "dropped"})
    clf = TermhoodClassifier(gateway, IDENTITY, book_text_allowed=True, batch_size=4)
    verdicts = clf.classify([_cand("analyst"), _cand("Signal Mapping"), _cand("July"), _cand("ARH", "acronym")])
    assert {k: v.tier for k, v in verdicts.items()} == {"analyst": "hint", "Signal Mapping": "locked",
                                                         "July": "dropped", "ARH": "locked"}
    assert len(gateway.calls) == 3          # the truncated batch, then its 3 missing items in 2 batches


def test_parse_termhood_accepts_a_fenced_json_array():
    raw = '```json\n[{"id": 0, "tier": "hint", "reason": "general vocabulary"}]\n```'
    [(i, tier, reason)] = parse_termhood(raw)
    assert (i, tier, reason) == (0, "hint", "general vocabulary")


def test_book_text_needs_an_explicit_permission():
    with pytest.raises(ValueError):
        TermhoodClassifier(ScriptedGateway({}), IDENTITY, book_text_allowed=False)


# ------------------------------------------------------------------ in the build
class CountingProposer(GatewayTermProposer):
    def __init__(self):
        super().__init__(gateway=None, identity=IDENTITY, local_gateway_confirmed=True)
        self.asked = []

    def propose(self, candidate):
        self.asked.append(candidate.source)
        return []


def test_build_records_the_verdict_and_skips_proposals_for_dropped_candidates():
    units = make_units()
    gateway = ScriptedGateway({"Kelburn Square": "dropped", "confirmation drift": "hint"})
    proposer = CountingProposer()
    tb = build_book_termbase(units, sources=[proposer], source_language="en", target_language="ar",
                             classifier=TermhoodClassifier(gateway, IDENTITY, book_text_allowed=True))
    by = {r.source: r for r in tb.entries}
    assert by["Kelburn Square"].candidate.termhood == "dropped"
    assert by["confirmation drift"].candidate.termhood == "hint"
    assert by["Kelburn Square"].status == "pending"            # a suggestion, never a decision
    assert "Kelburn Square" not in proposer.asked and "confirmation drift" in proposer.asked
    item = json.loads(gateway.calls[0]["messages"][-1]["content"])["candidates"][0]
    assert item["contexts"]                                     # sentences from the book's units


def test_build_without_classifier_is_unchanged():
    units = make_units()
    tb = build_book_termbase(units, sources=[], source_language="en", target_language="ar")
    assert all(r.candidate.termhood is None for r in tb.entries)
