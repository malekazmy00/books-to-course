"""Turning fetched documents into ``SourceText`` (sanitized text + ``ResearchSource`` provenance)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from b2c_contracts import FurtherReading, RejectedItem, ResearchSource

from ..extract import sanitize_text
from ..http import FetchRefused
from ..meta import BookMeta
from ..planner import PlannedQuery


@dataclass(frozen=True)
class SourceText:
    source: ResearchSource
    text: str  # sanitized plain text: what the extractor sees and what quotes are checked against
    section_hint: str | None = None
    concept: str | None = None
    reading: tuple[FurtherReading, ...] = ()


def source_id_for(url: str) -> str:
    return "src_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).casefold()
    return re.sub(r"[^0-9a-z]+", " ", text).strip()


class SourceBuilder:
    """Sanitizes raw text into a SourceText and keeps the list of refused/removed items."""

    def __init__(self, *, max_chars: int = 20_000) -> None:
        self.max_chars = max_chars
        self.rejected: list[RejectedItem] = []

    def refuse(self, url: str, stage: str, reason: str) -> None:
        self.rejected.append(RejectedItem(url=url[:2000], stage=stage, reason=reason[:300]))

    def build(self, *, url: str, provider: str, title: str | None, tier: int, fetched_at: datetime, raw_text: str,
              section_hint: str | None = None, concept: str | None = None,
              reading: Iterable[Mapping[str, Any]] = ()) -> SourceText | None:
        clean = sanitize_text(raw_text, max_chars=self.max_chars)
        for sentence in clean.removed:
            self.refuse(url, "injection", f"instruction-like text removed: {sentence[:200]}")
        if not clean.text.strip():
            self.refuse(url, "extract", "no text after sanitizing")
            return None
        flags = []
        if clean.removed:
            flags.append(f"injection_removed:{len(clean.removed)}")
        if clean.truncated:
            flags.append("truncated")
        sid = source_id_for(url)
        source = ResearchSource(source_id=sid, url=url, provider=provider, title=(title or None),
                                tier=tier, fetched_at=fetched_at,
                                content_sha256=hashlib.sha256(clean.text.encode("utf-8")).hexdigest(),
                                text_chars=len(clean.text), flags=flags)
        items = tuple(FurtherReading(title=r["title"], authors=list(r.get("authors") or []), year=r.get("year"),
                                     url=r.get("url") or url, source_id=sid, tier=tier, note=r.get("note"))
                      for r in reading if r.get("title"))
        return SourceText(source=source, text=clean.text, section_hint=section_hint, concept=concept, reading=items)


class KnowledgeProvider(Protocol):
    kinds: tuple[str, ...]

    def run(self, query: PlannedQuery, meta: BookMeta) -> list[SourceText]: ...


class ResearchRunner:
    """Runs planned queries through the providers that handle their kind; refusals are recorded."""

    def __init__(self, providers: Sequence[Any], builder: SourceBuilder) -> None:
        self.providers = list(providers)
        self.builder = builder

    def run(self, queries: Sequence[PlannedQuery], meta: BookMeta) -> list[SourceText]:
        out: dict[str, SourceText] = {}
        for q in queries:
            provider = next((p for p in self.providers if q.kind in p.kinds), None)
            if provider is None:
                continue
            try:
                found = provider.run(q, meta)
            except FetchRefused as exc:
                self.builder.refuse(q.label, exc.stage, exc.reason)
                continue
            for s in found:
                out.setdefault(s.source.source_id, s)
        return list(out.values())


__all__ = ["KnowledgeProvider", "ResearchRunner", "SourceBuilder", "SourceText", "norm", "source_id_for"]
