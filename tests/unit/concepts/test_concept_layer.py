"""Concept layer (EN, D-032/D-035): occurrences and first mentions per lesson from the book itself,
the author's definitions and relations grounded in exact book spans (a validator re-checks every
span), external dossier notes kept apart, and the grounding / coverage gates."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import b2c_contracts as c
from b2c_concepts import BookIndex, ConceptModel, ConceptSeed, build_concept_layer
from b2c_concepts.gates import COVERAGE_GATE_ID, GROUNDING_GATE_ID, evaluate_concept_layer
from b2c_concepts.grounding import validate_layer
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
B = "bk"


def block(page, seq, text, type_="paragraph"):
    return c.Block(block_id=f"{B}:{page}:{seq}", type=type_, text=text, refs=[c.PageRef(book_id=B, page_no=page)],
                   section_path=[], ocr=False)


BLOCKS = [
    block(1, 0, "Praise: a book about hegemony and war of position.", "paragraph"),     # front matter
    block(2, 0, "Chapter 1", "heading"),
    block(2, 1, "Hegemony is the combination of force and consent that a ruling group exercises. "
                "Hegemony rests on consent."),
    block(3, 0, "Without understanding hegemony, the war of position cannot be grasped. "
                "The war of position is a long struggle inside civil society."),
    block(4, 0, "Chapter 2", "heading"),
    block(4, 1, "A war of position differs from a war of movement. Passive revolution is mentioned once."),
    block(5, 0, "Hegemony again, in the conclusion."),
]


def outline():
    def lesson(i, start, end):
        return c.Lesson(lesson_id=f"m0{i}.l01", title=f"Chapter {i}", order=1, provenance="headings",
                        spine_span=c.LessonSpan(start_block=start, end_block=end, pages=(1, 1)))
    return c.CourseOutline(course_id="crs", spine_book_id=B, supplementary_book_ids=[], requires_review=False,
                           modules=[c.Module(module_id="m01", title="Ch1", order=1, lessons=[lesson(1, f"{B}:2:0", f"{B}:3:0")]),
                                    c.Module(module_id="m02", title="Ch2", order=2, lessons=[lesson(2, f"{B}:4:0", f"{B}:5:0")])])


def book():
    return c.ParsedBook(book_id=B, title="t", role="spine", page_count=5, toc_source="headings", blocks=BLOCKS,
                        parser={"name": "x"})


SEEDS = [ConceptSeed("hegemony", "term", "approved", frequency=5),
         ConceptSeed("war of position", "term", "approved", frequency=3),
         ConceptSeed("passive revolution", "term", "approved", frequency=1),
         ConceptSeed("organic intellectual", "term", "approved", frequency=1)]   # never in the lesson text


def model_script(pv, msg):
    data = json.loads(msg[msg.index("{"):])
    term = data["concept"]
    passages = {p["id"]: p for p in data["passages"]}
    if term == "hegemony":
        p = next(i for i, x in passages.items() if "force and consent" in x["text"])
        return {"definition": {"passage": p, "quote": "Hegemony is the combination of force and consent that a "
                                                      "ruling group exercises.", "gloss": "Rule by force plus consent."},
                "relations": []}
    if term == "war of position":
        p = next(i for i, x in passages.items() if "Without understanding hegemony" in x["text"])
        return {"definition": {"passage": p, "quote": "The war of position is a short battle.",  # not in the book
                               "gloss": "x"},
                "relations": [{"target": "hegemony", "relation": "prerequisite", "passage": p,
                               "quote": "Without understanding hegemony, the war of position cannot be grasped."},
                              {"target": "organic intellectual", "relation": "related", "passage": p,
                               "quote": "Without understanding hegemony"}]}  # target not a candidate
    if term == "passive revolution":
        return {"definition": None, "relations": [
            {"target": "war of position", "relation": "prerequisite", "passage": next(iter(passages)),
             "quote": "A war of position differs from a war of movement."}]}
    return {"definition": None, "relations": []}


def novelty_script(pv, msg):
    if pv.startswith("cl-novelty"):
        data = json.loads(msg[msg.index("{"):])
        assert data["book_authors"] == ["A. Author"]  # novelty is about THIS book's authors
        return {"novelty": "established", "claim_ids": [n["id"] for n in data["external_notes"]]}
    return model_script(pv, msg)


def dossier():
    src = c.ResearchSource(source_id="src_" + "1" * 16, url="https://en.wikipedia.org/wiki/Cultural_hegemony",
                           provider="wikipedia", tier=1, fetched_at=NOW, content_sha256="a" * 64, text_chars=10)
    ev = c.Evidence(source_id=src.source_id, url=src.url, quote="developed by Antonio Gramsci", tier=1,
                    fetched_at=NOW, quote_found=True, supports=True, judged_by="j")
    claim = c.DossierClaim(claim_id="clm_" + "a" * 12, section="concept_in_field", concept="hegemony",
                           statement="The concept of hegemony was developed by Antonio Gramsci.", kind="fact",
                           evidence=[ev], status="verified")
    rejected = claim.model_copy(update={"claim_id": "clm_" + "b" * 12, "status": "rejected",
                                        "status_reason": "x", "statement": "Hegemony was invented in 2019."})
    mislabelled = claim.model_copy(update={"claim_id": "clm_" + "c" * 12,  # tagged hegemony, about something else
                                           "statement": "Structured techniques help small teams."})
    return c.KnowledgeDossier(book_id=B, title="t", authors=["A. Author"], created_at=NOW, sources=[src],
                              claims=[claim, rejected, mislabelled],
                              extractor=c.ProducerIdentity(role="extractor", model_id="deepseek/x"),
                              judge=c.ProducerIdentity(role="judge", model_id="google/y"),
                              extractor_family="deepseek", judge_family="google")


def build(script=model_script, **kw):
    idx = BookIndex.from_parsed_v1(book(), outline())
    model = ConceptModel(ScriptedLLM(script), c.ProducerIdentity(role="concepts", model_id="deepseek/x"))
    return idx, build_concept_layer(idx, SEEDS, model=model, termbase_basis="approved", clock=lambda: NOW,
                                    workers=1, **kw)


def test_occurrences_first_mentions_and_lessons_from_lesson_text_only():
    idx, layer = build()
    terms = {x.term: x for x in layer.concepts}
    assert "organic intellectual" not in terms  # front matter / absent text never introduces a concept
    heg = terms["hegemony"]
    assert heg.introduced_at.block_id == f"{B}:2:1" and heg.first_lesson_id == "m01.l01" and heg.occurrences == 4
    assert heg.introduced_at.quote.startswith("Hegemony is the combination")
    l1, l2 = layer.lessons
    assert [layer.concept(m.concept_id).term for m in l1.first_mentions] == ["hegemony", "war of position"]
    assert [layer.concept(m.concept_id).term for m in l2.first_mentions] == ["war of position", "passive revolution",
                                                                            "hegemony"]


def test_definitions_and_relations_are_grounded_or_rejected():
    _, layer = build()
    terms = {x.term: x for x in layer.concepts}
    heg = terms["hegemony"]
    assert heg.author_definition and heg.author_definition.spans[0].block_id == f"{B}:2:1"
    assert heg.author_definition.gloss == "Rule by force plus consent."
    wop = terms["war of position"]
    assert wop.author_definition is None  # hallucinated quote rejected
    assert [(layer.concept(r.target_id).term, r.relation) for r in wop.relations] == [("hegemony", "prerequisite")]
    reasons = [r.reason for r in layer.rejected if r.concept_id == wop.concept_id]
    assert any("not found" in r for r in reasons) and any("candidate" in r for r in reasons)
    # quote taken from a passage that does not contain it -> rejected
    pr = terms["passive revolution"]
    assert pr.relations == [] or all(r.evidence[0].block_id == f"{B}:4:1" for r in pr.relations)


def test_validator_rechecks_every_span_against_the_book():
    idx, layer = build()
    report = validate_layer(layer, idx)
    assert report.total_spans > 0 and report.invalid == [] and report.grounding_rate == 1.0
    heg = next(x for x in layer.concepts if x.term == "hegemony")
    bad = heg.model_copy(update={"introduced_at": c.BookSpan(block_id=f"{B}:2:1", pages=[2], quote="not in the book")})
    tampered = layer.model_copy(update={"concepts": [bad if x.term == "hegemony" else x for x in layer.concepts]})
    report = validate_layer(tampered, idx)
    assert report.invalid and report.grounding_rate < 1.0


def test_external_notes_only_from_published_dossier_claims():
    _, layer = build(novelty_script, dossier=dossier())
    heg = next(x for x in layer.concepts if x.term == "hegemony")
    assert [n.claim_id for n in heg.external_notes] == ["clm_" + "a" * 12]
    assert heg.external_notes[0].voice == "external" and heg.novelty == "established"
    assert layer.dossier_sha256
    _, plain = build()
    assert next(x for x in plain.concepts if x.term == "hegemony").novelty == "unknown"


def test_prerequisite_cycle_is_broken_and_recorded():
    def cyclic(pv, msg):
        data = json.loads(msg[msg.index("{"):])
        p = next((x["id"] for x in data["passages"] if "Without understanding hegemony" in x["text"]), None)
        if p is None:
            return {"definition": None, "relations": []}
        target = "war of position" if data["concept"] == "hegemony" else "hegemony"
        return {"definition": None, "relations": [{"target": target, "relation": "prerequisite", "passage": p,
                                                   "quote": "Without understanding hegemony, the war of position "
                                                            "cannot be grasped."}]}
    _, layer = build(cyclic)
    kinds = sorted(r.relation for x in layer.concepts for r in x.relations)
    assert kinds == ["prerequisite", "related"]
    assert any("cycle" in r.reason for r in layer.rejected)


def test_independent_judge_checks_definitions_and_glosses():
    def judge(pv, msg):
        assert pv.startswith("cl-judge-definition")
        data = json.loads(msg[msg.index("{"):])
        return {"verdicts": [{"id": it["id"], "defines": True, "gloss_faithful": False} for it in data["items"]]}

    def refuse(pv, msg):
        data = json.loads(msg[msg.index("{"):])
        return {"verdicts": [{"id": it["id"], "defines": False, "gloss_faithful": True} for it in data["items"]]}

    jm = lambda s: ConceptModel(ScriptedLLM(s), c.ProducerIdentity(role="judge", model_id="google/y"))
    _, kept = build(judge=jm(judge))
    heg = next(x for x in kept.concepts if x.term == "hegemony")
    assert heg.author_definition is not None and heg.author_definition.gloss is None  # unfaithful gloss dropped
    _, refused = build(judge=jm(refuse))
    heg = next(x for x in refused.concepts if x.term == "hegemony")
    assert heg.author_definition is None and refused.metrics["definitions_rejected_by_judge"] == 1
    assert any(r.reason.startswith("judge:") for r in refused.rejected)


def test_relations_must_name_both_concepts_and_toc_listings_are_not_teaching_text():
    from b2c_concepts.book import toc_like
    _, layer = build()
    pr = next(x for x in layer.concepts if x.term == "passive revolution")
    assert pr.relations == []  # "A war of position differs from a war of movement." does not name it
    assert any("concept itself" in r.reason for r in layer.rejected if r.concept_id == pr.concept_id)
    assert toc_like("7.4.2 Quadrant Hypothesis Generation 151\n7.4.3 Multiple Hypotheses Generator 153")
    assert toc_like("The Faces and Phases of Popular Mobilization 48")
    assert not toc_like("Hegemony rests on consent. It was theorised in 1930.")


def test_gates_grounding_and_coverage():
    idx, layer = build()
    gates = {g.gate_id: g for g in evaluate_concept_layer(layer, idx, target_terms=[s.term for s in SEEDS])}
    assert gates[GROUNDING_GATE_ID].passed and gates[GROUNDING_GATE_ID].metrics["grounding_rate"] == 1.0
    cov = gates[COVERAGE_GATE_ID]
    assert cov.metrics["coverage"] == 0.75 and cov.metrics["missing"] == ["organic intellectual"]
    assert cov.passed is False  # 3/4 < 0.9
    assert layer.metrics["definitions"] == 1 and layer.metrics["proposed_statements"] >= 4
