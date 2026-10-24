"""Opt-in graph-assisted retrieval boundary for the optional Phase 6 trial.

This module contains no HippoRAG imports or model/runtime initialization. A
caller must explicitly enable the adapter and inject an already-created backend.
Backend results are resolved only against the caller authorized chunk set, so
unknown IDs and out-of-scope books/roles can never become citations.
"""
from __future__ import annotations

from collections.abc import Sequence
import re
from dataclasses import dataclass
import unicodedata
from typing import Protocol

from b2c_contracts import StructuredChunk

from .concepts import ConceptRelationStore


@dataclass(frozen=True, slots=True)
class GraphHit:
    """Backend hit identified by a project chunk ID, with backend score."""
    chunk_id: str
    score: float
    scope_id: str | None = None


class GraphBackend(Protocol):
    """Minimal adapter-facing contract; not tied to a HippoRAG release."""
    def retrieve(self, query: str, *, top_k: int) -> Sequence[GraphHit]: ...


class GraphRetriever(Protocol):
    """Retriever boundary compatible with chunk-based evaluation callers."""
    def search(self, query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
               allowed_roles: set[str] | None = None,
               allowed_book_ids: set[str] | None = None) -> list[tuple[StructuredChunk, float]]: ...


@dataclass(frozen=True, slots=True)
class GraphAdapterConfig:
    """Safety and rollout configuration; graph retrieval is off by default."""
    enabled: bool = False
    scope_id: str | None = None


class GraphRetrieverAdapter:
    """Resolve opt-in graph hits to authorized source chunks.

    The backend is injected to keep the optional dependency isolated. This
    adapter is executable with a compatible backend, but does not claim the
    HippoRAG 2 package itself conforms until a pinned, security-reviewed shim
    is implemented and evaluated.
    """
    def __init__(self, backend: GraphBackend, *, config: GraphAdapterConfig | None = None):
        self._backend = backend
        self._config = config or GraphAdapterConfig()
        if self._config.scope_id is not None and not self._config.scope_id.strip():
            raise ValueError("scope_id must be non-empty when configured")

    def search(self, query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
               allowed_roles: set[str] | None = None,
               allowed_book_ids: set[str] | None = None) -> list[tuple[StructuredChunk, float]]:
        if not self._config.enabled:
            raise RuntimeError("graph retrieval is disabled; enable it explicitly for an isolated evaluation")
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        by_id: dict[str, StructuredChunk] = {}
        for chunk in chunks:
            if chunk.chunk_id in by_id:
                raise ValueError(f"duplicate authorized chunk_id: {chunk.chunk_id}")
            by_id[chunk.chunk_id] = chunk
        hits = self._backend.retrieve(query, top_k=top_k)
        accepted: list[tuple[StructuredChunk, float]] = []
        seen: set[str] = set()
        for hit in hits:
            if self._config.scope_id is not None and hit.scope_id != self._config.scope_id:
                continue
            if self._config.scope_id is None and hit.scope_id is not None:
                raise ValueError("a scoped backend requires a matching adapter scope_id")
            chunk = by_id.get(hit.chunk_id)
            if chunk is None or hit.chunk_id in seen:
                continue
            if allowed_roles is not None and chunk.role not in allowed_roles:
                continue
            if allowed_book_ids is not None and chunk.book_id not in allowed_book_ids:
                continue
            seen.add(hit.chunk_id)
            accepted.append((chunk, hit.score))
            if len(accepted) == top_k:
                break
        return accepted


class ConceptGraphRetriever:
    """Deterministic, one-hop concept retrieval over a scoped relation store.

    The intended durable candidate is PostgresConceptRelationStore. Queries
    match exact entity identifiers only: there is no token embedding, fuzzy
    matching, or model/LLM call in this path. A configured scope is fixed at
    construction so a caller cannot override it per query.
    """

    def __init__(self, relation_store: ConceptRelationStore, *, scope_id: str):
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        self._relation_store = relation_store
        self._scope_id = scope_id

    def search(self, query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
               allowed_roles: set[str] | None = None,
               allowed_book_ids: set[str] | None = None) -> list[tuple[StructuredChunk, float]]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if allowed_roles is not None and not allowed_roles <= {"spine", "supplementary"}:
            raise ValueError("allowed_roles may contain only spine and supplementary")

        by_source: dict[tuple[str, str, str], StructuredChunk] = {}
        for chunk in chunks:
            key = (chunk.book_id, chunk.chunk_id, chunk.role)
            if key in by_source:
                raise ValueError(f"duplicate authorized source chunk: {key}")
            by_source[key] = chunk

        # The configured scope is passed on every relation read; same-named IDs
        # from another course cannot participate in matching or expansion.
        relations = self._relation_store.list_relations(self._scope_id)
        entities = {r.edge.src_entity for r in relations} | {r.edge.dst_entity for r in relations}
        matched = {entity for entity in entities if _query_matches_entity(query, entity)}
        if not matched:
            return []

        direct = [r for r in relations if r.edge.src_entity in matched or r.edge.dst_entity in matched]
        direct_keys = {(r.edge.src_entity, r.edge.dst_entity, r.edge.type) for r in direct}
        neighbors: set[str] = set()
        for relation in direct:
            for entity in (relation.edge.src_entity, relation.edge.dst_entity):
                if entity not in matched:
                    neighbors.add(entity)
        expanded = [
            r for r in relations
            if (r.edge.src_entity, r.edge.dst_entity, r.edge.type) not in direct_keys
            and (r.edge.src_entity in neighbors or r.edge.dst_entity in neighbors)
        ]

        scores: dict[tuple[str, str, str], float] = {}
        for score, group in ((1.0, direct), (0.5, expanded)):
            for relation in group:
                for citation in relation.evidence:
                    if allowed_roles is not None and citation.role not in allowed_roles:
                        continue
                    if allowed_book_ids is not None and citation.book_id not in allowed_book_ids:
                        continue
                    key = (citation.book_id, citation.chunk_id, citation.role)
                    chunk = by_source.get(key)
                    if chunk is None or not set(citation.pages) <= set(chunk.pages):
                        # Never turn an unknown/stale citation into a result, or
                        # return provenance inconsistent with the supplied chunk.
                        continue
                    scores[key] = max(scores.get(key, 0.0), score)

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0][0], item[0][2], item[0][1]))
        return [(by_source[key], score) for key, score in ranked[:top_k]]


def _query_matches_entity(query: str, entity_id: str) -> bool:
    """Match a full typed ID or its canonical name as an exact phrase.

    Human-readable names are NFKC/case/whitespace normalized, then matched
    with Unicode word boundaries. This allows ordinary questions to retrieve
    typed IDs without enabling partial-token, fuzzy, or embedding matches.
    """
    full_id = re.search(
        rf"(?<![\w.:-]){re.escape(entity_id)}(?![\w:-]|\.(?=\w))",
        query,
    )
    if full_id:
        return True

    _, separator, name = entity_id.partition(":")
    if not separator or not name.strip():
        return False
    normalized_query = _normalize_match_text(query)
    normalized_name = _normalize_match_text(name)
    if not normalized_query or not normalized_name:
        return False
    phrase_pattern = r"\s+".join(re.escape(part) for part in normalized_name.split())
    return re.search(rf"(?<![\w]:)(?<!\w){phrase_pattern}(?!\w)", normalized_query, re.UNICODE) is not None


def _normalize_match_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[-_]+", " ", value)
    return " ".join(value.split())
