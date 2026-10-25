"""KnowledgeDossier (knowledge research, EN) and ConceptLayer (SCHEMA 1.7.0; D-032..D-035).

Invariants the contract itself enforces (negative controls): provenance on every claim, the
"two sources or one tier-1" rule for facts, attributed viewpoints for conflicts, an independent
judge family, grounded author-voice statements, an acyclic prerequisite graph and external notes
kept apart from the author's voice."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def test_schema_version_bumped_for_knowledge_and_concepts():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 7, 0)


# ---------------------------------------------------------------- dossier
def source(n: int, tier: int = 1, provider: str = "wikipedia") -> c.ResearchSource:
    return c.ResearchSource(source_id=f"src_{n:016x}", url=f"https://example.org/{n}", provider=provider,
                            title=f"S{n}", tier=tier, fetched_at=NOW, content_sha256="a" * 64, text_chars=100)


def ev(src: c.ResearchSource, *, found=True, supports=True, quote="quoted text") -> c.Evidence:
    return c.Evidence(source_id=src.source_id, url=src.url, quote=quote, tier=src.tier, fetched_at=src.fetched_at,
                      quote_found=found, supports=supports, judged_by="judge@x")


def claim(evidence, *, n=1, status="verified", kind="fact", section="author", **kw) -> c.DossierClaim:
    return c.DossierClaim(claim_id=f"clm_{n:012x}", section=section, statement="Heuer worked at the CIA.",
                          kind=kind, evidence=evidence, status=status, **kw)


def ident(model: str) -> c.ProducerIdentity:
    return c.ProducerIdentity(role="x", provider="openrouter", model_id=model, prompt_version="p1")


def dossier(sources, claims, **kw) -> c.KnowledgeDossier:
    base = dict(book_id="b", title="T", authors=["A"], created_at=NOW, queries=["q"], sources=sources,
                claims=claims, extractor=ident("deepseek/x"), judge=ident("google/y"),
                extractor_family="deepseek", judge_family="google")
    return c.KnowledgeDossier(**(base | kw))


def test_tier1_single_source_fact_is_verified():
    s = source(1, tier=1)
    d = dossier([s], [claim([ev(s)])])
    assert [x.claim_id for x in d.published()] == ["clm_000000000001"] and d.language == "en"


def test_tier2_fact_needs_two_distinct_sources():
    a, b = source(1, tier=2), source(2, tier=3)
    with pytest.raises(ValidationError):
        claim([ev(a)])
    with pytest.raises(ValidationError):
        claim([ev(a), ev(a, quote="other")])  # same source twice is not corroboration
    assert claim([ev(a), ev(b)]).status == "verified"


def test_published_evidence_must_be_found_and_supporting():
    s = source(1)
    with pytest.raises(ValidationError):
        claim([ev(s, found=False)])
    with pytest.raises(ValidationError):
        claim([ev(s, supports=None)])
    with pytest.raises(ValidationError):
        claim([ev(s, supports=False)], status="attributed", kind="viewpoint", attributed_to="X")
    rejected = claim([ev(s, found=False)], status="rejected", status_reason="quote not in source")
    assert rejected.status == "rejected"
    with pytest.raises(ValidationError):
        claim([ev(s, found=False)], status="rejected")  # needs a reason


def test_viewpoints_are_attributed_never_verified():
    s = source(1, tier=2)
    with pytest.raises(ValidationError):
        claim([ev(s)], kind="viewpoint")  # a viewpoint cannot be a verified fact
    with pytest.raises(ValidationError):
        claim([ev(s)], kind="viewpoint", status="attributed")  # needs attributed_to
    ok = claim([ev(s)], kind="viewpoint", status="attributed", attributed_to="A reviewer in J. Hist.",
               section="reception")
    assert ok.attributed_to


def test_conflicting_claims_stay_attributed():
    s = source(1, tier=1)
    with pytest.raises(ValidationError):
        claim([ev(s)], conflict_group="cf1")  # verified + conflict
    ok = claim([ev(s)], status="attributed", attributed_to="S1", conflict_group="cf1")
    assert ok.conflict_group == "cf1"


def test_concept_in_field_names_its_concept():
    s = source(1)
    with pytest.raises(ValidationError):
        claim([ev(s)], section="concept_in_field")
    assert claim([ev(s)], section="concept_in_field", concept="hegemony").concept == "hegemony"


def test_dossier_evidence_must_point_to_a_listed_source_with_same_provenance():
    s = source(1)
    with pytest.raises(ValidationError):
        dossier([], [claim([ev(s)])])
    other = s.model_copy(update={"tier": 2})
    with pytest.raises(ValidationError):
        dossier([other], [claim([ev(s)])])  # tier differs from the source record


def test_judge_family_must_differ_from_extractor():
    s = source(1)
    with pytest.raises(ValidationError):
        dossier([s], [claim([ev(s)])], judge_family="deepseek")


def test_source_urls_are_https_and_ids_unique():
    with pytest.raises(ValidationError):
        c.ResearchSource(source_id="src_" + "0" * 16, url="http://x.org", provider="web", tier=3, fetched_at=NOW,
                         content_sha256="a" * 64, text_chars=1)
    s = source(1)
    with pytest.raises(ValidationError):
        dossier([s, s], [])


# ---------------------------------------------------------------- concept layer
def span(block="b:1:0", quote="Hegemony is consent armoured by coercion.") -> c.BookSpan:
    return c.BookSpan(block_id=block, pages=[3], quote=quote)


def concept(n: int, term: str, **kw) -> c.ConceptEntry:
    base = dict(concept_id=f"cpt_{n:012x}", term=term, kind="term", termbase_status="approved",
                introduced_at=span(), first_lesson_id="m1.l1", occurrences=3)
    return c.ConceptEntry(**(base | kw))


def layer(concepts, lessons=(), **kw) -> c.ConceptLayer:
    base = dict(book_id="b", created_at=NOW, termbase_basis="approved", concepts=list(concepts),
                lessons=list(lessons))
    return c.ConceptLayer(**(base | kw))


def rel(target: str, relation="prerequisite") -> c.ConceptRelation:
    return c.ConceptRelation(target_id=target, relation=relation, evidence=[span()])


def test_concept_layer_minimal_and_lookup():
    a = concept(1, "hegemony", author_definition=c.AuthorDefinition(spans=[span()], gloss="consent + force"))
    lay = layer([a], [c.LessonConceptList(lesson_id="m1.l1", title="L1",
                                          first_mentions=[c.LessonMention(concept_id=a.concept_id, block_id="b:1:0")])])
    assert lay.language == "en" and lay.concept(a.concept_id).term == "hegemony"


def test_author_statements_need_spans():
    with pytest.raises(ValidationError):
        c.AuthorDefinition(spans=[])
    with pytest.raises(ValidationError):
        c.ConceptRelation(target_id="cpt_000000000002", relation="related", evidence=[])
    with pytest.raises(ValidationError):
        c.BookSpan(block_id="b:1:0", pages=[1], quote="  ")


def test_relation_targets_exist_and_not_self():
    a = concept(1, "a", relations=[rel("cpt_000000000009")])
    with pytest.raises(ValidationError):
        layer([a])
    selfish = concept(1, "a", relations=[rel("cpt_000000000001")])
    with pytest.raises(ValidationError):
        layer([selfish])


def test_prerequisite_graph_is_acyclic():
    a = concept(1, "a", relations=[rel("cpt_000000000002")])
    b = concept(2, "b", relations=[rel("cpt_000000000001")])
    with pytest.raises(ValidationError):
        layer([a, b])
    b_related = concept(2, "b", relations=[rel("cpt_000000000001", "related")])
    assert len(layer([a, b_related]).concepts) == 2


def test_novelty_claims_need_external_notes():
    with pytest.raises(ValidationError):
        concept(1, "a", novelty="author_novel")
    note = c.ExternalConceptNote(claim_id="clm_000000000001", statement="Coined by Gramsci.",
                                 urls=["https://en.wikipedia.org/wiki/Cultural_hegemony"])
    ok = concept(1, "a", novelty="established", external_notes=[note])
    assert ok.external_notes[0].voice == "external"


def test_lesson_mentions_reference_known_concepts_once():
    a = concept(1, "a")
    m = c.LessonMention(concept_id=a.concept_id, block_id="b:1:0")
    with pytest.raises(ValidationError):
        layer([a], [c.LessonConceptList(lesson_id="l", title="L",
                                        first_mentions=[c.LessonMention(concept_id="cpt_000000000009",
                                                                        block_id="b:1:0")])])
    with pytest.raises(ValidationError):
        layer([a], [c.LessonConceptList(lesson_id="l", title="L", first_mentions=[m, m])])


def test_concept_ids_and_terms_unique():
    with pytest.raises(ValidationError):
        layer([concept(1, "a"), concept(1, "b")])
    with pytest.raises(ValidationError):
        layer([concept(1, "Hegemony"), concept(2, "hegemony")])
