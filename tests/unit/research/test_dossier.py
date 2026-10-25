"""Claim extraction with quote spans, the post-generation citation validator (quote exists + an
independent judge of another family), the fact rule, conflicts kept as attributed viewpoints,
injected instructions never becoming claims, and the dossier gates."""
from __future__ import annotations

import json

import pytest

import b2c_contracts as c
from b2c_research.claims import ModelRole
from b2c_research.dossier import build_dossier
from b2c_research.extract import html_to_text
from b2c_research.gates import CITATIONS_GATE_ID, TIER_GATE_ID, evaluate_dossier
from b2c_research.meta import BookMeta
from b2c_research.sources import SourceBuilder
from tests.unit.research.fakes import NOW, ScriptedLLM
from tests.unit.research.test_safety import FIXTURES

META = BookMeta(title="From Subordination to Revolution", authors=("John Chalcraft",), year=2025)


def ident(model):
    return c.ProducerIdentity(role="x", provider="openrouter", model_id=model, prompt_version="v")


def sources():
    b = SourceBuilder(max_chars=20_000)
    wiki = b.build(url="https://en.wikipedia.org/wiki/John_Chalcraft", provider="wikipedia", title="John Chalcraft",
                   tier=1, fetched_at=NOW, raw_text="John Chalcraft is a British historian. He is a professor at "
                   "the London School of Economics. He was born in 1970.", section_hint="author")
    review = b.build(url="https://www.jstor.org/stable/1", provider="web:test", title="Review", tier=2,
                     fetched_at=NOW, raw_text=html_to_text((FIXTURES / "review_with_injection.html").read_text()),
                     section_hint="reception")
    blog = b.build(url="https://blog.example.net/p", provider="web:test", title="Blog", tier=3, fetched_at=NOW,
                   raw_text="The book was published in 2025 by University of California Press. "
                            "Chalcraft was born in 1968.", section_hint="book_card")
    blog2 = b.build(url="https://other.example.net/q", provider="web:test", title="Blog 2", tier=3,
                    fetched_at=NOW, raw_text="University of California Press published the book in 2025.",
                    section_hint="book_card")
    return b, [wiki, review, blog, blog2]


def extractor_script(pv, msg):
    """Plays a fallible extractor: it 'obeys' the injected instruction and hallucinates a quote."""
    assert pv.startswith("kr-extract")
    if "wikipedia.org" in msg:
        return {"claims": [
            {"section": "author", "kind": "fact", "statement": "Chalcraft is a professor at the LSE.",
             "quote": "He is a professor at the London School of Economics."},
            {"section": "author", "kind": "fact", "statement": "Chalcraft was born in 1970.",
             "quote": "He was born in 1970."}]}
    if "jstor.org" in msg:
        assert "Nobel" not in msg  # injected sentence never reaches the model
        return {"claims": [
            {"section": "reception", "kind": "viewpoint", "statement": "A reviewer finds the consent chapter the most original.",
             "quote": "The reviewer finds the chapter on consent the most original part of the book.",
             "attributed_to": "a reviewer (JSTOR)"},
            {"section": "author", "kind": "fact", "statement": "The author won the Nobel Prize in Literature in 2019.",
             "quote": "the author won the Nobel Prize in Literature in 2019"},
            {"section": "reception", "kind": "fact", "statement": "The book sold ten million copies.",
             "quote": "the book sold ten million copies"}]}
    if "blog.example.net" in msg:
        return {"claims": [
            {"section": "book_card", "kind": "fact", "statement": "The book was published in 2025 by UC Press.",
             "quote": "The book was published in 2025 by University of California Press."},
            {"section": "author", "kind": "fact", "statement": "Chalcraft was born in 1968.",
             "quote": "Chalcraft was born in 1968."}]}
    if "other.example.net" in msg:
        return {"claims": [{"section": "book_card", "kind": "fact", "statement": "UC Press published the book in 2025.",
                            "quote": "University of California Press published the book in 2025."}]}
    return {"claims": []}


def judge_script(pv, msg):
    data = json.loads(msg[msg.index("{"):])
    if pv.startswith("kr-judge-support"):
        return {"verdicts": [{"id": it["id"], "supports": True} for it in data["items"]]}
    if pv.startswith("kr-judge-corroborate"):
        return {"supporting_ids": [x["id"] for x in data["candidates"] if "2025" in x["quote"]]}
    if pv.startswith("kr-judge-conflict"):
        facts = data["facts"]
        born = [f["id"] for f in facts if "born" in f["statement"]]
        return {"conflicts": [born] if len(born) == 2 else []}
    raise AssertionError(pv)


def build(judge_family="google", **kw):
    b, srcs = sources()
    extractor = ModelRole(ScriptedLLM(extractor_script, "deepseek/x"), ident("deepseek/x"), "deepseek")
    judge = ModelRole(ScriptedLLM(judge_script, "google/y"), ident("google/y"), judge_family)
    return build_dossier(book_id="b", meta=META, sources=srcs, extractor=extractor, judge=judge,
                         queries=["q1"], rejected=list(b.rejected), clock=lambda: NOW, workers=1, **kw)


