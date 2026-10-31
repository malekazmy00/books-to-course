"""Termbase review by model vote inside the line (D-033; amendment §5 step 2), with fakes.

Every blocking pending entry gets a decision by agreement (b2c_translation.termbase.vote); the
gate then opens without a person. Decisions carry an ``ai-vote-*`` reviewer and are evidence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate
from b2c_translation.termbase.vote import TermVerdict

from b2c_orchestration.termbase_review import TermSelection, approve_terms, gate_counts, review_by_models
from b2c_orchestration.termbase_store import TermbaseScope, TermbaseStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
SCOPE = TermbaseScope("c1", "bk", "en", "ar")


@pytest.fixture
def store():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    s = TermbaseStore("postgresql://unused", connect=FakeTermbaseDB().connect, clock=lambda: next(clock))
    tb = BookTermbase.new("bk", source_language="en", target_language="ar", extractor_version="fake@1")
    for source, kind, freq, proposals in [
        ("hegemony", "term", 9, [("الهيمنة", "gateway_proposer")]),
        ("probability", "term", 4, [("احتمال", "wikidata"), ("الاحتمالية", "gateway_proposer")]),
        ("articulation", "term", 3, [("صياغة", "gateway_proposer")]),
        ("July", "person", 2, [("يوليو", "transliteration")]),
        ("Gramsci", "person", 7, [("غرامشي", "transliteration")]),
        ("rare word", "term", 1, [("كلمة نادرة", "gateway_proposer")]),
    ]:
        tb.add_candidate(TermCandidate(source=source, kind=kind, frequency=freq, sections=1),
                         [Proposal(target=t, provenance=p) for t, p in proposals])
    s.merge_extraction(SCOPE, tb)
    return s


class ScriptedVoter:
    def __init__(self, verdicts, fail=()):
        self.verdicts, self.fail, self.seen = verdicts, set(fail), []

    def review(self, record):
        self.seen.append(record.source)
        if record.source in self.fail:
            raise RuntimeError("judge unavailable")
        tier, choice, target, reason = self.verdicts[record.source]
        return TermVerdict(tier=tier, choice=choice, target=target, reason=reason, model="deepseek/v4-flash")


VERDICTS = {"hegemony": ("locked", 1, None, "central concept"),
            "probability": ("locked", 1, None, "established term"),
            "articulation": ("locked", None, "التمفصل", "Gramscian term"),
            "July": ("dropped", None, None, "month name"),
            "Gramsci": ("locked", 1, None, "person"),
            "rare word": ("hint", 1, None, "general")}


def by_source(store):
    return {e.source: e for e in store.entries(SCOPE)}


def test_every_blocking_entry_is_decided_and_the_gate_opens(store):
    voter = ScriptedVoter(VERDICTS)
    report = review_by_models(store, SCOPE, voter, min_frequency=2)
    assert sorted(voter.seen) == ["Gramsci", "July", "articulation", "hegemony", "probability"]  # freq >= 2 only
    e = by_source(store)
    assert (e["hegemony"].status, e["hegemony"].target, e["hegemony"].tier) == ("approved", "الهيمنة", None)
    assert (e["probability"].target, e["probability"].tier) == ("احتمال", None)
    assert (e["articulation"].target, e["articulation"].tier) == ("التمفصل", "hint")
    assert (e["Gramsci"].target, e["Gramsci"].tier) == ("غرامشي", "hint")
    assert e["July"].status == "rejected" and "month name" in e["July"].rejection_reason
    assert e["rare word"].status == "pending"
    assert all(e[s].reviewer == "ai-vote-deepseek-v4-flash" for s in VERDICTS if s != "rare word")
    assert gate_counts(store.entries(SCOPE), 2).pending_blocking == 0
    assert report["counts"] == {"locked": 2, "hint": 2, "rejected": 1, "judge_unavailable": 0}
    assert {d["source"]: d["agreement"] for d in report["decisions"]}["probability"] == "judge+wikidata"


def test_unavailable_judge_never_blocks_production(store):
    voter = ScriptedVoter(VERDICTS, fail={"hegemony"})
    report = review_by_models(store, SCOPE, voter, min_frequency=2)
    e = by_source(store)
    assert (e["hegemony"].status, e["hegemony"].tier, e["hegemony"].reviewer) == ("approved", "hint",
                                                                                  "ai-vote-unavailable")
    assert report["counts"]["judge_unavailable"] == 1


def test_a_target_already_locked_elsewhere_becomes_a_shared_hint(store):
    approve_terms(store, SCOPE, [TermSelection("rare word", "الهيمنة")], "ai-vote-earlier", allow_shared_target=True)
    review_by_models(store, SCOPE, ScriptedVoter(VERDICTS), min_frequency=2)
    e = by_source(store)
    assert e["hegemony"].status == "approved" and e["hegemony"].target == "الهيمنة"


def test_decided_entries_are_left_alone(store):
    approve_terms(store, SCOPE, [TermSelection("hegemony", "الهيمنة")], "aly")
    voter = ScriptedVoter(VERDICTS)
    review_by_models(store, SCOPE, voter, min_frequency=2)
    assert "hegemony" not in voter.seen and by_source(store)["hegemony"].reviewer == "aly"


# ------------------------------------------------------------------ the activity
import json  # noqa: E402

import boto3  # noqa: E402
from b2c_contracts import ArtifactRef  # noqa: E402
from b2c_core import ArtifactStore  # noqa: E402
from moto import mock_aws  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402

from b2c_orchestration.translate_book import ReviewTermbaseInput, TranslateBookActivities  # noqa: E402


class VoterRuntime:
    def __init__(self, voter):
        self.voter, self.asked = voter, []

    def term_voter(self, profile_id):
        self.asked.append(profile_id)
        return self.voter


def review_input(**kw):
    data = dict(run_id="f" * 64, course_id="c1", book_id="bk", source_language="en", target_language="ar",
                min_frequency=2, judge_profile="deepseek-mqm")
    data.update(kw)
    return ReviewTermbaseInput(**data)


def test_activity_decides_and_stores_the_votes_as_evidence(store):
    with mock_aws():
        artifacts = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        artifacts.ensure_bucket()
        runtime = VoterRuntime(ScriptedVoter(VERDICTS))
        acts = TranslateBookActivities(artifact_store=artifacts, result_store=None, runtime=runtime,
                                       termbase_store=store, clock=lambda: NOW)
        out = acts.review_termbase(review_input())
        assert runtime.asked == ["deepseek-mqm"] and out.counts["locked"] == 2
        ref = ArtifactRef.model_validate(out.artifact)
        assert ref.stage == "book-termbase-votes"
        votes = json.loads(artifacts.read(ref, "votes.json"))
        assert votes["judge_profile"] == "deepseek-mqm" and len(votes["decisions"]) == 5
        again = acts.review_termbase(review_input())     # nothing pending: no new artifact
        assert again.artifact is None and sum(again.counts.values()) == 0


def test_activity_without_a_voting_profile_fails_non_retryable(store):
    acts = TranslateBookActivities(artifact_store=None, result_store=None, runtime=VoterRuntime(None),
                                   termbase_store=store)
    with pytest.raises(ApplicationError) as err:
        acts.review_termbase(review_input())
    assert err.value.non_retryable and err.value.type == "TermVoterNotConfigured"
