"""General multi-domain Wikidata concept lookup for EN→AR translation.

Wikidata structured data is CC0. This source is a broad, community-maintained concept
layer; exact context checks are required because its language coverage and quality vary.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from b2c_contracts import GlossaryTerm, TermbaseEntry

# Fallback hash, used only when an AGROVOC index does not report what it loaded.
from .agrovoc import AGROVOC_CANDIDATE_SHA256 as _LEGACY_AGROVOC_CANDIDATE_SHA256
from .agrovoc import termbase_dir, verify_sha256

WIKIDATA_RELEASE = "2026-09-30"
WIKIDATA_DUMP_SHA256 = "24b68c109044dc327498a68bee6db07f2b0e566ddb0bbeb2fbb289bf46b2f7fc"
WIKIDATA_CANDIDATE_SHA256 = "523aa61b81b51787816a02c394c5f753a17c01d497c23f31694b3c8c60dddc43"
WIKIDATA_LICENSE = "CC0 1.0 (Wikidata structured data)"
MULTISOURCE_POLICY_ID = "g3-policy-wikidata-agrovoc-context-v1"
WIKIDATA_RELATIVE_PATH = Path("wikidata-lexemes/wikidata_concept_en_ar_candidates.jsonl")


def default_wikidata_dataset() -> Path:
    return termbase_dir() / WIKIDATA_RELATIVE_PATH
_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is",
    "of", "on", "or", "the", "to", "with", "that", "which", "this", "these",
    "those", "be", "are", "was", "were", "it", "its", "does", "what",
})


def _normalize(value: str) -> str:
    return _SPACE.sub(" ", value).strip().casefold()


def _words(value: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(value)
            if len(word) > 2 and word.casefold() not in _STOPWORDS}


@dataclass(frozen=True, slots=True)
class WikidataConcept:
    concept_id: str
    uri: str
    en_preferred: str
    ar_preferred: str
    en_alternates: tuple[str, ...]
    ar_alternates: tuple[str, ...]
    linked_en_lemmas: tuple[str, ...]
    linked_ar_lemmas: tuple[str, ...]
    definition_en: str
    definition_ar: str
    domain_item_ids: tuple[str, ...]
    sense_uris: tuple[str, ...]
    license: str
    release: str
    source_dump_sha256: str


@dataclass(frozen=True, slots=True)
class MultiSourceAudit:
    term_en: str
    status: str
    reason: str
    source: str | None = None
    concept_uri: str | None = None
    term_ar: str | None = None
    domain_item_ids: tuple[str, ...] = ()
    context_overlap: tuple[str, ...] = ()
    candidate_count: int = 0
    license: str | None = None
    source_release: str | None = None
    source_hash: str | None = None
    policy_id: str = MULTISOURCE_POLICY_ID


@dataclass(frozen=True, slots=True)
class MultiSourceCompilation:
    entries: tuple[TermbaseEntry, ...]
    audit: tuple[MultiSourceAudit, ...]


class WikidataConceptIndex:
    def __init__(self, concepts: list[WikidataConcept], *, source_sha256: str = WIKIDATA_CANDIDATE_SHA256):
        # SHA-256 of the candidate bytes actually loaded; recorded in audit rows.
        self.source_sha256 = source_sha256
        self.concepts = tuple(concepts)
        self._by_label: dict[str, list[WikidataConcept]] = {}
        for concept in self.concepts:
            labels = dict.fromkeys((concept.en_preferred, *concept.en_alternates, *concept.linked_en_lemmas))
            for label in labels:
                key = _normalize(label)
                if key:
                    self._by_label.setdefault(key, []).append(concept)

    @classmethod
    def from_jsonl(
        cls, path: str | Path | None = None, *, expected_sha256: str | None = WIKIDATA_CANDIDATE_SHA256,
    ) -> "WikidataConceptIndex":
        """Load candidates, verifying the stamped hash unless explicitly opted out (None)."""
        resolved = Path(default_wikidata_dataset() if path is None else path)
        raw = resolved.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        verify_sha256(actual, expected_sha256, source="Wikidata", path=resolved)
        concepts: list[WikidataConcept] = []
        # Split on "\n" only: str.splitlines() would also split on U+2028 inside JSON strings.
        for line in raw.decode("utf-8").split("\n"):
            if not line.strip():
                continue
            row: dict[str, Any] = json.loads(line)
            if row.get("license") != WIKIDATA_LICENSE:
                raise ValueError("Wikidata snapshot contains an unexpected license")
            if not row.get("concept_id") or not row.get("en_preferred") or not row.get("ar_preferred"):
                raise ValueError("Wikidata concept row lacks bilingual preferred labels")
            definitions = row.get("definitions", {})
            concepts.append(WikidataConcept(
                concept_id=row["concept_id"],
                uri=row["concept_uri"],
                en_preferred=row["en_preferred"],
                ar_preferred=row["ar_preferred"],
                en_alternates=tuple(row.get("en_alternates", ())),
                ar_alternates=tuple(row.get("ar_alternates", ())),
                linked_en_lemmas=tuple(row.get("linked_en_lemmas", ())),
                linked_ar_lemmas=tuple(row.get("linked_ar_lemmas", ())),
                definition_en=definitions.get("en", ""),
                definition_ar=definitions.get("ar", ""),
                domain_item_ids=tuple(row.get("domain_item_ids", ())),
                sense_uris=tuple(row.get("wikidata_lexeme_senses", ())),
                license=row["license"],
                release=row.get("source_release", WIKIDATA_RELEASE),
                source_dump_sha256=row.get("source_dump_sha256", WIKIDATA_DUMP_SHA256),
            ))
        return cls(concepts, source_sha256=actual)

    def lookup(self, english_term: str) -> tuple[WikidataConcept, ...]:
        return tuple(self._by_label.get(_normalize(english_term), ()))


def compile_multisource_termbase(
    glossary: list[GlossaryTerm] | tuple[GlossaryTerm, ...],
    wikidata: WikidataConceptIndex,
    *,
    agrovoc: Any | None = None,
    agriculture_domain_confirmed: bool = False,
    minimum_context_overlap: int = 2,
) -> MultiSourceCompilation:
    """Prefer the unique context-supported concept from general and optional domain layers.

    AGROVOC candidates are considered only with explicit agriculture-domain confirmation.
    Candidate ties are rejected instead of being guessed.
    """
    if minimum_context_overlap < 1:
        raise ValueError("minimum_context_overlap must be positive")
    entries: list[TermbaseEntry] = []
    audits: list[MultiSourceAudit] = []
    seen: set[str] = set()
    for item in glossary:
        key = _normalize(item.term_en)
        if not key or key in seen:
            audits.append(MultiSourceAudit(item.term_en, "unresolved", "duplicate_or_empty_glossary_term"))
            continue
        seen.add(key)
        if not item.definition_en.strip():
            audits.append(MultiSourceAudit(item.term_en, "unresolved", "book_definition_missing"))
            continue
        candidates: list[dict[str, Any]] = []
        for concept in wikidata.lookup(item.term_en):
            if concept.definition_en and concept.definition_ar:
                overlap = _words(item.definition_en) & _words(concept.definition_en)
                if len(overlap) >= minimum_context_overlap:
                    candidates.append({
                        "source": "wikidata", "uri": concept.uri, "ar": concept.ar_preferred,
                        "overlap": tuple(sorted(overlap)), "domain": concept.domain_item_ids,
                        "license": concept.license, "release": concept.release,
                        "hash": getattr(wikidata, "source_sha256", WIKIDATA_CANDIDATE_SHA256),
                    })
        if agriculture_domain_confirmed and agrovoc is not None:
            for concept in agrovoc.lookup(item.term_en):
                definitions = concept.definitions
                en_def = " ".join(definitions.get("en", ()))
                if len(concept.ar_preferred) != 1 or not en_def or not definitions.get("ar"):
                    continue
                overlap = _words(item.definition_en) & _words(en_def)
                if len(overlap) >= minimum_context_overlap:
                    candidates.append({
                        "source": "agrovoc", "uri": concept.uri, "ar": concept.ar_preferred[0],
                        "overlap": tuple(sorted(overlap)), "domain": (),
                        "license": concept.license, "release": concept.modified,
                        "hash": getattr(agrovoc, "source_sha256", _LEGACY_AGROVOC_CANDIDATE_SHA256),
                    })
        if not candidates:
            audits.append(MultiSourceAudit(
                item.term_en, "unresolved", "no_context_supported_candidate",
                candidate_count=0,
            ))
            continue
        # Compare definition overlap ratio; ties are ambiguous even if Arabic labels happen to match.
        source_words = _words(item.definition_en)
        ranked = sorted(candidates, key=lambda c: (len(c["overlap"]) / max(1, len(source_words)),
                                                    len(c["overlap"])), reverse=True)
        best = ranked[0]
        best_score = (len(best["overlap"]) / max(1, len(source_words)), len(best["overlap"]))
        tied = [candidate for candidate in ranked
                if (len(candidate["overlap"]) / max(1, len(source_words)), len(candidate["overlap"])) == best_score]
        if len(tied) != 1:
            audits.append(MultiSourceAudit(
                item.term_en, "unresolved", "ambiguous_context_score",
                candidate_count=len(candidates),
            ))
            continue
        entries.append(TermbaseEntry(
            term_en=item.term_en.strip(), term_ar=best["ar"],
            definition_en=item.definition_en.strip(), approved_by=MULTISOURCE_POLICY_ID,
        ))
        audits.append(MultiSourceAudit(
            item.term_en, "accepted", "unique_highest_definition_overlap",
            source=best["source"], concept_uri=best["uri"], term_ar=best["ar"],
            domain_item_ids=tuple(best["domain"]), context_overlap=best["overlap"],
            candidate_count=len(candidates), license=best["license"],
            source_release=best["release"], source_hash=best["hash"],
        ))
    return MultiSourceCompilation(tuple(entries), tuple(audits))

