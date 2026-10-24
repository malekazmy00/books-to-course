from __future__ import annotations

import pytest
from b2c_contracts import StructuredChunk
from b2c_kg import (
    ConceptExtractionOutput,
    ConceptExtractionService,
    ExtractedEntity,
    ExtractedRelation,
    InMemoryConceptRelationStore,
)


class FakeGateway:
    model_id = "injected-test-model"

    def __init__(self, *outputs: ConceptExtractionOutput):
        self.outputs = list(outputs)
        self.calls = 0

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        assert schema is ConceptExtractionOutput
        assert prompt_version == "concept-extraction-v4-explicit-relation-cues"
        self.calls += 1
        return self.outputs.pop(0)


def chunk(chunk_id: str, book_id: str = "book-primary", role: str = "spine",
          pages: list[int] | None = None, text: str = "Probability applies to outcomes and connects outcomes to likelihood.") -> StructuredChunk:
    return StructuredChunk(
        chunk_id=chunk_id,
        book_id=book_id,
        role=role,
        section_path=["Probability"],
        block_ids=[f"{book_id}:block:1"],
        pages=pages or [4, 5],
        text=text,
        token_count=7,
        embedding_model="none",
    )


def output(quote: str = "Probability applies to outcomes and connects outcomes to likelihood.") -> ConceptExtractionOutput:
    return ConceptExtractionOutput(
        entities=[
            ExtractedEntity(name="probability", entity_type="concept", evidence_quote="Probability"),
            ExtractedEntity(name="outcomes", entity_type="concept", evidence_quote="outcomes"),
        ],
        relations=[
            ExtractedRelation(
                source_name="probability",
                source_type="concept",
                target_name="outcomes",
                target_type="concept",
                relation_type="applies_to",
                evidence_quote=quote,
            )
        ],
    )


def test_extract_returns_model_entities_with_chunk_derived_provenance():
    store = InMemoryConceptRelationStore()
    result = ConceptExtractionService(FakeGateway(output()), store, scope_id="course-a").extract(
        [chunk("chunk-1", book_id="primary-book", pages=[9, 8, 9])]
    )

    assert result.chunks_processed == 1
    assert {entity.name for entity in result.entities} == {"probability", "outcomes"}
    assert all(item.book_id == "primary-book" and item.role == "spine" and item.pages == (8, 9)
               for entity in result.entities for item in entity.evidence)
    [relation] = store.list_relations("course-a")
    assert relation.edge.src_entity == "concept:probability"
    assert relation.edge.dst_entity == "concept:outcomes"
    assert relation.edge.classifier == {
        "model": "injected-test-model",
        "prompt_version": "concept-extraction-v4-explicit-relation-cues",
    }
    assert relation.edge.evidence_chunk_ids == ["chunk-1"]
    assert relation.evidence[0].book_id == "primary-book"
    assert relation.evidence[0].pages == (8, 9)
    assert relation.evidence[0].role == "spine"


@pytest.mark.parametrize(
    "bad_output, reason_code",
    [
        (
            ConceptExtractionOutput(
                entities=[ExtractedEntity(name="variance", entity_type="concept",
                                           evidence_quote="Probability")],
                relations=[],
            ),
            "entity_name_not_in_quote",
        ),
        (
            ConceptExtractionOutput(
                entities=[ExtractedEntity(name="probability", entity_type="concept",
                                           evidence_quote="probability theory")],
                relations=[],
            ),
            "entity_quote_not_exact_source",
        ),
        (output("Probability connects outcomes to chance."), "relation_quote_not_exact_source"),
        (
            ConceptExtractionOutput(
                entities=[ExtractedEntity(name="probability", entity_type="concept",
                                           evidence_quote="Probability")],
                relations=[ExtractedRelation(
                    source_name="probability", source_type="concept",
                    target_name="outcomes", target_type="concept",
                    relation_type="applies_to",
                    evidence_quote="Probability applies to outcomes",
                )],
            ),
            "relation_endpoint_not_accepted_entity",
        ),
        (
            ConceptExtractionOutput(
                entities=[
                    ExtractedEntity(name="probability", entity_type="concept", evidence_quote="Probability"),
                    ExtractedEntity(name="outcomes", entity_type="concept", evidence_quote="outcomes"),
                ],
                relations=[ExtractedRelation(
                    source_name="probability", source_type="concept",
                    target_name="outcomes", target_type="concept",
                    relation_type="defines", evidence_quote="Probability applies to outcomes",
                )],
            ),
            "relation_type_not_explicit_in_quote",
        ),
        (
            ConceptExtractionOutput(
                entities=[
                    ExtractedEntity(name="probability", entity_type="concept", evidence_quote="Probability"),
                    ExtractedEntity(name="outcomes", entity_type="concept", evidence_quote="outcomes"),
                    ExtractedEntity(name="likelihood", entity_type="concept", evidence_quote="likelihood"),
                ],
                relations=[ExtractedRelation(
                    source_name="probability", source_type="concept",
                    target_name="likelihood", target_type="concept",
                    relation_type="applies_to", evidence_quote="Probability applies to outcomes",
                )],
            ),
            "relation_target_not_in_quote",
        ),
    ],
)
def test_invalid_items_are_rejected_with_sanitized_reason_codes(bad_output, reason_code):
    store = InMemoryConceptRelationStore()
    result = ConceptExtractionService(FakeGateway(bad_output), store, scope_id="course-a").extract(
        [chunk("chunk-1")]
    )
    assert [(item.item_kind, item.reason_code) for item in result.rejections] == [
        ("entity", reason_code)
    ] if reason_code.startswith("entity_") else [("relation", reason_code)]
    assert result.rejections[0].chunk_id == "chunk-1"
    assert result.rejections[0].item_index == 0
    assert "Probability applies to outcomes and connects outcomes to likelihood." not in repr(result.rejections)
    assert store.list_relations("course-a") == []


