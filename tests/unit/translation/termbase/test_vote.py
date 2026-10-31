"""Termbase approval by model vote (D-031 made permanent by D-033; charter §3; amendment §5 step 2).

A judge from another model family votes on each pending entry; code decides by agreement.
An entry is locked only when the judge agrees with an independent proposal (an established
source, or the engine's own proposer); otherwise it is at most a hint. Synthetic data.
"""
from __future__ import annotations

import json

import pytest
from b2c_translation.engines.base import TextCompletion
from b2c_translation.termbase import Proposal, TermCandidate, TermRecord
from b2c_translation.termbase.vote import (VOTE_TEMPLATE, TermVerdict, TermVoter, decide, parse_verdict,
                                           reviewer_id)


def record(source="hegemony", kind="term", proposals=(("الهيمنة", "gateway_proposer"),), **kw):
    cand = TermCandidate(source=source, kind=kind, frequency=kw.pop("frequency", 5), sections=2,
                         definition_context=kw.pop("definition", "Hegemony is leadership by consent."))
    return TermRecord(source=source, kind=kind, candidate=cand,
                      proposals=[Proposal(target=t, provenance=p) for t, p in proposals])


class Scripted:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def complete_text(self, messages, *, prompt_version, max_tokens, temperature=0.0):
        self.calls.append({"messages": messages, "prompt_version": prompt_version})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)
        return TextCompletion(text=text, model="judge-x")


# ------------------------------------------------------------------ prompt and parsing
def test_prompt_is_versioned_and_the_entry_is_sent_as_data():
    gw = Scripted({"tier": "locked", "choice": 1, "target": None, "reason": "r"})
    TermVoter(gw).review(record())
    [call] = gw.calls
    assert call["prompt_version"] == VOTE_TEMPLATE.version_id == "termbase.vote.system@v1"
    assert call["messages"][0]["role"] == "system"
    data = json.loads(call["messages"][-1]["content"])
    assert data["term"] == "hegemony" and data["kind"] == "term"
    assert data["proposals"] == [{"id": 1, "arabic": "الهيمنة", "source": "gateway_proposer"}]
    assert "Hegemony is leadership" in data["definition"]


def test_verdict_is_parsed_and_records_the_judge_model():
    v = TermVoter(Scripted({"tier": "hint", "choice": None, "target": "التمفصل", "reason": "r"})).review(record())
    assert v == TermVerdict(tier="hint", choice=None, target="التمفصل", reason="r", model="judge-x")


@pytest.mark.parametrize("raw", ["not json", '{"tier": "maybe"}', '{"tier": "locked", "choice": 7}',
                                 '{"tier": "locked", "choice": null, "target": null}'])
def test_invalid_verdicts_raise(raw):
    with pytest.raises(ValueError):
        parse_verdict(raw, record(), model="m")


# ------------------------------------------------------------------ decision by agreement
def test_judge_agrees_with_engine_proposal_locks_it():
    d = decide(record(), TermVerdict(tier="locked", choice=1, target=None, reason="r", model="m"))
    assert (d.action, d.target, d.tier, d.agreement) == ("approve", "الهيمنة", "locked", "judge+gateway_proposer")


def test_judge_agrees_with_established_source_locks_it():
    rec = record(proposals=(("احتمال", "wikidata"), ("الاحتمالية", "gateway_proposer")))
    d = decide(rec, TermVerdict(tier="locked", choice=1, target=None, reason="r", model="m"))
    assert (d.target, d.tier, d.agreement) == ("احتمال", "locked", "judge+wikidata")


def test_judge_own_target_is_never_locked():
    d = decide(record(), TermVerdict(tier="locked", choice=None, target="التمفصل", reason="r", model="m"))
    assert (d.action, d.target, d.tier, d.agreement) == ("approve", "التمفصل", "hint", "judge_only")


def test_hint_vote_stays_hint_even_with_agreement():
    d = decide(record(), TermVerdict(tier="hint", choice=1, target=None, reason="r", model="m"))
    assert (d.target, d.tier) == ("الهيمنة", "hint")


def test_transliteration_alone_is_not_an_independent_agreement():
    rec = record(source="Gramsci", kind="person", proposals=(("غرامشي", "transliteration"),))
    d = decide(rec, TermVerdict(tier="locked", choice=1, target=None, reason="r", model="m"))
    assert (d.target, d.tier, d.agreement) == ("غرامشي", "hint", "judge+transliteration")


def test_dropped_vote_rejects_with_the_reason():
    d = decide(record(source="July"), TermVerdict(tier="dropped", choice=None, target=None,
                                                  reason="month name", model="m"))
    assert (d.action, d.reason) == ("reject", "model vote: month name")


def test_unavailable_judge_falls_back_to_a_hint_of_the_best_proposal():
    d = decide(record(), None)
    assert (d.action, d.target, d.tier, d.agreement) == ("approve", "الهيمنة", "hint", "no_judge")


def test_unavailable_judge_and_no_proposal_rejects():
    d = decide(record(proposals=()), None)
    assert d.action == "reject" and "no Arabic proposal" in d.reason


def test_reviewer_id_names_the_judge_and_is_a_valid_reviewer():
    rid = reviewer_id("deepseek/deepseek-v4-flash-0731")
    assert rid == "ai-vote-deepseek-deepseek-v4-flash-0731"
    assert not rid.startswith("g3-policy-")
