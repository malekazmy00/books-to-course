from __future__ import annotations

from b2c_contracts import ConceptEdge, StructuredChunk
from b2c_kg import ConceptEvidence, ConceptGraphRetriever, InMemoryConceptRelationStore


def edge(src: str, dst: str, chunk_id: str, *, rel_type: str = "prerequisite_of") -> ConceptEdge:
    return ConceptEdge(
        src_entity=src,
        dst_entity=dst,
        type=rel_type,  # type: ignore[arg-type]
        evidence_chunk_ids=[chunk_id],
        confidence=.9,
        classifier={"model": "unit", "prompt_version": "v1"},
    )


def citation(chunk_id: str, book_id: str, page: int, role: str = "spine") -> ConceptEvidence:
    return ConceptEvidence(chunk_id, book_id, role, (page,))  # type: ignore[arg-type]


def chunk(chunk_id: str, book_id: str, page: int, role: str = "spine") -> StructuredChunk:
    return StructuredChunk(
        chunk_id=chunk_id, book_id=book_id, role=role,
        section_path=["Chapter"], block_ids=[f"{book_id}:{page}:0"],
        pages=[page], text=f"content for {chunk_id}", token_count=4,
        embedding_model="none",
    )


def add_relation(store, scope, src, dst, chunk_id, book_id, page, role="spine"):
    store.put(scope, edge(src, dst, chunk_id), [citation(chunk_id, book_id, page, role)])


def test_exact_entity_match_returns_source_chunk_with_provenance():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:probability", "concept:random-variable",
                 "chunk-direct", "book-primary", 12)
    retriever = ConceptGraphRetriever(store, scope_id="course-a")
    result = retriever.search("Explain concept:probability.", [chunk("chunk-direct", "book-primary", 12)])

    assert len(result) == 1
    source, score = result[0]
    assert (source.chunk_id, source.book_id, source.role, source.pages, source.block_ids) == (
        "chunk-direct", "book-primary", "spine", [12], ["book-primary:12:0"])
    assert score == 1.0


def test_direct_entity_match_expands_one_hop_with_lower_deterministic_score():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:a", "concept:b",
                 "chunk-direct", "book-primary", 2)
    add_relation(store, "course-a", "concept:b", "concept:c",
                 "chunk-expanded", "book-support", 9, "supplementary")
    retriever = ConceptGraphRetriever(store, scope_id="course-a")
    result = retriever.search(
        "concept:a", [chunk("chunk-direct", "book-primary", 2),
                      chunk("chunk-expanded", "book-support", 9, "supplementary")])

    assert [(item.chunk_id, score) for item, score in result] == [
        ("chunk-direct", 1.0), ("chunk-expanded", .5)]


def test_unknown_entities_and_stale_evidence_ids_produce_no_result():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:known", "concept:other",
                 "missing-source-chunk", "book-primary", 4)
    retriever = ConceptGraphRetriever(store, scope_id="course-a")
    assert retriever.search("concept:unknown", []) == []
    # A matching entity cannot cite evidence that is absent from source chunks.
    assert retriever.search("concept:known", []) == []
    # Matching is exact; partial or embedded identifiers are not accepted.
    assert retriever.search("xconcept:known", [chunk("missing-source-chunk", "book-primary", 4)]) == []
    assert retriever.search("concept:known-extra", [chunk("missing-source-chunk", "book-primary", 4)]) == []


def test_identical_entity_ids_are_isolated_by_configured_scope():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:same", "concept:target",
                 "chunk-a", "book-a", 3)
    add_relation(store, "course-b", "concept:same", "concept:target",
                 "chunk-b", "book-b", 7)
    chunks = [chunk("chunk-a", "book-a", 3), chunk("chunk-b", "book-b", 7)]

    result_a = ConceptGraphRetriever(store, scope_id="course-a").search("concept:same", chunks)
    result_b = ConceptGraphRetriever(store, scope_id="course-b").search("concept:same", chunks)
    assert [item.chunk_id for item, _ in result_a] == ["chunk-a"]
    assert [item.chunk_id for item, _ in result_b] == ["chunk-b"]


def test_allowed_book_and_role_filters_apply_to_direct_and_expanded_evidence():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:a", "concept:b",
                 "spine-direct", "book-primary", 1, "spine")
    add_relation(store, "course-a", "concept:b", "concept:c",
                 "support-expanded", "book-support", 2, "supplementary")
    add_relation(store, "course-a", "concept:b", "concept:d",
                 "other-expanded", "book-other", 3, "spine")
    retriever = ConceptGraphRetriever(store, scope_id="course-a")

    result = retriever.search(
        "concept:a",
        [chunk("spine-direct", "book-primary", 1, "spine"),
         chunk("support-expanded", "book-support", 2, "supplementary"),
         chunk("other-expanded", "book-other", 3, "spine")],
        allowed_roles={"supplementary"}, allowed_book_ids={"book-support"},
    )
    assert [(item.chunk_id, score) for item, score in result] == [("support-expanded", .5)]


def test_scope_and_query_validation_fail_closed():
    store = InMemoryConceptRelationStore()
    try:
        ConceptGraphRetriever(store, scope_id=" ")
    except ValueError as exc:
        assert "scope_id" in str(exc)
    else:
        raise AssertionError("empty scope should be rejected")
    retriever = ConceptGraphRetriever(store, scope_id="course-a")
    try:
        retriever.search(" ", [])
    except ValueError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("empty query should be rejected")
def test_natural_question_matches_human_readable_entity_name():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:probability", "concept:random-variable",
                 "chunk-direct", "book-primary", 12)
    retriever = ConceptGraphRetriever(store, scope_id="course-a")

    result = retriever.search(
        "How does probability connect to other ideas?",
        [chunk("chunk-direct", "book-primary", 12)],
    )
    assert [item.chunk_id for item, _ in result] == ["chunk-direct"]


def test_full_typed_entity_id_still_matches():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:probability", "concept:random-variable",
                 "chunk-direct", "book-primary", 12)
    retriever = ConceptGraphRetriever(store, scope_id="course-a")

    result = retriever.search(
        "Explain concept:probability.",
        [chunk("chunk-direct", "book-primary", 12)],
    )
    assert [item.chunk_id for item, _ in result] == ["chunk-direct"]


def test_partial_human_name_does_not_match_a_longer_entity_name():
    store = InMemoryConceptRelationStore()
    add_relation(store, "course-a", "concept:probability", "concept:random-variable",
                 "chunk-direct", "book-primary", 12)
    retriever = ConceptGraphRetriever(store, scope_id="course-a")

    assert retriever.search("How does prob work?", [chunk("chunk-direct", "book-primary", 12)]) == []
    assert retriever.search("How does probabilityish work?", [chunk("chunk-direct", "book-primary", 12)]) == []
