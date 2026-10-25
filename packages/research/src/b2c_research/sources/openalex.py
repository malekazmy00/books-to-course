"""OpenAlex (https://docs.openalex.org): the book's work record, its reviews, works citing it, and the
author's profile and most-cited works. Rendered as labelled plain text so quotes can be checked.

Since 2026 OpenAlex meters requests: without an API key a small shared daily budget applies
(checked 2026-10-25: $0.10/day per IP, $0.001 per list request). ``api_key`` is optional.
"""
from __future__ import annotations

import re
from typing import Any

from ..meta import BookMeta
from ..planner import PlannedQuery
from .base import SourceBuilder, SourceText, norm

API = "https://api.openalex.org"


def _abstract(work: dict[str, Any]) -> str:
    inv = work.get("abstract_inverted_index") or {}
    if not isinstance(inv, dict) or not inv:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions or []:
            slots[int(p)] = word
    return " ".join(slots[i] for i in sorted(slots))


def _authors(work: dict[str, Any]) -> list[str]:
    return [a.get("author", {}).get("display_name", "") for a in work.get("authorships") or []
            if a.get("author", {}).get("display_name")]


def _venue(work: dict[str, Any]) -> str:
    return ((work.get("primary_location") or {}).get("source") or {}).get("display_name") or ""


def _surname_overlap(names: list[str], meta: BookMeta) -> bool:
    surnames = {norm(s) for s in meta.surnames}
    return any(norm(n).split()[-1:] and norm(n).split()[-1] in surnames for n in names if norm(n))


