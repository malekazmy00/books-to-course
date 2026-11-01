"""Known Arabic equivalents of the book's terms, collected before translation (D-034).

learning-companion §11: the established Arabic equivalents are gathered in the book-knowledge stage,
BEFORE translation, because the termbase is fixed before it. This source looks each termbase candidate
up in Wikidata (CC0):

* an entity counts only when its English label or alias IS the term (case-insensitive) and it has
  Arabic — an Arabic Wikipedia article (sitelink ``arwiki``) or an Arabic label;
* the Arabic Wikipedia title comes first: it is the term Arabic writing uses (``hegemony`` -> «هيمنة»,
  while the Wikidata label is «سيطرة»), the Arabic label second;
* several such entities are told apart by the words their English description shares with the
  candidate's context; a tie gives nothing (the termbase vote and the notes handle the rest);
* a failed lookup gives nothing and never blocks the termbase (D-033).

Proposals carry provenance ``wikidata`` (an independent established source: the model vote locks a
term only when it agrees with one, D-050). Network access is injected (``fetch``: API params -> JSON),
so this layer makes no HTTP call itself.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from .model import Proposal, TermCandidate, TermRecord, target_key

KNOWN_VERSION = "wikidata-known-ar-v1"
ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_WORD = re.compile(r"[a-z]{3,}")
_STOP = frozenset({"the", "and", "for", "with", "from", "that", "this", "which", "into", "over", "other", "others",
                   "one", "its", "are", "was", "were", "has", "have", "been", "not", "but", "all", "any"})


def clean_title(title: str | None) -> str | None:
    """An Arabic title without its disambiguation parentheses, or None when it is not Arabic text."""
    text = _PAREN.sub("", " ".join((title or "").split()))
    if not text or not _ARABIC.search(text) or _LATIN.search(text):
        return None
    return text


def _words(text: str | None) -> set[str]:
    return {w for w in _WORD.findall((text or "").casefold()) if w not in _STOP}


class WikidataKnownEquivalents:
    """``ProposalSource`` of established Arabic equivalents from Wikidata / Arabic Wikipedia."""

    def __init__(self, fetch: Callable[[Mapping[str, Any]], Mapping[str, Any]], *, max_hits: int = 7) -> None:
        self._fetch = fetch
        self.max_hits = max_hits
        self._memo: dict[str, list[Proposal]] = {}

    def propose(self, candidate: TermCandidate) -> list[Proposal]:
        key = candidate.source.casefold()
        if key not in self._memo:
            try:
                self._memo[key] = self._lookup(candidate)
            except Exception:  # never blocks the termbase (D-033)
                self._memo[key] = []
        return self._memo[key]

    def _lookup(self, candidate: TermCandidate) -> list[Proposal]:
        term = candidate.source.casefold()
        found = self._fetch({"action": "wbsearchentities", "search": candidate.source, "language": "en",
                             "limit": self.max_hits, "format": "json"})
        ids = [h["id"] for h in found.get("search") or [] if h.get("id")]
        if not ids:
            return []
        data = self._fetch({"action": "wbgetentities", "ids": "|".join(ids), "format": "json",
                            "props": "labels|aliases|descriptions|sitelinks", "languages": "en|ar",
                            "sitefilter": "arwiki"})
        matches = []
        for qid in ids:
            ent = (data.get("entities") or {}).get(qid) or {}
            english = {((ent.get("labels") or {}).get("en") or {}).get("value", "").casefold()}
            english |= {a.get("value", "").casefold() for a in (ent.get("aliases") or {}).get("en") or []}
            if term not in english:
                continue
            arwiki = clean_title(((ent.get("sitelinks") or {}).get("arwiki") or {}).get("title"))
            label = clean_title(((ent.get("labels") or {}).get("ar") or {}).get("value"))
            if arwiki or label:
                desc = ((ent.get("descriptions") or {}).get("en") or {}).get("value", "")
                matches.append((qid, arwiki, label, desc))
        if len(matches) > 1:
            context = _words(candidate.definition_context) | _words(candidate.expansion)
            scored = sorted(((len(_words(m[3]) & context), m) for m in matches), key=lambda x: -x[0])
            if scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
                return []
            matches = [scored[0][1]]
        if not matches:
            return []
        qid, arwiki, label, desc = matches[0]
        out: list[Proposal] = []
        for target, origin in ((arwiki, "arwiki title"), (label, "label")):
            if target and target not in {p.target for p in out}:
                out.append(Proposal(target=target, provenance="wikidata", reference=ENTITY_URL.format(qid=qid),
                                    note=f"{KNOWN_VERSION}: {origin}; {desc[:200]}"))
        return out


def known_description(record: TermRecord) -> tuple[str, str] | None:
    """(English description, Wikidata URL) of an approved entry, only when the entity we looked up gave
    the approved spelling: the description of another entity must not be read for this name."""
    if not record.target:
        return None
    for p in record.proposals:
        if p.provenance == "wikidata" and (p.note or "").startswith(KNOWN_VERSION) and p.reference \
                and target_key(p.target) == target_key(record.target):
            description = (p.note or "").split("; ", 1)[-1].strip() if "; " in (p.note or "") else ""
            if description:
                return description, p.reference
    return None


__all__ = ["KNOWN_VERSION", "WikidataKnownEquivalents", "clean_title", "known_description"]
