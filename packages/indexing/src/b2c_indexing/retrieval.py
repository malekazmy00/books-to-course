"""Deterministic retrieval baselines; no embedding or reranker is selected."""
from __future__ import annotations
import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from b2c_contracts.models import StructuredChunk
from b2c_core.arabic_text import normalize_arabic
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
def _terms(text: str) -> list[str]:
    return [x.casefold() for x in _TOKEN_RE.findall(normalize_arabic(text))]
def lexical_search(query: str, chunks: Sequence[StructuredChunk], *, top_k: int = 10,
                   allowed_roles: set[str] | None = None,
                   allowed_book_ids: set[str] | None = None) -> list[tuple[StructuredChunk, float]]:
    """BM25-style lexical baseline with explicit book and role filters."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    query_terms = set(_terms(query))
    eligible = [c for c in chunks if (allowed_roles is None or c.role in allowed_roles)
                and (allowed_book_ids is None or c.book_id in allowed_book_ids)]
    if not eligible or not query_terms:
        return []
    docs = [_terms(c.text) for c in eligible]
    avg = sum(map(len, docs)) / len(docs) or 1.0
    df = Counter(t for doc in docs for t in set(doc))
    ranked = []
    for chunk, terms in zip(eligible, docs):
        counts = Counter(terms)
        score = 0.0
        for term in query_terms:
            freq = counts[term]
            if freq:
                idf = math.log(1 + (len(eligible)-df[term]+0.5)/(df[term]+0.5))
                score += idf * freq * 2.2 / (freq + 1.2*(1-0.75+0.75*len(terms)/avg))
        if score > 0:
            ranked.append((chunk, score))
    return sorted(ranked, key=lambda x: (-x[1], x[0].book_id, x[0].role, x[0].chunk_id))[:top_k]
def dense_search(query_vector: Sequence[float], chunks: Sequence[StructuredChunk],
                 embed: Callable[[str], Sequence[float]], *, top_k: int = 10, allowed_roles: set[str] | None = None,
                 allowed_book_ids: set[str] | None = None) -> list[tuple[StructuredChunk, float]]:
    """Dense adapter seam using caller-provided vectors; no model choice implied."""
    q = list(query_vector)
    if not q or top_k < 1:
        raise ValueError("query vector and positive top_k are required")
    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b):
            raise ValueError("embedding dimensions differ")
        d = math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
        return sum(x*y for x,y in zip(a,b))/d if d else 0.0
    eligible = [c for c in chunks if (allowed_roles is None or c.role in allowed_roles)
                and (allowed_book_ids is None or c.book_id in allowed_book_ids)]
    batch_embed = getattr(embed, "embed_many", None)
    vectors = batch_embed([c.text for c in eligible]) if callable(batch_embed) else [embed(c.text) for c in eligible]
    if len(vectors) != len(eligible):
        raise ValueError("passage embedder returned a vector count that does not match the candidate count")
    return sorted(((c, cosine(q, vector)) for c, vector in zip(eligible, vectors)),
                  key=lambda x: (-x[1], x[0].book_id, x[0].role, x[0].chunk_id))[:top_k]
def reciprocal_rank_fusion(*rankings: Sequence[tuple[StructuredChunk, float]], top_k: int = 10,
                           k: int = 60) -> list[tuple[StructuredChunk, float]]:
    """RRF uses the full persisted identity so equal chunk IDs from two books never merge."""
    if top_k < 1 or k < 1:
        raise ValueError("top_k and k must be positive")
    def identity(chunk: StructuredChunk) -> tuple[str, str, str]:
        return chunk.book_id, chunk.role, chunk.chunk_id
    found: dict[tuple[str, str, str], StructuredChunk] = {}
    scores: Counter[tuple[str, str, str]] = Counter()
    for ranking in rankings:
        for rank, (chunk, _) in enumerate(ranking, 1):
            key = identity(chunk)
            found[key] = chunk
            scores[key] += 1/(k+rank)
    keys = sorted(scores, key=lambda item: (-scores[item], item))
    return [(found[key], scores[key]) for key in keys[:top_k]]
