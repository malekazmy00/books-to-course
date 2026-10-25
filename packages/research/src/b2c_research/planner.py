"""Query planning from book metadata and the termbase's key concepts.

Official/open APIs first (OpenAlex, Open Library, Wikidata, Wikipedia); general web search only
when a b2c-owned search provider is configured (``web=True``). Open Syllabus is NOT queried: it
publishes no public API, and its terms (blog.opensyllabus.org/terms-and-policies, revision
2023-05-04, checked 2026-10-25) limit research access to anonymized metadata portions by agreement.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .meta import BookMeta


@dataclass(frozen=True)
class PlannedQuery:
    kind: str      # provider.operation, e.g. "openalex.work", "wikipedia.page", "web.search"
    query: str
    section: str   # DossierSection the results mainly serve
    concept: str | None = None

    @property
    def label(self) -> str:
        return f"{self.kind}: {self.query}"


def plan_queries(meta: BookMeta, concepts: Sequence[str] = (), *, max_concepts: int = 8,
                 web: bool = False) -> list[PlannedQuery]:
    t = meta.title
    out = [PlannedQuery("openalex.work", t, "book_card"), PlannedQuery("openlibrary.search", t, "book_card"),
           PlannedQuery("wikidata.entity", t, "book_card"), PlannedQuery("wikipedia.page", t, "book_card"),
           PlannedQuery("openalex.reviews", t, "reception"), PlannedQuery("openalex.citing", t, "reception")]
    for author in meta.authors:
        out += [PlannedQuery("openalex.author", author, "author"), PlannedQuery("wikidata.entity", author, "author"),
                PlannedQuery("wikipedia.page", author, "author")]
    for subject in meta.subjects:
        out.append(PlannedQuery("wikipedia.page", subject, "context"))
    seen: set[str] = set()
    for concept in concepts:
        key = concept.casefold().strip()
        if not key or key in seen:
            continue
        if len(seen) >= max_concepts:
            break
        seen.add(key)
        out.append(PlannedQuery("wikipedia.page", concept, "concept_in_field", concept=concept))
    if web:
        out += [PlannedQuery("web.search", f'"{t}" review', "reception"),
                PlannedQuery("web.search", f'"{t}" criticism', "reception")]
        if meta.authors:
            out.append(PlannedQuery("web.search", f'"{meta.authors[0]}" interview "{t}"', "author"))
    return out


__all__ = ["PlannedQuery", "plan_queries"]
