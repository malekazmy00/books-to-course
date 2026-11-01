"""Language research in Arabic after translation (D-034; learning-companion §11b), on the companion queue.

Reads the approved termbase of the translation run and stores ``language-research`` (stage), built by
``b2c_research.language.research_language``: per term an honest status (established / found in Arabic
Wikipedia / not established, with the original to search for) and a "read in Arabic" list.

A term is *established* only when an established source gave the same Arabic equivalent (glossary,
Wikidata/Arabic Wikipedia, AGROVOC, a prior termbase, translation memory): two models agreeing is a
consistent choice, not an Arabic source. Lookups go to Arabic Wikipedia and Wikidata through the
research fetcher (throttled, cached); a failure is recorded in the metrics, never raised.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.common import RetryPolicy

from b2c_contracts import ArtifactRef, stage_key
from b2c_research.language import LANGUAGE_RESEARCH_FORMAT, LanguageTerm, arwiki_url, research_language
from b2c_translation.termbase import BookTermbase
from b2c_translation.termbase.model import target_key

from .heartbeat import activity_heartbeats

ACTIVITY = "language_research"
STAGE = "language-research"
VERSION = "language-research-2026-11-01.1"
ESTABLISHED_SOURCES = frozenset({"glossary", "agrovoc", "wikidata", "prior_termbase", "translation_memory"})
ARWIKI_API = "https://ar.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
LANGUAGE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=1),
                                        retry_policy=RetryPolicy(maximum_attempts=3))


class LanguageResearchInput(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    termbase_ref: dict[str, Any]      # approved termbase snapshot (stage book-termbase-approved)
    max_lookups: int = Field(default=80, ge=0, le=500)


def language_terms(termbase: BookTermbase) -> list[LanguageTerm]:
    out = []
    for r in termbase.entries:
        if r.status != "approved" or not r.target:
            continue
        same = [p for p in r.proposals if target_key(p.target) == target_key(r.target)]
        established = any(p.provenance in ESTABLISHED_SOURCES for p in same)
        arwiki = next((arwiki_url(r.target) for p in same
                       if p.provenance == "wikidata" and "arwiki title" in (p.note or "")), None)
        out.append(LanguageTerm(source=r.source, target=r.target, kind=r.kind, frequency=r.candidate.frequency,
                                established=established, arabic_wikipedia=arwiki))
    return out


def wiki_lookups(fetcher: Any) -> tuple[Callable[[str], dict | None], Callable[[str], list[str]]]:
    """(page, english) over the research fetcher: exact Arabic title (redirects followed) -> its
    Wikidata item; Wikidata item -> English labels and aliases."""
    def page(title: str) -> dict | None:
        data = fetcher.get(ARWIKI_API, params={"action": "query", "titles": title, "redirects": 1, "format": "json",
                                               "prop": "pageprops", "ppprop": "wikibase_item"}).json()
        for p in (data.get("query") or {}).get("pages", {}).values():
            if "missing" not in p and (p.get("pageprops") or {}).get("wikibase_item"):
                return {"title": p["title"], "wikibase_item": p["pageprops"]["wikibase_item"]}
        return None

    def english(qid: str) -> list[str]:
        data = fetcher.get(WIKIDATA_API, params={"action": "wbgetentities", "ids": qid, "props": "labels|aliases",
                                                 "languages": "en", "format": "json"}).json()
        ent = (data.get("entities") or {}).get(qid) or {}
        names = [((ent.get("labels") or {}).get("en") or {}).get("value", "")]
        return [n for n in names + [a.get("value", "") for a in (ent.get("aliases") or {}).get("en") or []] if n]

    return page, english


class LanguageResearchActivities:
    def __init__(self, *, artifact_store: Any, page: Callable[[str], dict | None],
                 english: Callable[[str], list[str]], clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.store, self.page, self.english, self.clock = artifact_store, page, english, clock

    @activity.defn(name=ACTIVITY)
    def research(self, inp: LanguageResearchInput) -> dict[str, Any]:
        ref = ArtifactRef.model_validate(inp.termbase_ref)
        if ref.stage != "book-termbase-approved" or not ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
            raise ValueError("termbase_ref must be an approved termbase of this course")
        with activity_heartbeats(ACTIVITY):
            termbase = BookTermbase.from_json(self.store.read(ref, "termbase.json"))
            key = stage_key(STAGE, VERSION, [ref.sha256], {"max_lookups": inp.max_lookups})

            def compute() -> dict[str, bytes]:
                result = research_language(language_terms(termbase), page=self.page, english=self.english,
                                           max_lookups=inp.max_lookups)
                doc = {**result, "format": LANGUAGE_RESEARCH_FORMAT, "book_id": termbase.book_id,
                       "termbase_sha256": ref.sha256, "version": VERSION, "created_at": self.clock().isoformat()}
                return {"language-research.json": json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")}

            out, hit = self.store.get_or_compute(inp.course_id, STAGE, key, compute)
            doc = json.loads(self.store.read(out, "language-research.json"))
        return {"ref": out.model_dump(mode="json"), "cache_hit": hit, "metrics": doc["metrics"],
                "digest": hashlib.sha256(json.dumps(doc["terms"], sort_keys=True).encode()).hexdigest()}


__all__ = ["ACTIVITY", "LANGUAGE_OPTIONS", "LanguageResearchActivities", "LanguageResearchInput", "STAGE",
           "language_terms", "wiki_lookups"]