def test_valid_items_survive_neighboring_rejections_without_source_text_in_telemetry():
    good = output()
    mixed = ConceptExtractionOutput(
        entities=[
            ExtractedEntity(name="bad-name", entity_type="concept", evidence_quote="Probability"),
            *good.entities,
        ],
        relations=[
            *good.relations,
            ExtractedRelation(
                source_name="probability", source_type="concept", target_name="likelihood",
                target_type="concept", relation_type="applies_to",
                evidence_quote="Probability applies to outcomes",
            ),
        ],
    )
    store = InMemoryConceptRelationStore()
    result = ConceptExtractionService(FakeGateway(mixed), store, scope_id="course-a").extract([chunk("chunk-1")])
    assert {item.name for item in result.entities} == {"probability", "outcomes"}
    assert len(result.relations) == 1
    assert [(item.item_kind, item.item_index, item.reason_code) for item in result.rejections] == [
        ("entity", 0, "entity_name_not_in_quote"),
        ("relation", 1, "relation_target_not_in_quote"),
    ]
    assert "Probability applies to outcomes and connects outcomes to likelihood." not in repr(result.rejections)


def test_invalid_chunk_items_are_reported_and_valid_other_chunk_is_written():
    good = output()
    bad = output("not present in the second source")
    store = InMemoryConceptRelationStore()
    gateway = FakeGateway(good, bad)
    chunks = [
        chunk("chunk-a", text="Probability applies to outcomes and connects outcomes to likelihood."),
        chunk("chunk-b", book_id="book-support", role="supplementary",
              text="A different explanation with no matching quote."),
    ]

    result = ConceptExtractionService(gateway, store, scope_id="course-a").extract(chunks)
    assert gateway.calls == 2
    assert len(result.relations) == 1
    assert {(item.chunk_id, item.reason_code) for item in result.rejections} == {
        ("chunk-b", "entity_quote_not_exact_source"),
        ("chunk-b", "entity_quote_not_exact_source"),
        ("chunk-b", "relation_quote_not_exact_source"),
    }
    assert store.list_relations("course-a")[0].edge.evidence_chunk_ids == ["chunk-a"]


def test_repeated_extraction_merges_relation_evidence_idempotently():
    store = InMemoryConceptRelationStore()
    gateway = FakeGateway(output(), output())
    svc = ConceptExtractionService(gateway, store, scope_id="course-a")
    source = chunk("same-chunk", book_id="support-book", role="supplementary", pages=[13])

    first = svc.extract([source])
    second = svc.extract([source])
    assert first.relations == second.relations
    [relation] = store.list_relations("course-a")
    assert relation.edge.evidence_chunk_ids == ["same-chunk"]
    assert relation.evidence[0].book_id == "support-book"
    assert relation.evidence[0].role == "supplementary"
    assert relation.evidence[0].pages == (13,)


def test_same_extraction_is_isolated_between_required_scopes():
    store = InMemoryConceptRelationStore()
    source = chunk("chunk-1")
    ConceptExtractionService(FakeGateway(output()), store, scope_id="course-a").extract([source])
    ConceptExtractionService(FakeGateway(output()), store, scope_id="course-b").extract([source])

    assert len(store.list_relations("course-a")) == 1
    assert len(store.list_relations("course-b")) == 1
    assert store.list_relations("course-a")[0].scope_id == "course-a"
    assert store.list_relations("course-b")[0].scope_id == "course-b"


def test_empty_scope_is_rejected():
    with pytest.raises(ValueError, match="scope_id"):
        ConceptExtractionService(FakeGateway(), InMemoryConceptRelationStore(), scope_id=" ")


class LLMOutputTruncated(Exception):
    pass


class TruncatedGateway:
    model_id = "injected-test-model"

    def structured(self, *args, **kwargs):
        raise LLMOutputTruncated("this test message must not be recorded")


def test_chunk_level_model_failure_is_sanitized_and_remaining_chunks_reported():
    service = ConceptExtractionService(TruncatedGateway(), InMemoryConceptRelationStore(), scope_id="course-a")
    result = service.extract([chunk("chunk-a"), chunk("chunk-b")])
    assert result.chunks_processed == 0
    assert result.chunks_skipped == 1
    assert [(item.chunk_id, item.item_kind, item.item_index, item.reason_code) for item in result.rejections] == [
        ("chunk-a", "chunk", None, "model_output_truncated")
    ]
    assert "this test message must not be recorded" not in repr(result.rejections)
