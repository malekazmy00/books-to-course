"""The language-research activity (companion queue): approved termbase -> language-research artifact."""
from __future__ import annotations

import json

import boto3
import pytest
from b2c_contracts import ArtifactRef
from b2c_core import ArtifactStore
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate
from moto import mock_aws

from b2c_orchestration.language_research import LanguageResearchActivities, LanguageResearchInput, language_terms


def termbase():
    tb = BookTermbase.new("bk", source_language="en", target_language="ar")
    rows = [("hegemony", "هيمنة", [("هيمنة", "wikidata", "wikidata-known-ar-v1: arwiki title; x")], None, 9),
            ("groupthink", "تفكير جماعي", [("تفكير جماعي", "gateway_proposer", None)], "hint", 4),
            ("articulation", "تمفصل", [("صياغة", "gateway_proposer", None)], "hint", 3)]
    for source, target, props, tier, freq in rows:
        tb.add_candidate(TermCandidate(source=source, kind="term", frequency=freq),
                         [Proposal(target=t, provenance=p, note=n) for t, p, n in props])
        tb.approve(source, target, "ai-vote-deepseek", tier=tier or "locked")
    return tb


def test_status_comes_from_where_the_equivalent_came_from():
    terms = {t.source: t for t in language_terms(termbase())}
    assert terms["hegemony"].established and terms["hegemony"].arabic_wikipedia.endswith("%D9%87%D9%8A%D9%85%D9%86%D8%A9")
    assert not terms["groupthink"].established        # two models agreeing is not an Arabic source
    assert not terms["articulation"].established


@pytest.fixture
def store():
    with mock_aws():
        s = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        s.ensure_bucket()
        yield s


def test_activity_stores_the_research_as_an_artifact(store):
    tb_ref = store.put("c1", "book-termbase-approved", "f" * 64, {"termbase.json": termbase().to_json().encode()})
    pages = {"تفكير جماعي": {"title": "تفكير جماعي", "wikibase_item": "Q1"}}
    acts = LanguageResearchActivities(artifact_store=store, page=pages.get, english=lambda q: ["groupthink"])
    out = acts.research(LanguageResearchInput(course_id="c1", termbase_ref=tb_ref.model_dump(mode="json")))
    ref = ArtifactRef.model_validate(out["ref"])
    assert ref.stage == "language-research" and out["metrics"]["not_established"] == 1
    doc = json.loads(store.read(ref, "language-research.json"))
    assert {t["source"]: t["status"] for t in doc["terms"]} == {
        "hegemony": "established", "groupthink": "found_in_arabic_wikipedia", "articulation": "not_established"}
    assert doc["book_id"] == "bk" and len(doc["read_in_arabic"]) == 2