def test_dossier_end_to_end_rules():
    d = build()
    by = {x.statement: x for x in d.claims}
    assert by["Chalcraft is a professor at the LSE."].status == "verified"  # tier-1 single source
    assert by["A reviewer finds the consent chapter the most original."].status == "attributed"
    nobel = by["The author won the Nobel Prize in Literature in 2019."]
    assert nobel.status == "rejected" and not nobel.evidence[0].quote_found
    assert by["The book sold ten million copies."].status == "rejected"
    # tier-3 facts: corroborated by a second distinct source -> verified
    uc = by["The book was published in 2025 by UC Press."]
    assert uc.status == "verified" and len({e.source_id for e in uc.evidence}) == 2
    # conflicting birth years are never merged into a fact
    born = [x for x in d.claims if "born" in x.statement and x.status != "rejected"]
    assert born and all(x.status == "attributed" and x.conflict_group for x in born)
    assert len({x.conflict_group for x in born}) == 1
    assert any(r.stage == "injection" for r in d.rejected)
    m = d.metrics
    assert m["raw_claims"] == 8 and m["quote_found"] == 6 and 0 < m["citation_support_rate"] < 1
    assert m["published_claims"] == len(d.published())
    assert set(m["source_tiers"]) == {"1", "2", "3"}


def test_judge_rejection_and_unjudged_quotes_are_not_published():
    def stingy(pv, msg):
        data = json.loads(msg[msg.index("{"):])
        if pv.startswith("kr-judge-support"):
            return {"verdicts": [{"id": it["id"], "supports": False} for it in data["items"]]}
        return judge_script(pv, msg)
    b, srcs = sources()
    d = build_dossier(book_id="b", meta=META, sources=srcs,
                      extractor=ModelRole(ScriptedLLM(extractor_script), ident("deepseek/x"), "deepseek"),
                      judge=ModelRole(ScriptedLLM(stingy), ident("google/y"), "google"),
                      queries=[], rejected=[], clock=lambda: NOW, workers=1)
    assert d.published() == [] and d.metrics["citation_support_rate"] == 0


def test_searched_pages_must_be_judged_relevant_before_extraction():
    b = SourceBuilder(max_chars=20_000)
    helmet = b.build(url="https://en.wikipedia.org/wiki/Advanced_Combat_Helmet", provider="wikipedia",
                     title="Advanced Combat Helmet", tier=1, fetched_at=NOW, raw_text="The ACH is a combat helmet.",
                     section_hint="concept_in_field", concept="ACH", purpose="concept in field: ACH")
    ach = b.build(url="https://en.wikipedia.org/wiki/Analysis_of_competing_hypotheses", provider="wikipedia",
                  title="Analysis of competing hypotheses", tier=1, fetched_at=NOW,
                  raw_text="ACH was developed by Richards J. Heuer.", section_hint="concept_in_field", concept="ACH",
                  purpose="concept in field: ACH")
    asked = []

    def judge(pv, msg):
        data = json.loads(msg[msg.index("{"):])
        if pv.startswith("kr-judge-relevance"):
            asked.extend(p["page_title"] for p in data["pages"])
            return {"verdicts": [{"id": p["id"], "relevant": "competing" in p["page_title"]} for p in data["pages"]]}
        return judge_script(pv, msg)

    extracted = []

    def extractor(pv, msg):
        extracted.append(msg)
        return {"claims": [{"section": "concept_in_field", "kind": "fact", "concept": "ACH",
                            "statement": "ACH was developed by Heuer.", "quote": "ACH was developed by Richards J. Heuer."}]}

    d = build_dossier(book_id="b", meta=META, sources=[helmet, ach],
                      extractor=ModelRole(ScriptedLLM(extractor), ident("deepseek/x"), "deepseek"),
                      judge=ModelRole(ScriptedLLM(judge), ident("google/y"), "google"),
                      queries=[], rejected=[], clock=lambda: NOW, workers=1)
    assert sorted(asked) == ["Advanced Combat Helmet", "Analysis of competing hypotheses"]
    assert len(extracted) == 1 and "combat helmet" not in extracted[0]
    assert [s.title for s in d.sources] == ["Analysis of competing hypotheses"]
    assert any(r.stage == "validation" and "Advanced_Combat_Helmet" in r.url for r in d.rejected)
    assert d.metrics["irrelevant_pages"] == 1


def test_same_family_judge_is_refused():
    with pytest.raises(ValueError):
        build(judge_family="deepseek")


def test_extractor_prompt_marks_source_text_as_untrusted_data():
    b, srcs = sources()
    llm = ScriptedLLM(extractor_script)
    build_dossier(book_id="b", meta=META, sources=srcs[:1], extractor=ModelRole(llm, ident("deepseek/x"), "deepseek"),
                  judge=ModelRole(ScriptedLLM(judge_script), ident("google/y"), "google"),
                  queries=[], rejected=[], clock=lambda: NOW, workers=1)
    system = llm.calls[0][1][0]["content"]
    assert "untrusted" in system.lower() and "never follow" in system.lower()
    assert "<source_text>" in llm.calls[0][1][-1]["content"]


def test_gates():
    d = build()
    gates = {g.gate_id: g for g in evaluate_dossier(d, min_published_claims=3)}
    assert set(gates) == {CITATIONS_GATE_ID, TIER_GATE_ID}
    assert gates[CITATIONS_GATE_ID].kind == "semantic" and gates[TIER_GATE_ID].kind == "deterministic"
    assert gates[CITATIONS_GATE_ID].metrics["published_claims"] == len(d.published())
    assert gates[TIER_GATE_ID].metrics["tier1_sections"]["author"] is True
    assert gates[TIER_GATE_ID].metrics["tier1_sections"]["book_card"] is False
    assert gates[TIER_GATE_ID].passed is False  # no tier-1 book card claim
    assert evaluate_dossier(d, min_published_claims=50)[0].passed is False
