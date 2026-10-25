"""Assemble a ``KnowledgeDossier`` from fetched sources (learning-companion §6, §10).

1. Extract claims with verbatim quotes from every source (extractor family A).
2. Citation validator, deterministic part: the quote must occur in the sanitized source text.
3. Citation validator, semantic part: an independent judge (family B != A) decides whether the quote
   supports the claim.
4. Conflicts: validated facts on the same subject (section, concept) are compared by the judge;
   contradicting claims are kept as attributed viewpoints in one ``conflict_group``, never merged.
5. The fact rule: a fact is ``verified`` with a tier-1 source or two distinct sources (the judge
   checks other sources' validated quotes as corroboration); otherwise a single tier-2 fact is
   published only *attributed* to its source and a single tier-3 fact is rejected.
Viewpoints are always attributed. Rejected claims stay in the artifact for audit, never shown.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlsplit

from b2c_contracts import DossierClaim, Evidence, FurtherReading, KnowledgeDossier, RejectedItem

from .claims import CitationJudge, ClaimExtractor, ModelRole, RawClaim
from .extract import quote_in_text
from .meta import BookMeta
from .sources.base import SourceText


def _claim_id(rc: RawClaim) -> str:
    raw = f"{rc.source_id}|{rc.section}|{rc.statement}|{rc.quote}"
    return "clm_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _label(src: SourceText) -> str:
    host = urlsplit(src.source.url).hostname or src.source.provider
    return f"{src.source.title} ({host})" if src.source.title else host


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def build_dossier(*, book_id: str, meta: BookMeta, sources: Sequence[SourceText], extractor: ModelRole,
                  judge: ModelRole, queries: Sequence[str], rejected: Sequence[RejectedItem],
                  clock: Callable[[], datetime], concepts: Sequence[str] = (), workers: int = 4,
                  extractor_options: dict | None = None, max_further_reading: int = 15) -> KnowledgeDossier:
    if extractor.family == judge.family:
        raise ValueError("the citation judge must be from a different model family than the extractor")
    by_sid = {s.source.source_id: s for s in sources}
    ext = ClaimExtractor(extractor, **(extractor_options or {}))
    jdg = CitationJudge(judge)
    judged_by = judge.identity.model_id

    def run_extract(src: SourceText) -> tuple[list[RawClaim], list[str]]:
        return ext.extract(meta, src, concepts=concepts)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        extracted = list(pool.map(run_extract, sources))
    raw: dict[str, RawClaim] = {}
    extraction_errors = 0
    for claims, errors in extracted:
        extraction_errors += len(errors)
        for rc in claims:
            raw.setdefault(_claim_id(rc), rc)

    found = {cid: len(rc.quote) <= 800 and quote_in_text(rc.quote, by_sid[rc.source_id].text)
             for cid, rc in raw.items()}
    per_source: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for cid, rc in raw.items():
        if found[cid]:
            per_source[rc.source_id].append((cid, rc.statement, rc.quote))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        verdict_maps = list(pool.map(jdg.supports, per_source.values()))
    supports: dict[str, bool | None] = {cid: None for cid in raw}
    for m in verdict_maps:
        supports.update(m)
    valid = [cid for cid in raw if found[cid] and supports[cid] is True]

    def evidence(cid: str, *, supported: bool | None = None) -> Evidence:
        rc = raw[cid]
        src = by_sid[rc.source_id].source
        return Evidence(source_id=src.source_id, url=src.url, quote=rc.quote[:800], tier=src.tier,
                        fetched_at=src.fetched_at, quote_found=found[cid],
                        supports=supports[cid] if supported is None else supported,
                        judged_by=judged_by if (supports[cid] is not None or supported is not None) else None)

    # ---- conflicts among validated facts on the same subject
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cid in valid:
        rc = raw[cid]
        if rc.kind == "fact":
            groups[(rc.section, (rc.concept or "").casefold())].append(cid)
    uf = _UnionFind()
    in_conflict: set[str] = set()
    for (section, _), ids in groups.items():
        for start in range(0, len(ids), 40):
            batch = ids[start:start + 40]
            for a, b in jdg.conflicts(section, [(i, raw[i].statement) for i in batch]):
                uf.union(a, b)
                in_conflict.update((a, b))

    claims: list[DossierClaim] = []
    for cid, rc in raw.items():
        src = by_sid[rc.source_id]
        base = dict(claim_id=cid, section=rc.section, statement=rc.statement, kind=rc.kind, concept=rc.concept)
        if not found[cid]:
            reason = "quote too long" if len(rc.quote) > 800 else "quote not found in the fetched source text"
            claims.append(DossierClaim(**base, evidence=[evidence(cid)], status="rejected", status_reason=reason))
            continue
        if supports[cid] is not True:
            reason = "judge found the quote does not support the claim" if supports[cid] is False \
                else "no judge verdict"
            claims.append(DossierClaim(**base, evidence=[evidence(cid)], status="rejected", status_reason=reason))
            continue
        who = rc.attributed_to or _label(src)
        if cid in in_conflict:
            group = "cf_" + hashlib.sha256(uf.find(cid).encode()).hexdigest()[:10]
            claims.append(DossierClaim(**base, evidence=[evidence(cid)], status="attributed", attributed_to=who,
                                       conflict_group=group, status_reason="conflicts with another source"))
            continue
        if rc.kind == "viewpoint":
            claims.append(DossierClaim(**base, evidence=[evidence(cid)], status="attributed", attributed_to=who))
            continue
        if src.source.tier == 1:
            claims.append(DossierClaim(**base, evidence=[evidence(cid)], status="verified"))
            continue
        candidates = [(o, raw[o].quote) for o in valid
                      if raw[o].source_id != rc.source_id and raw[o].section == rc.section][:12]
        extra = jdg.corroborating(rc.statement, candidates)
        ev = [evidence(cid)]
        seen = {rc.source_id}
        for o in extra:
            if raw[o].source_id not in seen:
                seen.add(raw[o].source_id)
                ev.append(evidence(o, supported=True))
        if len(seen) >= 2:
            claims.append(DossierClaim(**base, evidence=ev, status="verified"))
        elif src.source.tier == 2:
            claims.append(DossierClaim(**base, evidence=ev, status="attributed", attributed_to=_label(src),
                                       status_reason="single tier-2 source: stated in its name"))
        else:
            claims.append(DossierClaim(**base, evidence=ev, status="rejected",
                                       status_reason="uncorroborated fact from a single tier-3 source"))

    reading: list[FurtherReading] = []
    seen_reading: set[str] = set()
    for src in sorted(sources, key=lambda s: s.source.tier):
        for item in src.reading:
            key = item.title.casefold()
            if key not in seen_reading and len(reading) < max_further_reading:
                seen_reading.add(key)
                reading.append(item)

    published = [c for c in claims if c.status != "rejected"]
    best_tier = Counter(str(min(e.tier for e in c.evidence)) for c in published)
    rejected_items = list(rejected)
    metrics = {
        "sources": len(sources),
        "source_tiers": dict(Counter(str(s.source.tier) for s in sources)),
        "raw_claims": len(raw),
        "extraction_errors": extraction_errors,
        "quote_found": sum(found.values()),
        "judged_supported": len(valid),
        "quote_found_rate": round(sum(found.values()) / len(raw), 4) if raw else 0.0,
        "citation_support_rate": round(len(valid) / len(raw), 4) if raw else 0.0,
        "published_claims": len(published),
        "verified": sum(c.status == "verified" for c in claims),
        "attributed": sum(c.status == "attributed" for c in claims),
        "rejected_claims": sum(c.status == "rejected" for c in claims),
        "conflict_groups": len({c.conflict_group for c in claims if c.conflict_group}),
        "published_by_best_tier": dict(best_tier),
        "published_by_section": dict(Counter(c.section for c in published)),
        "rejected_items_by_stage": dict(Counter(r.stage for r in rejected_items)),
        "injection_removed": sum(r.stage == "injection" for r in rejected_items),
        "further_reading": len(reading),
    }
    return KnowledgeDossier(book_id=book_id, title=meta.full_title, authors=list(meta.authors),
                            identifiers=meta.identifiers(), created_at=clock(), queries=list(queries),
                            sources=[s.source for s in sources], claims=claims, further_reading=reading,
                            rejected=rejected_items, extractor=extractor.identity, judge=judge.identity,
                            extractor_family=extractor.family, judge_family=judge.family, metrics=metrics)


__all__ = ["build_dossier"]
