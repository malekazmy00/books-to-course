"""Evidence-backed concept-relation storage abstractions.

This module deliberately provides only a deterministic in-memory adapter. It
does not select or imply a production graph/database backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from b2c_contracts import ConceptEdge


@dataclass(frozen=True, slots=True)
class ConceptEvidence:
    """A source citation supporting one concept edge."""

    chunk_id: str
    book_id: str
    role: Literal["spine", "supplementary"]
    pages: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.chunk_id.strip():
            raise ValueError("chunk_id must not be empty")
        if not self.book_id.strip():
            raise ValueError("book_id must not be empty")
        if not self.pages or any(page < 1 for page in self.pages):
            raise ValueError("evidence must cite one or more positive physical pages")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("evidence page numbers must be unique")


@dataclass(frozen=True, slots=True)
class EvidenceBackedRelation:
    """A contract edge paired with page- and role-aware evidence."""

    scope_id: str
    edge: ConceptEdge
    evidence: tuple[ConceptEvidence, ...]


RelationWrite = tuple[ConceptEdge, list[ConceptEvidence]]


class ConceptRelationStore(Protocol):
    """Backend-neutral operations needed by the Phase 4 concept layer."""

    def put(self, scope_id: str, edge: ConceptEdge, evidence: list[ConceptEvidence]) -> EvidenceBackedRelation: ...

    def put_many(self, scope_id: str, writes: list[RelationWrite]) -> list[EvidenceBackedRelation]: ...

    def list_relations(self, scope_id: str) -> list[EvidenceBackedRelation]: ...

    def delete_book(self, scope_id: str, book_id: str) -> int: ...


class InMemoryConceptRelationStore:
    """Deterministic adapter for unit tests and early contract development."""

    def __init__(self) -> None:
        self._relations: dict[tuple[str, str, str, str], EvidenceBackedRelation] = {}

    def put(self, scope_id: str, edge: ConceptEdge, evidence: list[ConceptEvidence]) -> EvidenceBackedRelation:
        return self.put_many(scope_id, [(edge, evidence)])[0]

    def put_many(self, scope_id: str, writes: list[RelationWrite]) -> list[EvidenceBackedRelation]:
        """Atomically validate and merge a batch of relation writes."""
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        staged = dict(self._relations)
        keys: list[tuple[str, str, str, str]] = []
        for edge, evidence in writes:
            if not evidence:
                raise ValueError("at least one evidence citation is required")
            by_chunk: dict[str, ConceptEvidence] = {}
            for item in evidence:
                if item.chunk_id not in edge.evidence_chunk_ids:
                    raise ValueError(f"evidence chunk {item.chunk_id!r} is absent from ConceptEdge")
                prior = by_chunk.get(item.chunk_id)
                if prior is not None and prior != item:
                    raise ValueError(f"conflicting evidence metadata for chunk {item.chunk_id!r}")
                by_chunk[item.chunk_id] = item
            if set(by_chunk) != set(edge.evidence_chunk_ids):
                raise ValueError("every ConceptEdge evidence_chunk_id needs page-aware evidence")

            key = (scope_id, edge.src_entity, edge.dst_entity, edge.type)
            existing = staged.get(key)
            if existing is not None:
                if existing.edge.confidence != edge.confidence or existing.edge.classifier != edge.classifier:
                    raise ValueError("relation metadata conflicts with the existing edge")
                by_chunk = {item.chunk_id: item for item in existing.evidence}
                by_chunk.update({item.chunk_id: item for item in evidence})

            ordered = tuple(by_chunk[cid] for cid in sorted(by_chunk))
            merged_edge = ConceptEdge(
                src_entity=edge.src_entity,
                dst_entity=edge.dst_entity,
                type=edge.type,
                evidence_chunk_ids=[item.chunk_id for item in ordered],
                confidence=edge.confidence,
                classifier=edge.classifier,
            )
            staged[key] = EvidenceBackedRelation(scope_id=scope_id, edge=merged_edge, evidence=ordered)
            keys.append(key)

        self._relations = staged
        return [staged[key] for key in keys]

    def list_relations(self, scope_id: str) -> list[EvidenceBackedRelation]:
        return sorted(
            (relation for (scope, *_), relation in self._relations.items() if scope == scope_id),
            key=lambda item: (item.edge.src_entity, item.edge.dst_entity, item.edge.type),
        )

    def delete_book(self, scope_id: str, book_id: str) -> int:
        """Remove one book's evidence; retain edges still supported elsewhere."""
        affected = 0
        for key, relation in list(self._relations.items()):
            if key[0] != scope_id:
                continue
            retained = tuple(item for item in relation.evidence if item.book_id != book_id)
            if len(retained) == len(relation.evidence):
                continue
            affected += 1
            if not retained:
                del self._relations[key]
                continue
            edge = ConceptEdge(
                src_entity=relation.edge.src_entity,
                dst_entity=relation.edge.dst_entity,
                type=relation.edge.type,
                evidence_chunk_ids=[item.chunk_id for item in retained],
                confidence=relation.edge.confidence,
                classifier=relation.edge.classifier,
            )
            self._relations[key] = EvidenceBackedRelation(scope_id, edge, retained)
        return affected
