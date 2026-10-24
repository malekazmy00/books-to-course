from __future__ import annotations

import pytest

from b2c_contracts import ConceptEdge
from b2c_kg.concepts import ConceptEvidence, InMemoryConceptRelationStore


def edge(*chunk_ids: str) -> ConceptEdge:
    return ConceptEdge(
        src_entity="concept:probability",
        dst_entity="concept:random-variable",
        type="prerequisite_of",
        evidence_chunk_ids=list(chunk_ids),
        confidence=0.91,
        classifier={"model": "test-fixture", "prompt_version": "unit-v1"},
    )


def evidence(chunk_id: str, book_id: str, page: int, role: str = "spine") -> ConceptEvidence:
    return ConceptEvidence(chunk_id, book_id, role, (page,))  # type: ignore[arg-type]


def test_create_and_retrieve_relation_retains_chunk_page_and_role() -> None:
    store = InMemoryConceptRelationStore()
    stored = store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    found = store.list_relations("course-a")
    assert found == [stored]
    assert found[0].edge.evidence_chunk_ids == ["chunk-a"]
    assert found[0].evidence == (evidence("chunk-a", "book-a", 12),)
    assert found[0].evidence[0].role == "spine"


def test_same_relation_merges_evidence_idempotently() -> None:
    store = InMemoryConceptRelationStore()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    [relation] = store.list_relations("course-a")
    assert relation.edge.evidence_chunk_ids == ["chunk-a", "chunk-b"]
    assert {item.book_id for item in relation.evidence} == {"book-a", "book-b"}


def test_scope_isolation_and_source_book_deletion() -> None:
    store = InMemoryConceptRelationStore()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    store.put("course-b", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    assert len(store.list_relations("course-a")) == 1
    assert len(store.list_relations("course-b")) == 1
    assert store.delete_book("course-a", "book-a") == 1
    assert store.list_relations("course-a") == []
    assert len(store.list_relations("course-b")) == 1


def test_deleting_one_support_preserves_edge_with_remaining_book_evidence() -> None:
    store = InMemoryConceptRelationStore()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    assert store.delete_book("course-a", "book-a") == 1
    [relation] = store.list_relations("course-a")
    assert relation.edge.evidence_chunk_ids == ["chunk-b"]
    assert [(item.book_id, item.pages) for item in relation.evidence] == [("book-b", (31,))]


@pytest.mark.parametrize(
    "args, message",
    [
        (("", "book-a", "spine", (1,)), "chunk_id"),
        (("chunk-a", "book-a", "spine", (0,)), "positive physical pages"),
        (("chunk-a", "book-a", "spine", (4, 4)), "unique"),
    ],
)
def test_invalid_evidence_is_rejected(args: tuple, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ConceptEvidence(*args)  # type: ignore[arg-type]


def test_edge_cannot_claim_missing_or_unreferenced_page_evidence() -> None:
    store = InMemoryConceptRelationStore()
    with pytest.raises(ValueError, match="absent from ConceptEdge"):
        store.put("course-a", edge("chunk-a"), [evidence("chunk-b", "book-a", 12)])
    with pytest.raises(ValueError, match="every ConceptEdge evidence_chunk_id"):
        store.put("course-a", edge("chunk-a", "chunk-b"), [evidence("chunk-a", "book-a", 12)])


def test_conflicting_relation_metadata_is_rejected() -> None:
    store = InMemoryConceptRelationStore()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    conflict = ConceptEdge(**{**edge("chunk-b").model_dump(), "confidence": 0.2})
    with pytest.raises(ValueError, match="metadata conflicts"):
        store.put("course-a", conflict, [evidence("chunk-b", "book-b", 4)])
def test_batch_relation_writes_are_atomic_when_a_later_write_conflicts():
    store = InMemoryConceptRelationStore()
    original = edge("chunk-original")
    store.put("course-a", original, [evidence("chunk-original", "book-a", 4)])
    before = store.list_relations("course-a")
    unrelated = ConceptEdge(
        src_entity="concept:new",
        dst_entity="concept:target",
        type="defines",
        evidence_chunk_ids=["chunk-new"],
        confidence=.7,
        classifier={"model": "unit", "prompt_version": "batch"},
    )
    conflicting = ConceptEdge(**{**edge("chunk-conflict").model_dump(), "confidence": .2})

    with pytest.raises(ValueError, match="metadata conflicts"):
        store.put_many("course-a", [
            (unrelated, [evidence("chunk-new", "book-b", 9)]),
            (conflicting, [evidence("chunk-conflict", "book-c", 12)]),
        ])

    assert store.list_relations("course-a") == before
