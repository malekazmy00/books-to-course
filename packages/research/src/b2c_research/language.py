"""Language research in Arabic, after translation (D-034; learning-companion §11b).

What the termbase could not settle becomes knowledge for the learner instead of a silent choice:

* ``established``: an independent source agreed with the Arabic equivalent before translation (a locked
  term, or the Arabic Wikipedia title found by D-034); its Arabic article joins "read in Arabic";
* ``found_in_arabic_wikipedia``: an unsettled term whose chosen Arabic equivalent is the exact title of
  an Arabic Wikipedia article about the SAME concept (the article's Wikidata item names the English
  term) — search results are not used: they mix concepts (e.g. «تمفصل» returns anatomy);
* ``not_established``: said plainly («لا نعرف مقابلًا عربيًا مستقرًا»), with the original to search for.

Lookups are injected (``page``: Arabic title -> {title, wikibase_item} | None; ``english``: Wikidata id ->
English labels and aliases), capped by frequency, and a failed lookup is recorded, never raised.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

LANGUAGE_RESEARCH_FORMAT = "b2c.language-research/1"
ARWIKI = "https://ar.wikipedia.org/wiki/"


@dataclass(frozen=True)
class LanguageTerm:
    source: str
    target: str
    kind: str
    frequency: int
    established: bool
    arabic_wikipedia: str | None = None


def arwiki_url(title: str) -> str:
    return ARWIKI + quote(title.replace(" ", "_"), safe="_")


def _display(url: str) -> str:
    from urllib.parse import unquote
    return unquote(url)


def research_language(terms: Sequence[LanguageTerm], *, page: Callable[[str], dict[str, Any] | None],
                      english: Callable[[str], list[str]], max_lookups: int = 80) -> dict[str, Any]:
    ordered = sorted(terms, key=lambda t: (-t.frequency, t.source.casefold()))
    out: list[dict[str, Any]] = []
    reading: dict[str, dict[str, Any]] = {}
    metrics = {"terms": 0, "established": 0, "found_in_arabic_wikipedia": 0, "not_established": 0, "lookup_failed": 0}
    lookups = 0
    for t in ordered:
        entry: dict[str, Any] = {"source": t.source, "target": t.target, "kind": t.kind, "frequency": t.frequency,
                                 "arabic_wikipedia": None, "note_ar": None, "search_original": None}
        url = t.arabic_wikipedia
        if t.established:
            entry["status"] = "established"
        else:
            entry["status"] = "not_established"
            if lookups < max_lookups:
                lookups += 1
                try:
                    found = page(t.target)
                    if found and found.get("wikibase_item") and t.source.casefold() in {
                            x.casefold() for x in english(found["wikibase_item"])}:
                        entry["status"] = "found_in_arabic_wikipedia"
                        url = arwiki_url(found["title"])
                except Exception:
                    metrics["lookup_failed"] += 1
        if entry["status"] == "not_established":
            url = None
            entry["search_original"] = t.source
            entry["note_ar"] = (f"لا نعرف مقابلًا عربيًا مستقرًا لهذا المصطلح. استعملنا «{t.target}»، "
                                "ويفيد البحث بالمصطلح الأصلي في المراجع.")
        if url:
            entry["arabic_wikipedia"] = _display(url)
            item = reading.setdefault(entry["arabic_wikipedia"], {"title": _display(url)[len(ARWIKI):].replace("_", " "),
                                                                  "url": entry["arabic_wikipedia"], "terms": []})
            item["terms"].append(t.source)
        metrics["terms"] += 1
        metrics[entry["status"]] += 1
        out.append(entry)
    return {"format": LANGUAGE_RESEARCH_FORMAT, "terms": out, "read_in_arabic": list(reading.values()),
            "metrics": metrics}


__all__ = ["LANGUAGE_RESEARCH_FORMAT", "LanguageTerm", "arwiki_url", "research_language"]
