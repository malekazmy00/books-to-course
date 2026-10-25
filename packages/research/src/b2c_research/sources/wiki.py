"""Wikipedia (en) via the MediaWiki Action API (plain-text extracts, no HTML) and Wikidata entities.

A Wikipedia page is accepted only if it is about the query: the author's surname or the book title
in the page title, or for a concept, the term in the page text plus one of the book's context
keywords (so "Red Hat" the company is not taken for "Red Hat Analysis").
"""
from __future__ import annotations

from typing import Any

from ..meta import BookMeta
from ..planner import PlannedQuery
from .base import SourceBuilder, SourceText, norm

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

# Properties rendered for people and works (label in the rendered text).
WIKIDATA_PROPS = {
    "P31": "instance of", "P569": "date of birth", "P570": "date of death", "P19": "place of birth",
    "P27": "country of citizenship", "P69": "educated at", "P108": "employer", "P106": "occupation",
    "P101": "field of work", "P800": "notable work", "P737": "influenced by", "P463": "member of",
    "P50": "author", "P577": "publication date", "P123": "publisher", "P921": "main subject",
    "P136": "genre", "P212": "ISBN-13", "P957": "ISBN-10", "P393": "edition number", "P1476": "title",
    "P135": "movement", "P1066": "student of", "P184": "doctoral advisor",
}


class WikipediaProvider:
    kinds = ("wikipedia.page",)

    def __init__(self, fetcher: Any, builder: SourceBuilder, *, context_keywords: tuple[str, ...] = (),
                 search_hint: str | None = None, max_candidates: int = 3) -> None:
        self.fetcher = fetcher
        self.builder = builder
        self.context_keywords = tuple(k.casefold() for k in context_keywords)
        self.search_hint = search_hint
        self.max_candidates = max_candidates

    def _accept(self, q: PlannedQuery, meta: BookMeta, title: str, text: str) -> bool:
        t, body = norm(title), norm(text)
        if q.section == "author":
            surname = norm(q.query).split()[-1:] or [""]
            return surname[0] in t.split()
        if q.section == "book_card":
            return norm(meta.title) in t or (t and t in norm(meta.title) and len(t) > 12)
        if q.section == "context":
            return norm(q.query) in t or t in norm(q.query)
        raw_term = (q.concept or q.query).strip()
        term = norm(raw_term)
        initials = "".join(w[0] for w in t.split() if w not in ("of", "and", "the", "for", "in"))
        about = (term in t or (raw_term.isupper() and initials == term.replace(" ", ""))
                 or (t in term and len(t) > 4))
        keywords = self.context_keywords or tuple(meta.context_keywords)
        return bool(term) and about and term.split()[0] in body and (not keywords or any(k in body for k in keywords))

    def run(self, q: PlannedQuery, meta: BookMeta) -> list[SourceText]:
        searches = [q.query]
        if q.section == "concept_in_field" and self.search_hint:
            searches.insert(0, f"{q.query} {self.search_hint}")
        tried: set[str] = set()
        for search in searches:
            found = self.fetcher.get(WIKIPEDIA_API, params={"action": "query", "list": "search", "srsearch": search,
                                                            "srlimit": 5, "format": "json"}).json()
            titles = [h.get("title") for h in (found.get("query") or {}).get("search") or []
                      if h.get("title") and h.get("title") not in tried][: self.max_candidates]
            tried.update(titles)
            hit = self._first_accepted(q, meta, titles)
            if hit is not None:
                return [hit]
        return []

    def _first_accepted(self, q: PlannedQuery, meta: BookMeta, titles: list[str]) -> SourceText | None:
        for title in titles:
            res = self.fetcher.get(WIKIPEDIA_API, params={
                "action": "query", "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain",
                "inprop": "url", "redirects": 1, "titles": title, "format": "json"})
            pages = ((res.json().get("query") or {}).get("pages") or {}).values()
            for page in pages:
                text = page.get("extract") or ""
                url = page.get("fullurl") or ""
                if not text or not url.startswith("https://") or not self._accept(q, meta, page.get("title", ""), text):
                    continue
                s = self.builder.build(url=url, provider="wikipedia", title=page.get("title"), tier=1,
                                       fetched_at=res.fetched_at, raw_text=f"{page.get('title')}\n{text}",
                                       section_hint=q.section, concept=q.concept)
                if s:
                    return s
        return None


def _time(value: dict[str, Any]) -> str:
    t = str(value.get("time", "")).lstrip("+")
    date = t.split("T")[0]
    parts = [p for p in date.split("-") if p and p != "00"]
    return "-".join(parts)


class WikidataProvider:
    kinds = ("wikidata.entity",)

    def __init__(self, fetcher: Any, builder: SourceBuilder) -> None:
        self.fetcher = fetcher
        self.builder = builder

    def run(self, q: PlannedQuery, meta: BookMeta) -> list[SourceText]:
        found = self.fetcher.get(WIKIDATA_API, params={"action": "wbsearchentities", "search": q.query,
                                                       "language": "en", "limit": 5, "format": "json"}).json()
        want = norm(q.query)
        hits = [h for h in found.get("search") or [] if norm(h.get("label") or "") == want]
        if not hits:
            return []
        qid = hits[0]["id"]
        res = self.fetcher.get(WIKIDATA_ENTITY.format(qid=qid))
        entity = (res.json().get("entities") or {}).get(qid) or {}
        label = ((entity.get("labels") or {}).get("en") or {}).get("value") or q.query
        desc = ((entity.get("descriptions") or {}).get("en") or {}).get("value") or ""
        values: list[tuple[str, str]] = []
        refs: set[str] = set()
        for pid, prop_label in WIKIDATA_PROPS.items():
            for claim in (entity.get("claims") or {}).get(pid, [])[:6]:
                v = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
                if isinstance(v, dict) and "id" in v:
                    values.append((prop_label, "@" + v["id"]))
                    refs.add(v["id"])
                elif isinstance(v, dict) and "time" in v:
                    values.append((prop_label, _time(v)))
                elif isinstance(v, dict) and "text" in v:
                    values.append((prop_label, str(v["text"])))
                elif isinstance(v, str):
                    values.append((prop_label, v))
        labels: dict[str, str] = {}
        ids = sorted(refs)
        for i in range(0, len(ids), 50):
            data = self.fetcher.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": "|".join(ids[i:i + 50]),
                                                          "props": "labels", "languages": "en", "format": "json"}).json()
            for eid, ent in (data.get("entities") or {}).items():
                lab = ((ent.get("labels") or {}).get("en") or {}).get("value")
                if lab:
                    labels[eid] = lab
        lines = [f"{label}: {desc}" if desc else label]
        for prop_label, value in values:
            if value.startswith("@"):
                value = labels.get(value[1:])
                if not value:
                    continue
            if value:
                lines.append(f"{label} — {prop_label}: {value}")
        url = f"https://www.wikidata.org/wiki/{qid}"
        s = self.builder.build(url=url, provider="wikidata", title=label, tier=1, fetched_at=res.fetched_at,
                               raw_text="\n".join(lines), section_hint=q.section)
        return [s] if s else []


__all__ = ["WIKIDATA_PROPS", "WikidataProvider", "WikipediaProvider"]
