"""Licensed AGROVOC EN–AR candidate lookup for Phase 7 translation.

AGROVOC is an agriculture-focused source. Callers must explicitly attest that the
book domain matches; unresolved or ambiguous candidates are omitted from injection.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from b2c_contracts import GlossaryTerm, TermbaseEntry

AGROVOC_RELEASE = "2026-09-07"
AGROVOC_LICENSE = "CC BY IGO 3.0"
AGROVOC_DATA_SHA256 = (
    "521087c43c2046aec42a1e4f8e7eb059441e18c202d9efb7dc7e0b7c81bddf9a"
)
POLICY_ID = "g3-policy-agrovoc-en-ar-v1"
DEFAULT_TERMBASE_DIR = Path("/mnt/wsl/b2cdata/eval-datasets/termbases")
AGROVOC_RELATIVE_PATH = Path("agrovoc/agrovoc_en_ar_candidates.jsonl")


def termbase_dir() -> Path:
    """Root of the licensed termbase releases (env ``B2C_TERMBASE_DIR``)."""
    return Path(os.environ.get("B2C_TERMBASE_DIR") or DEFAULT_TERMBASE_DIR)


def default_agrovoc_dataset() -> Path:
    return termbase_dir() / AGROVOC_RELATIVE_PATH


# Kept for callers that imported the constant; resolved once at import time.
DEFAULT_DATASET = default_agrovoc_dataset()


def verify_sha256(actual: str, expected: str | None, *, source: str, path: str | Path) -> None:
    """Fail closed when a loaded termbase is not the stamped release."""
    if expected is not None and actual != expected:
        raise ValueError(
            f"{source} dataset SHA-256 mismatch for {path}: expected {expected}, got {actual}; "
            "refusing to use an unverified termbase (pass expected_sha256=None only for "
            "non-release data such as test fixtures)"
        )

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[\w]+", re.UNICODE)
_STOPWORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "is",
    "of", "on", "or", "the", "to", "with", "that", "which", "this", "these",
    "those", "be", "are", "was", "were", "it", "its",
})


def normalize_term(value: str) -> str:
    return _SPACE.sub(" ", value).strip().casefold()


def _content_words(value: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(value) if len(w) > 2 and w.casefold() not in _STOPWORDS}


@dataclass(frozen=True, slots=True)
class AgrovocConcept:
    uri: str
    en_preferred: tuple[str, ...]
    en_alternates: tuple[str, ...]
    ar_preferred: tuple[str, ...]
    definitions: dict[str, tuple[str, ...]]
    modified: str
    license: str


@dataclass(frozen=True, slots=True)
class TermCandidateAudit:
    term_en: str
    status: str
    reason: str
    concept_uri: str | None = None
    term_ar: str | None = None
    context_overlap: tuple[str, ...] = ()
    source_hash: str = AGROVOC_DATA_SHA256
    source_release: str = AGROVOC_RELEASE
    license: str = AGROVOC_LICENSE
    policy_id: str = POLICY_ID


@dataclass(frozen=True, slots=True)
class AgrovocCompilation:
    entries: tuple[TermbaseEntry, ...]
    audit: tuple[TermCandidateAudit, ...]


class AgrovocIndex:
    def __init__(self, concepts: list[AgrovocConcept], *, source_sha256: str = AGROVOC_DATA_SHA256):
        # SHA-256 of the bytes actually loaded; recorded in every audit row.
        self.source_sha256 = source_sha256
        self._by_label: dict[str, list[AgrovocConcept]] = {}
        for concept in concepts:
            for label in (*concept.en_preferred, *concept.en_alternates):
                normalized = normalize_term(label)
                if normalized:
                    self._by_label.setdefault(normalized, []).append(concept)

    @classmethod
    def from_jsonl(
        cls, path: str | Path | None = None, *, expected_sha256: str | None = AGROVOC_DATA_SHA256,
    ) -> "AgrovocIndex":
        """Load the dataset and verify it against the stamped release hash.

        The pin is on by default; ``expected_sha256=None`` is an explicit opt-out
        for non-release data. Either way the loaded bytes' hash is recorded.
        """
        resolved = Path(default_agrovoc_dataset() if path is None else path).resolve()
        stat = resolved.stat()
        index = _load_index(str(resolved), stat.st_mtime_ns, stat.st_size)
        verify_sha256(index.source_sha256, expected_sha256, source="AGROVOC", path=resolved)
        return index

    def lookup(self, english_term: str) -> tuple[AgrovocConcept, ...]:
        return tuple(self._by_label.get(normalize_term(english_term), ()))


@lru_cache(maxsize=2)
def _load_index(path: str, _mtime_ns: int, _size: int) -> AgrovocIndex:
    concepts: list[AgrovocConcept] = []
    raw = Path(path).read_bytes()
    # Split on "\n" only: str.splitlines() would also split on U+2028 inside JSON strings.
    for line in raw.decode("utf-8").split("\n"):
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        if row.get("license") != AGROVOC_LICENSE:
            raise ValueError("AGROVOC dataset contains an unexpected license value")
        if not row.get("concept_uri") or not row.get("en_preferred") or not row.get("ar_preferred"):
            raise ValueError("AGROVOC row lacks concept URI or bilingual preferred labels")
        concepts.append(AgrovocConcept(
            uri=row["concept_uri"],
            en_preferred=tuple(row["en_preferred"]),
            en_alternates=tuple(row.get("en_alternates", ())),
            ar_preferred=tuple(row["ar_preferred"]),
            definitions={lang: tuple(values) for lang, values in row.get("definitions", {}).items()},
            modified=row.get("modified", ""),
            license=row["license"],
        ))
    return AgrovocIndex(concepts, source_sha256=hashlib.sha256(raw).hexdigest())


def compile_agrovoc_termbase(
    glossary: list[GlossaryTerm] | tuple[GlossaryTerm, ...],
    index: AgrovocIndex,
    *,
    agriculture_domain_confirmed: bool,
    minimum_context_overlap: int = 2,
) -> AgrovocCompilation:
    """Compile only unique, context-supported AGROVOC matches as machine-approved entries.

    This is an opt-in, domain-specific policy. It never invents a mapping. Conflicting,
    out-of-domain, or weakly supported terms remain unresolved and are not injected.
    """
    if minimum_context_overlap < 1:
        raise ValueError("minimum_context_overlap must be positive")
    entries: list[TermbaseEntry] = []
    audits: list[TermCandidateAudit] = []
    seen: set[str] = set()
    for item in glossary:
        key = normalize_term(item.term_en)
        if not key or key in seen:
            audits.append(TermCandidateAudit(item.term_en, "unresolved", "duplicate_or_empty_glossary_term"))
            continue
        seen.add(key)
        if not agriculture_domain_confirmed:
            audits.append(TermCandidateAudit(item.term_en, "unresolved", "domain_not_confirmed"))
            continue
        matches = index.lookup(item.term_en)
        if len(matches) != 1:
            audits.append(TermCandidateAudit(
                item.term_en, "unresolved",
                "no_exact_agrovoc_label_match" if not matches else "ambiguous_agrovoc_label",
            ))
            continue
        concept = matches[0]
        if len(concept.ar_preferred) != 1:
            audits.append(TermCandidateAudit(item.term_en, "unresolved", "ambiguous_arabic_preferred_label", concept.uri))
            continue
        en_definitions = concept.definitions.get("en", ())
        ar_definitions = concept.definitions.get("ar", ())
        if not en_definitions or not ar_definitions or not item.definition_en.strip():
            audits.append(TermCandidateAudit(item.term_en, "unresolved", "bilingual_definition_evidence_missing", concept.uri))
            continue
        source_words = _content_words(item.definition_en)
        definition_words = _content_words(" ".join(en_definitions))
        overlap = tuple(sorted(source_words & definition_words))
        if len(overlap) < minimum_context_overlap:
            audits.append(TermCandidateAudit(item.term_en, "unresolved", "context_definition_mismatch", concept.uri,
                                             context_overlap=overlap))
            continue
        arabic = concept.ar_preferred[0]
        entries.append(TermbaseEntry(
            term_en=item.term_en.strip(),
            term_ar=arabic,
            definition_en=item.definition_en.strip(),
            approved_by=POLICY_ID,
        ))
        audits.append(TermCandidateAudit(
            item.term_en, "accepted", "unique_label_and_contextual_definition_match",
            concept_uri=concept.uri, term_ar=arabic, context_overlap=overlap,
        ))
    source_hash = getattr(index, "source_sha256", AGROVOC_DATA_SHA256)
    return AgrovocCompilation(tuple(entries), tuple(replace(a, source_hash=source_hash) for a in audits))

