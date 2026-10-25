"""Open Library search API (https://openlibrary.org/dev/docs/api/search): catalogue facts of the book."""
from __future__ import annotations

from typing import Any

from ..meta import BookMeta
from ..planner import PlannedQuery
from .base import SourceBuilder, SourceText, norm

SEARCH = "https://openlibrary.org/search.json"
FIELDS = "key,title,subtitle,author_name,first_publish_year,publisher,edition_count,subject,number_of_pages_median"


class OpenLibraryProvider:
    kinds = ("openlibrary.search",)

    def __init__(self, fetcher: Any, builder: SourceBuilder) -> None:
        self.fetcher = fetcher
        self.builder = builder

    def run(self, q: PlannedQuery, meta: BookMeta) -> list[SourceText]:
        params: dict[str, Any] = {"title": meta.title, "limit": 5, "fields": FIELDS}
        if meta.authors:
            params["author"] = meta.authors[0]
        res = self.fetcher.get(SEARCH, params=params)
        key = norm(meta.title)
        for doc in res.json().get("docs") or []:
            if not norm(doc.get("title") or "").startswith(key) or not doc.get("key"):
                continue
            lines = [f"Title: {doc.get('title')}" + (f": {doc['subtitle']}" if doc.get("subtitle") else ""),
                     "Authors: " + "; ".join(doc.get("author_name") or [])]
            if doc.get("first_publish_year"):
                lines.append(f"First published: {doc['first_publish_year']}")
            if doc.get("publisher"):
                lines.append("Publishers: " + "; ".join(doc["publisher"][:6]))
            if doc.get("edition_count"):
                lines.append(f"Edition count in Open Library: {doc['edition_count']}")
            if doc.get("number_of_pages_median"):
                lines.append(f"Median number of pages: {doc['number_of_pages_median']}")
            if doc.get("subject"):
                lines.append("Subjects: " + "; ".join(doc["subject"][:12]))
            s = self.builder.build(url=f"https://openlibrary.org{doc['key']}", provider="openlibrary",
                                   title=doc.get("title"), tier=1, fetched_at=res.fetched_at,
                                   raw_text="\n".join(lines), section_hint="book_card")
            return [s] if s else []
        return []


__all__ = ["OpenLibraryProvider"]
