"""The only network client of b2c_research (import-linter allows ``httpx`` here and nowhere else in
the package). It fetches public web/API documents, never model servers (CLAUDE.md rule 5: model
calls go through b2c_llm_gateway, injected by orchestration).

Every hop (including redirects) passes ``DomainPolicy``; bodies are streamed with a byte cap; only
text-like content types are accepted; the fetch date is recorded. ``FileFetchCache`` keeps
responses on disk (scratch directory, outside git) so a rerun does not hit the APIs again and keeps
the ORIGINAL fetch date.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import httpx

from .policy import DomainPolicy

USER_AGENT = "b2c-knowledge-research/1 (non-commercial research; book2course)"
TEXT_TYPES = ("text/html", "text/plain", "application/json", "application/xhtml+xml",
              "application/ld+json", "application/sparql-results+json")


class FetchRefused(Exception):
    """A URL was not fetched: ``stage`` is ``policy`` (refused before any request) or ``fetch``."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(f"{stage}: {reason}")
        self.stage = stage
        self.reason = reason


@dataclass(frozen=True)
class FetchLimits:
    max_bytes: int = 2_000_000
    timeout_s: float = 20.0
    max_redirects: int = 3
    min_interval_s: float = 1.0   # politeness: at most one request per host per interval
    max_retries: int = 3          # on HTTP 429/503, honouring Retry-After (capped)
    max_retry_wait_s: float = 30.0


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    fetched_at: datetime
    from_cache: bool = False

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        return json.loads(self.body)


class Fetcher(Protocol):
    def get(self, url: str, *, params: Mapping[str, object] | None = None,
            headers: Mapping[str, str] | None = None) -> FetchResult: ...


class FileFetchCache:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")

    def get(self, url: str) -> FetchResult | None:
        path = self._path(url)
        if not path.exists():
            return None
        d = json.loads(path.read_text())
        return FetchResult(url=d["url"], final_url=d["final_url"], status=d["status"], content_type=d["content_type"],
                           body=base64.b64decode(d["body"]), fetched_at=datetime.fromisoformat(d["fetched_at"]),
                           from_cache=True)

    def put(self, result: FetchResult) -> None:
        tmp = self._path(result.url).with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": result.url, "final_url": result.final_url, "status": result.status,
                                   "content_type": result.content_type, "fetched_at": result.fetched_at.isoformat(),
                                   "body": base64.b64encode(result.body).decode("ascii")}))
        tmp.replace(self._path(result.url))


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HttpxFetcher:
    def __init__(self, *, policy: DomainPolicy | None = None, limits: FetchLimits | None = None,
                 cache: FileFetchCache | None = None, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], datetime] = _now, user_agent: str = USER_AGENT,
                 default_params: Mapping[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.policy = policy or DomainPolicy()
        self.limits = limits or FetchLimits()
        self.cache = cache
        self.clock = clock
        self.default_params = dict(default_params or {})
        self.sleep = sleep
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=self.limits.timeout_s, transport=transport, follow_redirects=False,
                                    headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

    def get(self, url: str, *, params: Mapping[str, object] | None = None,
            headers: Mapping[str, str] | None = None) -> FetchResult:
        full = str(httpx.URL(url, params={**self.default_params, **dict(params or {})} or None))
        reason = self.policy.refusal(full)
        if reason:
            raise FetchRefused("policy", reason)
        if self.cache is not None and (hit := self.cache.get(full)) is not None:
            return hit
        current = full
        hops = retries = 0
        while hops <= self.limits.max_redirects:
            reason = self.policy.refusal(current)
            if reason:
                raise FetchRefused("policy", f"redirect target refused: {reason}")
            self._throttle(httpx.URL(current).host)
            try:
                with self._client.stream("GET", current, headers=dict(headers or {})) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = str(httpx.URL(current).join(r.headers["location"]))
                        hops += 1
                        continue
                    if r.status_code in (429, 503) and retries < self.limits.max_retries:
                        retries += 1
                        try:
                            wait = float(r.headers.get("retry-after", ""))
                        except ValueError:
                            wait = 2.0 * 2 ** retries
                        self.sleep(min(self.limits.max_retry_wait_s, max(1.0, wait)))
                        continue
                    if r.status_code != 200:
                        raise FetchRefused("fetch", f"HTTP {r.status_code}")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in TEXT_TYPES:
                        raise FetchRefused("fetch", f"content type {ctype or 'missing'} is not text")
                    declared = r.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > self.limits.max_bytes:
                        raise FetchRefused("fetch", "body larger than the size limit")
                    body = bytearray()
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > self.limits.max_bytes:
                            raise FetchRefused("fetch", "body larger than the size limit")
            except httpx.HTTPError as exc:
                raise FetchRefused("fetch", type(exc).__name__) from exc
            result = FetchResult(url=full, final_url=current, status=200, content_type=ctype, body=bytes(body),
                                 fetched_at=self.clock())
            if self.cache is not None:
                self.cache.put(result)
            return result
        raise FetchRefused("fetch", "too many redirects")

    def _throttle(self, host: str) -> None:
        if self.limits.min_interval_s <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, -1e9) + self.limits.min_interval_s - now
            self._last[host] = now + max(0.0, wait)
        if wait > 0:
            self.sleep(wait)


__all__ = ["FetchLimits", "FetchRefused", "FetchResult", "Fetcher", "FileFetchCache", "HttpxFetcher", "TEXT_TYPES",
           "USER_AGENT"]
