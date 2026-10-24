"""Course-scoped query-time retrieval over persisted structured chunks."""
from __future__ import annotations
from collections.abc import Callable, Sequence
import math
from dataclasses import dataclass
from typing import Literal
from b2c_contracts.models import StructuredChunk
from .postgres_store import PostgresChunkStore
from .retrieval import dense_search, lexical_search, reciprocal_rank_fusion
QueryMode = Literal["lexical", "dense", "hybrid"]
Embedding = Callable[[str], Sequence[float]]
@dataclass(frozen=True)
class RetrievalHit:
    chunk: StructuredChunk
    score: float
    method: str
class QueryRetrievalService:
    """Scope retrieval to a required course; dense model choice remains injected."""
    def __init__(self, store: PostgresChunkStore, *, embed_query: Embedding | None = None,
                 embed_passage: Embedding | None = None):
        self._store, self._embed_query, self._embed_passage = store, embed_query, embed_passage
    def search(self, course_id: str, query: str, *, top_k: int = 10,
               allowed_book_ids: set[str] | None = None, allowed_roles: set[str] | None = None,
               mode: QueryMode = "lexical", min_score: float | None = None) -> list[RetrievalHit]:
        if not isinstance(course_id, str) or not course_id.strip(): raise ValueError("course_id must be non-empty")
        if not isinstance(query, str) or not query.strip(): raise ValueError("query must be non-empty")
        if top_k < 1: raise ValueError("top_k must be positive")
        if mode not in ("lexical", "dense", "hybrid"): raise ValueError("mode must be lexical, dense, or hybrid")
        if min_score is not None and (isinstance(min_score, bool) or not isinstance(min_score, (int, float))
                                      or not math.isfinite(min_score)):
            raise ValueError("min_score must be a finite number")
        if min_score is not None and mode != "lexical":
            raise ValueError("min_score is calibrated only for lexical mode; dense/hybrid need separate calibration")
        if allowed_roles is not None and not allowed_roles <= {"spine", "supplementary"}:
            raise ValueError("allowed_roles may contain only spine and supplementary")
        if mode in ("dense", "hybrid") and (self._embed_query is None or self._embed_passage is None):
            raise ValueError("dense and hybrid modes require injected query and passage embedders")
        chunks = self._store.list_chunks(course_id, book_ids=allowed_book_ids, roles=allowed_roles)
        return self.search_candidates(query, chunks, top_k=top_k, mode=mode,
                                      allowed_book_ids=allowed_book_ids,
                                      allowed_roles=allowed_roles, min_score=min_score)

    def search_candidates(self, query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
                          mode: QueryMode = "lexical",
                          allowed_book_ids: set[str] | None = None,
                          allowed_roles: set[str] | None = None,
                          min_score: float | None = None) -> list[RetrievalHit]:
        """Rank a caller-bounded candidate set without broadening its source scope."""
        if not isinstance(query, str) or not query.strip(): raise ValueError("query must be non-empty")
        if top_k < 1: raise ValueError("top_k must be positive")
        if mode not in ("lexical", "dense", "hybrid"): raise ValueError("mode must be lexical, dense, or hybrid")
        if min_score is not None and (isinstance(min_score, bool) or not isinstance(min_score, (int, float))
                                      or not math.isfinite(min_score)):
            raise ValueError("min_score must be a finite number")
        if min_score is not None and mode != "lexical":
            raise ValueError("min_score is calibrated only for lexical mode; dense/hybrid need separate calibration")
        if allowed_roles is not None and not allowed_roles <= {"spine", "supplementary"}:
            raise ValueError("allowed_roles may contain only spine and supplementary")
        if mode in ("dense", "hybrid") and (self._embed_query is None or self._embed_passage is None):
            raise ValueError("dense and hybrid modes require injected query and passage embedders")
        # Recheck caller allow-lists before any ranker sees stored text.
        chunks = [c for c in chunks if (allowed_book_ids is None or c.book_id in allowed_book_ids)
                  and (allowed_roles is None or c.role in allowed_roles)]
        if not chunks: return []
        lexical = lexical_search(query, chunks, top_k=len(chunks)) if mode in ("lexical", "hybrid") else []
        dense = []
        if mode in ("dense", "hybrid"):
            assert self._embed_query is not None and self._embed_passage is not None
            dense = dense_search(self._embed_query(query), chunks, self._embed_passage, top_k=len(chunks))
        if mode == "lexical": ranked, method = lexical[:top_k], "lexical"
        elif mode == "dense": ranked, method = dense[:top_k], "dense_injected"
        else: ranked, method = reciprocal_rank_fusion(lexical, dense, top_k=top_k), "rrf"
        if min_score is not None:
            ranked = [(chunk, score) for chunk, score in ranked if float(score) >= min_score]
        return [RetrievalHit(chunk=c, score=float(score), method=method) for c, score in ranked]
