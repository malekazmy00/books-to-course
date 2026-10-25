"""General web search behind an interface (lowest reliability, strictest controls; §6, §10).

* ``SearxngSearch`` — the b2c-owned SearXNG instance (compose profile ``knowledge-search``, service
  ``b2c-searxng``; never another project's SearXNG, CLAUDE.md rule 7). Its JSON API is reached over
  the internal network only.
* ``PaidSearchAPI`` — a pluggable paid API (Brave Search API shape). Not configured in this
  repository: ``from_key_file`` raises ``SearchNotConfigured`` without a key file (D-029 opt-in).
* ``WebResearch`` — fetches the hits through the policy-checked fetcher, extracts plain text in the
  sandbox and assigns the domain tier.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..extract import html_to_text
from ..http import FetchRefused, HttpxFetcher
from ..meta import BookMeta
from ..planner import PlannedQuery
from ..policy import DomainPolicy, tier_for_url
from .base import SourceBuilder, SourceText


class SearchNotConfigured(RuntimeError):
    """No web search provider is configured (no SearXNG URL / no paid API key)."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    engine: str


class WebSearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int) -> list[SearchHit]: ...


class SearxngSearch:
    name = "searxng"

    def __init__(self, base_url: str, fetcher: Any) -> None:
        self.base_url = base_url.rstrip("/")
        self.fetcher = fetcher

    @classmethod
    def internal(cls, base_url: str, **fetcher_kw: Any) -> "SearxngSearch":
        host = urlsplit(base_url).hostname or ""
        policy = DomainPolicy(allow=(host,), internal_hosts=(host,))
        return cls(base_url, HttpxFetcher(policy=policy, **fetcher_kw))

    def search(self, query: str, *, limit: int) -> list[SearchHit]:
        res = self.fetcher.get(f"{self.base_url}/search", params={"q": query, "format": "json", "language": "en",
                                                                  "safesearch": 1})
        hits = []
        for r in (res.json().get("results") or [])[:limit]:
            if isinstance(r.get("url"), str):
                hits.append(SearchHit(url=r["url"], title=str(r.get("title") or ""),
                                      snippet=str(r.get("content") or ""), engine=self.name))
        return hits


class PaidSearchAPI:
    """Brave Search API (https://api.search.brave.com/res/v1/web/search). Opt-in; not configured."""
    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, fetcher: Any) -> None:
        if not api_key:
            raise SearchNotConfigured("paid search API key is empty")
        self._key = api_key
        self.fetcher = fetcher

    @classmethod
    def from_key_file(cls, path: str | Path, fetcher: Any | None = None) -> "PaidSearchAPI":
        p = Path(path)
        if not p.is_file() or not p.read_text().strip():
            raise SearchNotConfigured(f"no paid search key at {p}")
        return cls(p.read_text().strip(), fetcher or HttpxFetcher(policy=DomainPolicy(allow=("api.search.brave.com",))))

    def search(self, query: str, *, limit: int) -> list[SearchHit]:
        res = self.fetcher.get(self.ENDPOINT, params={"q": query, "count": limit},
                               headers={"X-Subscription-Token": self._key, "Accept": "application/json"})
        results = ((res.json().get("web") or {}).get("results")) or []
        return [SearchHit(url=r["url"], title=str(r.get("title") or ""), snippet=str(r.get("description") or ""),
                          engine=self.name) for r in results[:limit] if isinstance(r.get("url"), str)]


class WebResearch:
    kinds = ("web.search",)

    def __init__(self, search: WebSearchProvider, fetcher: Any, builder: SourceBuilder, *, max_pages: int = 5,
                 policy: DomainPolicy | None = None) -> None:
        self.search = search
        self.fetcher = fetcher
        self.builder = builder
        self.max_pages = max_pages
        self.policy = policy or DomainPolicy()

    def run(self, q: PlannedQuery, meta: BookMeta) -> list[SourceText]:
        out = []
        for hit in self.search.search(q.query, limit=self.max_pages * 2):
            if len(out) >= self.max_pages:
                break
            reason = self.policy.refusal(hit.url)
            if reason:
                self.builder.refuse(hit.url, "policy", reason)
                continue
            try:
                res = self.fetcher.get(hit.url)
            except FetchRefused as exc:
                self.builder.refuse(hit.url, exc.stage, exc.reason)
                continue
            raw = html_to_text(res.text()) if "html" in res.content_type else res.text()
            s = self.builder.build(url=hit.url, provider=f"web:{self.search.name}", title=hit.title or None,
                                   tier=tier_for_url(hit.url), fetched_at=res.fetched_at, raw_text=raw,
                                   section_hint=q.section, concept=q.concept, purpose=f"{q.section}: {q.query}")
            if s:
                out.append(s)
        return out


__all__ = ["PaidSearchAPI", "SearchHit", "SearchNotConfigured", "SearxngSearch", "WebResearch", "WebSearchProvider"]