class OpenAlexProvider:
    kinds = ("openalex.work", "openalex.reviews", "openalex.citing", "openalex.author")

    def __init__(self, fetcher: Any, builder: SourceBuilder, *, api_key: str | None = None,
                 mailto: str | None = None, max_items: int = 8) -> None:
        self.fetcher = fetcher
        self.builder = builder
        self.max_items = max_items
        self.auth = {"api_key": api_key} if api_key else ({"mailto": mailto} if mailto else {})
        self._works: dict[str, tuple[list[dict[str, Any]], Any]] = {}

    def _get(self, path: str, **params: Any) -> tuple[dict[str, Any], Any]:
        res = self.fetcher.get(API + path, params={**params, **self.auth})
        return res.json(), res

    def _title_search(self, title: str) -> tuple[list[dict[str, Any]], Any]:
        if title not in self._works:
            clean = re.sub(r"[,|:]+", " ", title).strip()
            data, res = self._get("/works", filter=f"title.search:{clean}", **{"per-page": 25})
            self._works[title] = (list(data.get("results") or []), res)
        return self._works[title]

    def _book_works(self, meta: BookMeta) -> tuple[list[dict[str, Any]], Any]:
        works, res = self._title_search(meta.title)
        key = norm(meta.title)
        hits = [w for w in works if norm(w.get("display_name") or "").startswith(key)
                and w.get("type") != "review" and _surname_overlap(_authors(w), meta)]
        hits.sort(key=lambda w: -(w.get("cited_by_count") or 0))
        return hits[:3], res

    def run(self, query: PlannedQuery, meta: BookMeta) -> list[SourceText]:
        if query.kind == "openalex.work":
            return self._work(meta, query)
        if query.kind == "openalex.reviews":
            return self._reviews(meta, query)
        if query.kind == "openalex.citing":
            return self._citing(meta, query)
        if query.kind == "openalex.author":
            return self._author(meta, query)
        return []

    def _render_work(self, w: dict[str, Any]) -> str:
        lines = [f"Title: {w.get('display_name')}", f"Publication year: {w.get('publication_year')}",
                 f"Type: {w.get('type')}"]
        insts = {i.get("display_name") for a in w.get("authorships") or [] for i in a.get("institutions") or []
                 if i.get("display_name")}
        lines.append("Authors: " + "; ".join(_authors(w)))
        if insts:
            lines.append("Author institutions: " + "; ".join(sorted(insts)))
        if _venue(w):
            lines.append(f"Published by / in: {_venue(w)}")
        if w.get("doi"):
            lines.append(f"DOI: {w['doi']}")
        lines.append(f"Cited by count in OpenAlex: {w.get('cited_by_count', 0)}")
        topics = [t.get("display_name") for t in w.get("topics") or [] if t.get("display_name")]
        if topics:
            lines.append("Topics: " + "; ".join(topics[:6]))
        if _abstract(w):
            lines.append("Abstract: " + _abstract(w))
        return "\n".join(lines)

    def _work(self, meta: BookMeta, q: PlannedQuery) -> list[SourceText]:
        works, res = self._book_works(meta)
        out = []
        for w in works:
            s = self.builder.build(url=w["id"], provider="openalex", title=w.get("display_name"), tier=1,
                                   fetched_at=res.fetched_at, raw_text=self._render_work(w), section_hint=q.section)
            if s:
                out.append(s)
        return out

    def _reviews(self, meta: BookMeta, q: PlannedQuery) -> list[SourceText]:
        works, res = self._title_search(meta.title)
        key = norm(meta.title)
        out = []
        for w in works:
            names = _authors(w)
            if key not in norm(w.get("display_name") or "") or _surname_overlap(names, meta):
                continue
            source_type = ((w.get("primary_location") or {}).get("source") or {}).get("type")
            if w.get("type") not in ("review", "article") and source_type != "journal":
                continue
            text = (f"Review of the book \"{meta.title}\".\nReview title: {w.get('display_name')}\n"
                    f"Reviewer(s): {'; '.join(names) or 'unknown'}\nPublished in: {_venue(w) or 'unknown'}\n"
                    f"Year: {w.get('publication_year')}\n" + (f"Abstract: {_abstract(w)}" if _abstract(w) else ""))
            landing = (w.get("primary_location") or {}).get("landing_page_url")
            s = self.builder.build(url=w["id"], provider="openalex", title=f"Review in {_venue(w) or 'a journal'}",
                                   tier=2, fetched_at=res.fetched_at, raw_text=text, section_hint="reception",
                                   reading=[{"title": f"Review: {w.get('display_name')} ({_venue(w)})",
                                             "authors": names, "year": w.get("publication_year"),
                                             "url": landing or w["id"], "note": "published review"}])
            if s:
                out.append(s)
            if len(out) >= self.max_items:
                break
        return out

    def _citing(self, meta: BookMeta, q: PlannedQuery) -> list[SourceText]:
        works, _ = self._book_works(meta)
        if not works:
            return []
        book = works[0]
        wid = book["id"].rsplit("/", 1)[-1]
        data, res = self._get("/works", filter=f"cites:{wid}", sort="cited_by_count:desc",
                              **{"per-page": self.max_items})
        citing = list(data.get("results") or [])
        lines = [f"Works citing \"{book.get('display_name')}\" (OpenAlex {wid}), most cited first. "
                 f"The book's cited-by count in OpenAlex is {book.get('cited_by_count', 0)}."]
        reading = []
        for w in citing:
            names = _authors(w)
            lines.append(f"- {w.get('display_name')} ({w.get('publication_year')}) by {', '.join(names[:3]) or 'unknown'}"
                         f"; cited by {w.get('cited_by_count', 0)}")
            reading.append({"title": w.get("display_name"), "authors": names[:5], "year": w.get("publication_year"),
                            "url": w.get("id"), "note": "a later work that cites this book"})
        s = self.builder.build(url=f"https://openalex.org/works?filter=cites:{wid}", provider="openalex",
                               title=f"Works citing {book.get('display_name')}", tier=1, fetched_at=res.fetched_at,
                               raw_text="\n".join(lines), section_hint="reception", reading=reading)
        return [s] if s else []

    def _author(self, meta: BookMeta, q: PlannedQuery) -> list[SourceText]:
        data, res = self._get("/authors", filter=f"display_name.search:{q.query}", **{"per-page": 10})
        target = norm(q.query).split()
        candidates = [a for a in data.get("results") or []
                      if norm(a.get("display_name") or "").split()[-1:] == target[-1:]
                      and norm(a.get("display_name") or "")[:1] == norm(q.query)[:1]]
        if not candidates:
            return []
        book_ids = {a.get("author", {}).get("id") for w in self._book_works(meta)[0] for a in w.get("authorships") or []}
        candidates.sort(key=lambda a: (a.get("id") not in book_ids, -(a.get("works_count") or 0)))
        a = candidates[0]
        aid = a["id"].rsplit("/", 1)[-1]
        works_data, _ = self._get("/works", filter=f"author.id:{aid}", sort="cited_by_count:desc",
                                  **{"per-page": self.max_items})
        insts = [i.get("display_name") for i in a.get("last_known_institutions") or [] if i.get("display_name")]
        topics = [t.get("display_name") for t in a.get("topics") or [] if t.get("display_name")]
        lines = [f"Author: {a.get('display_name')}", f"Works count: {a.get('works_count')}",
                 f"Cited by count: {a.get('cited_by_count')}"]
        if a.get("orcid"):
            lines.append(f"ORCID: {a['orcid']}")
        if insts:
            lines.append("Last known institutions: " + "; ".join(insts))
        if topics:
            lines.append("Research topics: " + "; ".join(topics[:8]))
        reading = []
        lines.append("Most cited works:")
        key = norm(meta.title)
        for w in works_data.get("results") or []:
            lines.append(f"- {w.get('display_name')} ({w.get('publication_year')}), cited by {w.get('cited_by_count', 0)}")
            if not norm(w.get("display_name") or "").startswith(key):
                reading.append({"title": w.get("display_name"), "authors": _authors(w)[:5],
                                "year": w.get("publication_year"), "url": w.get("id"),
                                "note": f"another work by {a.get('display_name')}"})
        s = self.builder.build(url=a["id"], provider="openalex", title=a.get("display_name"), tier=1,
                               fetched_at=res.fetched_at, raw_text="\n".join(lines), section_hint="author",
                               reading=reading[:5])
        return [s] if s else []


__all__ = ["OpenAlexProvider"]
