"""Source-grounded, opt-in concept extraction over structured chunks."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from b2c_contracts import ConceptEdge, ConceptEdgeType, EntityType, StructuredChunk
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .concepts import (
    ConceptEvidence,
    ConceptRelationStore,
    EvidenceBackedRelation,
)

PROMPT_VERSION = "concept-extraction-v4-explicit-relation-cues"
# ConceptEdge requires a scalar confidence for the whole relation. Extraction
# confidence is not calibrated and varies by mention, so use a stable neutral
# sentinel rather than letting repeated/incremental writes conflict.
UNSCORED_CONFIDENCE = 0.5


class ExtractedEntity(BaseModel):
    """Entity mention and verbatim supporting text returned by a model."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=240)
    entity_type: EntityType
    evidence_quote: str = Field(min_length=1, max_length=1200)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("entity name must not be blank")
        return value


class ExtractedRelation(BaseModel):
    """Typed relation mention; source quote must occur verbatim in its chunk."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_name: str = Field(min_length=1, max_length=240)
    source_type: EntityType
    target_name: str = Field(min_length=1, max_length=240)
    target_type: EntityType
    relation_type: ConceptEdgeType
    evidence_quote: str = Field(min_length=1, max_length=1600)

    @field_validator("source_name", "target_name")
    @classmethod
    def _strip_names(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("relation entity names must not be blank")
        return value


class ConceptExtractionOutput(BaseModel):
    """Strict model response schema. It intentionally contains no provenance fields."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=100)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=100)


