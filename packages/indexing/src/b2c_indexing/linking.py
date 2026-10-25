"""Lesson -> supplementary evidence linking with a relevance threshold (R5, audit 03 H1).

The old link took the top 5 lexical hits of a 2,400-character raw query for every lesson,
with no threshold: every lesson got five "supporting/contradicting" passages, related or not.

Here a lesson is one document (all its body chunks) reduced to its most distinctive terms
(a concept query, audit 03 §5.3.6); every supplementary body chunk is scored by TF-IDF cosine
(length-normalised, so a longer lesson does not inflate scores; IDF over the course corpus,
English stop words removed, light plural folding). Each of the top-k candidates gets a
recorded ``SupplementLinkJudgement``; only those at or above the threshold are linked, and
a lesson with none is linked to nothing.

The threshold is NOT calibrated against human relevance labels (none exist yet, R2):
``calibrated=False`` is recorded on every judgement. Its value is set above the score that
unrelated books reach on each other (negative control, eval/reports/indexing-v2-2026-10-25.md).
Replacing the scorer by a reranker or an LLM judge is a new method and a new policy version.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from b2c_contracts import StructuredChunk, SupplementLinkJudgement

LINK_METHOD = "tfidf-cosine/1"
_TERM = re.compile(r"[a-z][a-z'\-]{2,}")
_STOP = frozenset("""
a about above after again against all also although am among an and any are as at be because been
before being below between both but by can could did do does doing down during each either else
even ever every few for from further had has have having he her here hers herself him himself his
how however i if in into is it its itself just less like may me might more most much must my
myself neither no nor not now of off often on once one only or other others our ours ourselves out
over own per perhaps rather same shall she should since so some such than that the their theirs
them themselves then there therefore these they this those though through thus to too toward
towards under until up upon us very via was we were what whatever when where whether which while
who whom whose why will with within without would yet you your yours yourself yourselves
also see chapter section figure table example examples page pp vol ed eds ibid et al
""".split())


@dataclass(frozen=True)
class LinkPolicy:
    threshold: float = 0.12
    top_k: int = 5
    query_terms: int = 40
    method: str = LINK_METHOD

    @property
    def version(self) -> str:
        raw = json.dumps({"threshold": self.threshold, "top_k": self.top_k, "query_terms": self.query_terms,
                          "method": self.method, "stop_words": len(_STOP)}, sort_keys=True)
        return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


SUPPLEMENT_LINK_POLICY = LinkPolicy()


def _fold(term: str) -> str:
    term = term.strip("'-")
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("s") and not term.endswith(("ss", "us", "is")):
        return term[:-1]
    return term


def terms(text: str) -> list[str]:
    return [t for t in (_fold(w) for w in _TERM.findall(text.lower())) if t and t not in _STOP and len(t) > 2]


def _vector(tf: Counter, idf: Mapping[str, float], limit: int | None = None) -> dict[str, float]:
    weights = {t: (1 + math.log(n)) * idf.get(t, 0.0) for t, n in tf.items() if n}
    if limit is not None:
        weights = dict(sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:limit])
    norm = math.sqrt(sum(w * w for w in weights.values()))
    return {t: w / norm for t, w in weights.items()} if norm else {}


def link_supplementary(lessons: Mapping[str, Sequence[StructuredChunk]], supplementary: Sequence[StructuredChunk],
                       *, policy: LinkPolicy = SUPPLEMENT_LINK_POLICY
                       ) -> tuple[dict[str, list[StructuredChunk]], list[SupplementLinkJudgement]]:
    """Return (linked chunks per lesson, every recorded judgement), deterministic."""
    candidates = [c for c in supplementary if c.role == "supplementary" and c.evidence_class == "body"]
    lesson_tf = {lid: Counter(t for c in chunks if c.evidence_class == "body" for t in terms(c.text))
                 for lid, chunks in lessons.items()}
    cand_tf = [Counter(terms(c.text)) for c in candidates]
    docs = [*lesson_tf.values(), *cand_tf]
    df = Counter(t for tf in docs for t in tf)
    n = len(docs)
    idf = {t: math.log((1 + n) / (1 + d)) + 1 for t, d in df.items()}
    cand_vec = [_vector(tf, idf) for tf in cand_tf]
    linked: dict[str, list[StructuredChunk]] = {}
    judgements: list[SupplementLinkJudgement] = []
    for lid, tf in lesson_tf.items():
        query = _vector(tf, idf, policy.query_terms)
        scored = sorted(((sum(w * vec.get(t, 0.0) for t, w in query.items()), c)
                         for c, vec in zip(candidates, cand_vec)),
                        key=lambda x: (-x[0], x[1].book_id, x[1].chunk_id))[:policy.top_k]
        linked[lid] = []
        for rank, (score, c) in enumerate(scored, 1):
            score = round(score, 6)
            verdict = "linked" if score >= policy.threshold else "below_threshold"
            judgements.append(SupplementLinkJudgement(
                lesson_id=lid, chunk_id=c.chunk_id, book_id=c.book_id, rank=rank, method=policy.method,
                score=score, threshold=policy.threshold, verdict=verdict, calibrated=False,
                policy_version=policy.version))
            if verdict == "linked":
                linked[lid].append(c)
    return linked, judgements


__all__ = ["LINK_METHOD", "LinkPolicy", "SUPPLEMENT_LINK_POLICY", "link_supplementary", "terms"]