class StructuredModel(Protocol):
    model_id: str

    def structured(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        *,
        prompt_version: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> BaseModel: ...


@dataclass(frozen=True, slots=True)
class GroundedConceptEntity:
    entity_id: str
    name: str
    entity_type: EntityType
    evidence: tuple[ConceptEvidence, ...]


@dataclass(frozen=True, slots=True)
class ExtractionRejection:
    """Privacy-safe rejection metadata; deliberately excludes model/source text."""
    chunk_id: str
    item_kind: Literal["entity", "relation", "chunk"]
    item_index: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class ConceptExtractionResult:
    entities: tuple[GroundedConceptEntity, ...]
    relations: tuple[EvidenceBackedRelation, ...]
    chunks_processed: int
    chunks_skipped: int = 0
    rejections: tuple[ExtractionRejection, ...] = ()


def _normalized_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _quote_contains_name(name: str, quote: str) -> bool:
    return _normalized_name(name) in _normalized_name(quote)


def _entity_id(entity_type: EntityType, name: str) -> str:
    normalized = _normalized_name(name)
    if not normalized:
        raise ValueError("entity name must not be blank")
    return f"{entity_type}:{normalized}"


class ConceptExtractionService:
    """Extract concepts for one fixed scope and write only validated relations.

    Every model call is scoped to one StructuredChunk. Evidence quotes must be
    literal substrings of that chunk. Book, role, page, and chunk provenance are
    always constructed from the input chunk, never accepted from model output.
    """

    def __init__(
        self,
        gateway: StructuredModel,
        relation_store: ConceptRelationStore,
        *,
        scope_id: str,
        max_tokens: int = 4096,
    ) -> None:
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._gateway = gateway
        self._store = relation_store
        self._scope_id = scope_id.strip()
        self._max_tokens = max_tokens

    def extract(self, chunks: Sequence[StructuredChunk]) -> ConceptExtractionResult:
        source_by_id: dict[str, StructuredChunk] = {}
        for chunk in chunks:
            prior = source_by_id.get(chunk.chunk_id)
            if prior is not None and (
                prior.book_id != chunk.book_id
                or prior.role != chunk.role
                or prior.pages != chunk.pages
                or prior.text != chunk.text
            ):
                raise ValueError(f"chunk_id has conflicting source data: {chunk.chunk_id}")
            source_by_id[chunk.chunk_id] = chunk

        ordered_chunks = [source_by_id[key] for key in sorted(source_by_id)]
        entity_evidence: dict[str, dict[str, ConceptEvidence]] = defaultdict(dict)
        entity_names: dict[str, tuple[str, EntityType]] = {}
        relation_evidence: dict[tuple[str, str, str], dict[str, ConceptEvidence]] = defaultdict(dict)
        rejections: list[ExtractionRejection] = []
        chunks_processed = 0
        chunks_skipped = 0

        for chunk_index, chunk in enumerate(ordered_chunks):
            try:
                response = self._gateway.structured(
                [
                    {
                        "role": "system",
                        "content": (
                            "Extract only entities and relations explicitly stated in this source chunk. "
                            "Return compact JSON. Each evidence_quote must be copied verbatim as one "
                            "contiguous substring, preserving every character, including Unicode "
                            "ligatures, punctuation, whitespace, and symbols. Never paraphrase, normalize, "
                            "or correct the text. Omit an item if you cannot quote it exactly. For a "
                            "relation, its quote must contain both endpoint names verbatim and the words "
                            "that state the relation; do not infer a missing endpoint from context. "
                            "Relation endpoints must also be listed as entities in this response. Do not "
                            "return any provenance fields."
                        ),
                    },
                        {"role": "user", "content": f"Source chunk {chunk.chunk_id}:\n{chunk.text}"},
                    ],
                    ConceptExtractionOutput,
                    prompt_version=PROMPT_VERSION,
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                )
                output = ConceptExtractionOutput.model_validate(response)
            except Exception as exc:
                error_codes = {
                    "LLMOutputTruncated": "model_output_truncated",
                    "InvalidLLMOutput": "model_output_invalid",
                    "LLMUnavailable": "model_unavailable",
                    "ValidationError": "model_output_invalid",
                }
                reason = error_codes.get(type(exc).__name__)
                if reason is None:
                    raise
                rejections.append(ExtractionRejection(
                    chunk_id=chunk.chunk_id, item_kind="chunk", item_index=None,
                    reason_code=reason,
                ))
                chunks_skipped = len(ordered_chunks) - chunk_index - 1
                break
            chunks_processed += 1
            accepted_entities: set[str] = set()
            for index, entity in enumerate(output.entities):
                reason = _entity_rejection_reason(entity, chunk)
                eid = _entity_id(entity.entity_type, entity.name)
                prior = entity_names.get(eid)
                if reason is None and prior is not None and prior[1] != entity.entity_type:
                    reason = "entity_type_conflict"
                if reason is not None:
                    rejections.append(ExtractionRejection(
                        chunk_id=chunk.chunk_id, item_kind="entity", item_index=index,
                        reason_code=reason,
                    ))
                    continue
                canonical = (entity.name.strip(), entity.entity_type)
                entity_names.setdefault(eid, canonical)
                entity_evidence[eid][chunk.chunk_id] = _evidence_from_chunk(chunk)
                accepted_entities.add(eid)

            for index, relation in enumerate(output.relations):
                reason = _relation_rejection_reason(relation, chunk)
                src = _entity_id(relation.source_type, relation.source_name)
                dst = _entity_id(relation.target_type, relation.target_name)
                if reason is None and (src not in accepted_entities or dst not in accepted_entities):
                    reason = "relation_endpoint_not_accepted_entity"
                if reason is not None:
                    rejections.append(ExtractionRejection(
                        chunk_id=chunk.chunk_id, item_kind="relation", item_index=index,
                        reason_code=reason,
                    ))
                    continue
                key = (src, dst, relation.relation_type)
                relation_evidence[key][chunk.chunk_id] = _evidence_from_chunk(chunk)

        writes: list[tuple[ConceptEdge, list[ConceptEvidence]]] = []
        for (src, dst, relation_type), citations in sorted(relation_evidence.items()):
            evidence = [citations[key] for key in sorted(citations)]
            writes.append((
                ConceptEdge(
                    src_entity=src,
                    dst_entity=dst,
                    type=relation_type,
                    evidence_chunk_ids=[item.chunk_id for item in evidence],
                    confidence=UNSCORED_CONFIDENCE,
                    classifier={
                        "model": self._gateway.model_id,
                        "prompt_version": PROMPT_VERSION,
                    },
                ),
                evidence,
            ))

        stored = self._store.put_many(self._scope_id, writes) if writes else []
        entities = tuple(
            GroundedConceptEntity(
                entity_id=eid,
                name=entity_names[eid][0],
                entity_type=entity_names[eid][1],
                evidence=tuple(entity_evidence[eid][key] for key in sorted(entity_evidence[eid])),
            )
            for eid in sorted(entity_names)
        )
        return ConceptExtractionResult(
            entities=entities,
            relations=tuple(stored),
            chunks_processed=chunks_processed,
            chunks_skipped=chunks_skipped,
            rejections=tuple(rejections),
        )


def _entity_rejection_reason(entity: ExtractedEntity, chunk: StructuredChunk) -> str | None:
    if not entity.evidence_quote.strip():
        return "entity_quote_empty"
    if entity.evidence_quote not in chunk.text:
        return "entity_quote_not_exact_source"
    if not _quote_contains_name(entity.name, entity.evidence_quote):
        return "entity_name_not_in_quote"
    return None


def _relation_rejection_reason(relation: ExtractedRelation, chunk: StructuredChunk) -> str | None:
    if not relation.evidence_quote.strip():
        return "relation_quote_empty"
    if relation.evidence_quote not in chunk.text:
        return "relation_quote_not_exact_source"
    if not _quote_contains_name(relation.source_name, relation.evidence_quote):
        return "relation_source_not_in_quote"
    if not _quote_contains_name(relation.target_name, relation.evidence_quote):
        return "relation_target_not_in_quote"
    if not _relation_type_is_explicit(relation, relation.evidence_quote):
        return "relation_type_not_explicit_in_quote"
    return None


def _relation_type_is_explicit(relation: ExtractedRelation, quote: str) -> bool:
    """Accept only a directed relation phrase stated explicitly in the exact quote."""
    text = _normalized_name(quote)
    source = r"\s+".join(re.escape(part) for part in _normalized_name(relation.source_name).split())
    target = r"\s+".join(re.escape(part) for part in _normalized_name(relation.target_name).split())
    patterns = {
        "defines": (
            rf"\b{source}\b\s+(?:explicitly\s+)?defines\s+\b{target}\b",
            rf"\b{source}\b\s+(?:is|was)\s+defined\s+as\s+\b{target}\b",
            rf"\b{target}\b\s+(?:is|was)\s+defined\s+by\s+\b{source}\b",
        ),
        "applies_to": (
            rf"\b{source}\b\s+(?:applies|applied)\s+to\s+\b{target}\b",
            rf"\b{source}\b\s+(?:is|was)\s+applied\s+to\s+\b{target}\b",
        ),
        "example_of": (
            rf"\b{source}\b\s+(?:is|was)\s+(?:an?\s+)?example\s+of\s+\b{target}\b",
        ),
        "part_of": (
            rf"\b{source}\b\s+(?:is|was)\s+part\s+of\s+\b{target}\b",
        ),
        "contrasts_with": (
            rf"\b{source}\b\s+(?:contrasts|contrasted)\s+with\s+\b{target}\b",
            rf"\b{source}\b\s+(?:is|was)\s+in\s+contrast\s+to\s+\b{target}\b",
        ),
        "prerequisite_of": (
            rf"\b{source}\b\s+(?:is|was)\s+(?:a\s+)?prerequisite\s+(?:for|of)\s+\b{target}\b",
            rf"\b{source}\b\s+(?:must|should)\s+be\s+learned\s+before\s+\b{target}\b",
        ),
    }.get(relation.relation_type, ())
    return any(re.search(pattern, text) for pattern in patterns)


def _evidence_from_chunk(chunk: StructuredChunk) -> ConceptEvidence:
    return ConceptEvidence(
        chunk_id=chunk.chunk_id,
        book_id=chunk.book_id,
        role=chunk.role,
        pages=tuple(sorted(set(chunk.pages))),
    )
