"""Per-book locked termbase + named-entity table (D-028 principle 5).

The termbase plays the role of a TBX termbase in a CAT tool: it is built before
translation from the book's own candidates, every Arabic equivalent is a *proposal*
with provenance until a human approves it (Gate G3, T-TR-01), and only approved
entries reach ``UnitTranslator`` (``locked_terms()`` -> ``terms``, ``entity_table()`` ->
``entities``) and the ``terminology`` / ``named_entity`` QA checks.

Artifact: canonical JSON (sorted keys, UTF-8) with ``format``/``format_version`` and a
SHA-256 over the content without the hash itself; ``from_json`` refuses a mismatch.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Literal

from b2c_contracts import TermbaseEntry
from pydantic import BaseModel, ConfigDict, Field

ARTIFACT_FORMAT = "b2c.book-termbase"
ARTIFACT_FORMAT_VERSION = 1

CandidateKind = Literal["term", "acronym", "person", "organization", "place", "work"]
TERM_KINDS: frozenset[str] = frozenset({"term", "acronym"})
ENTITY_KINDS: frozenset[str] = frozenset({"person", "organization", "place", "work"})

Provenance = Literal["glossary", "agrovoc", "wikidata", "prior_termbase", "translation_memory",
                     "gateway_proposer", "transliteration"]
# Lower = preferred. (a) established sources, (b) memory, (c) model proposal, (d) rules.
PROVENANCE_PRIORITY: dict[str, int] = {
    "glossary": 0, "agrovoc": 1, "wikidata": 2,
    "prior_termbase": 3, "translation_memory": 4,
    "gateway_proposer": 5, "transliteration": 6,
}
MACHINE_REVIEWER_PREFIXES = ("g3-policy-",)

_LATIN = re.compile(r"[A-Za-z]")
_WS = re.compile(r"\s+")


class TermbaseError(ValueError):
    """A G3 rule or artifact-integrity violation."""


def source_key(source: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFC", source)).strip().casefold()


def target_key(target: str) -> str:
    text = unicodedata.normalize("NFKC", target)
    text = "".join(ch for ch in text if not unicodedata.combining(ch) and ch != "ـ")
    return _WS.sub(" ", text).strip()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TermCandidate(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source: str = Field(min_length=1)
    kind: CandidateKind
    frequency: int = Field(ge=0)          # translatable units containing the candidate
    sections: int = Field(default=0, ge=0)  # distinct section paths among those units
    unit_ids: tuple[str, ...] = ()        # sample (first occurrences, at most 5)
    definition_context: str | None = None
    expansion: str | None = None          # acronyms only
    signals: tuple[str, ...] = ()         # e.g. "capitalized", "defined", "c_value", "index"
    score: float = 0.0


class Proposal(_Model):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(min_length=1)
    provenance: Provenance
    reference: str | None = None          # concept URI, glossary/termbase sha, model@prompt version
    source_approved_by: str | None = None  # approval recorded by the upstream source, if any
    note: str | None = None


class TermRecord(_Model):
    source: str
    kind: CandidateKind
    candidate: TermCandidate
    proposals: list[Proposal] = []
    status: Literal["pending", "approved", "rejected"] = "pending"
    target: str | None = None
    do_not_translate: bool = False
    forbidden_variants: list[str] = []
    allow_shared_target: bool = False
    approved_by: str | None = None  # None => not approved yet (Gate G3)
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None


class ConsistencyIssue(_Model):
    kind: Literal["duplicate_source", "shared_target", "forbidden_target", "approval_incomplete"]
    sources: tuple[str, ...]
    target: str | None = None


def _check_reviewer(reviewer: str | None) -> str:
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise TermbaseError("a reviewer is required (Gate G3)")
    if reviewer.startswith(MACHINE_REVIEWER_PREFIXES):
        raise TermbaseError(f"{reviewer!r} is a machine policy; G3 approval must be human")
    return reviewer


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BookTermbase(_Model):
    format: Literal["b2c.book-termbase"] = ARTIFACT_FORMAT
    format_version: int = ARTIFACT_FORMAT_VERSION
    book_id: str = Field(min_length=1)
    source_language: str
    target_language: str
    extractor_version: str | None = None
    entries: list[TermRecord] = []

    # ------------------------------------------------------------------ construction
    @classmethod
    def new(cls, book_id: str, *, source_language: str, target_language: str,
            extractor_version: str | None = None) -> BookTermbase:
        return cls(book_id=book_id, source_language=source_language, target_language=target_language,
                   extractor_version=extractor_version)

    def add_candidate(self, candidate: TermCandidate, proposals: Iterable[Proposal]) -> TermRecord:
        if self._find(candidate.source) is not None:
            raise TermbaseError(f"duplicate termbase source: {candidate.source!r}")
        ordered = _order_proposals(proposals)
        record = TermRecord(source=candidate.source, kind=candidate.kind, candidate=candidate,
                            proposals=ordered)
        self.entries.append(record)
        self.entries.sort(key=lambda r: (source_key(r.source), r.source))
        return record

    # ------------------------------------------------------------------ lookup
    def _find(self, source: str) -> TermRecord | None:
        key = source_key(source)
        return next((r for r in self.entries if source_key(r.source) == key), None)

    def get(self, source: str) -> TermRecord:
        record = self._find(source)
        if record is None:
            raise TermbaseError(f"unknown termbase source: {source!r}")
        return record

    def pending(self) -> list[TermRecord]:
        return [r for r in self.entries if r.status == "pending"]

    def _approved(self, kinds: frozenset[str]) -> dict[str, str]:
        return {r.source: r.target for r in self.entries
                if r.status == "approved" and r.approved_by and r.target and r.kind in kinds}

    def locked_terms(self) -> dict[str, str]:
        """Approved terms and acronyms only: UnitTranslator(terms=...)."""
        return self._approved(TERM_KINDS)

    def entity_table(self) -> dict[str, str]:
        """Approved people, organisations, places and works only: UnitTranslator(entities=...)."""
        return self._approved(ENTITY_KINDS)

    def forbidden_variants(self) -> dict[str, tuple[str, ...]]:
        return {r.source: tuple(r.forbidden_variants) for r in self.entries
                if r.status == "approved" and r.forbidden_variants}

    def forbidden_hits(self, source_text: str, target_text: str) -> list[tuple[str, str]]:
        """(source term, forbidden variant) pairs: the term occurs in the source and a
        forbidden rendering occurs in the target."""
        hits = []
        target = target_key(target_text)
        for record in self.entries:
            if record.status != "approved" or not record.forbidden_variants:
                continue
            if not _source_occurs(source_text, record.source):
                continue
            for variant in record.forbidden_variants:
                if target_key(variant) and target_key(variant) in target:
                    hits.append((record.source, variant))
        return hits

    # ------------------------------------------------------------------ G3 decisions
    def approve(self, source: str, target: str, reviewer: str, *, replace: bool = False,
                forbidden_variants: Sequence[str] = (), forbid_other_proposals: bool = False,
                allow_shared_target: bool = False, do_not_translate: bool = False,
                now: datetime | None = None) -> TermRecord:
        reviewer = _check_reviewer(reviewer)
        record = self.get(source)
        target = _WS.sub(" ", unicodedata.normalize("NFC", target or "")).strip()
        if not target:
            raise TermbaseError("an approved target must not be empty")
        if do_not_translate:
            if source_key(target) != source_key(record.source):
                raise TermbaseError("do_not_translate keeps the source form as target")
        elif _LATIN.search(target):
            raise TermbaseError("target contains Latin letters; use do_not_translate to keep the source form")
        if record.status == "approved" and record.target != target and not replace:
            raise TermbaseError(f"{record.source!r} already approved as {record.target!r}; pass replace=True")
        variants = [v.strip() for v in forbidden_variants if v.strip()]
        if forbid_other_proposals:
            variants += [p.target for p in record.proposals if target_key(p.target) != target_key(target)]
        variants = list(dict.fromkeys(variants))
        if target_key(target) in {target_key(v) for v in variants}:
            raise TermbaseError("the approved target is one of its forbidden variants")
        if not allow_shared_target:
            for other in self.entries:
                if (other is not record and other.status == "approved" and other.target
                        and target_key(other.target) == target_key(target)):
                    raise TermbaseError(
                        f"target {target!r} already locked for {other.source!r}; "
                        "pass allow_shared_target=True if this is intended")
        record.status = "approved"
        record.target = target
        record.do_not_translate = do_not_translate
        record.forbidden_variants = variants
        record.allow_shared_target = allow_shared_target
        record.approved_by = reviewer
        record.approved_at = now or _now()
        record.rejected_by = record.rejected_at = record.rejection_reason = None
        return record

    def reject(self, source: str, reviewer: str, reason: str, *, now: datetime | None = None) -> TermRecord:
        reviewer = _check_reviewer(reviewer)
        record = self.get(source)
        record.status = "rejected"
        record.target = None
        record.approved_by = None
        record.approved_at = None
        record.rejected_by = reviewer
        record.rejected_at = now or _now()
        record.rejection_reason = reason.strip() or "rejected"
        return record

    # ------------------------------------------------------------------ checks
    def consistency_issues(self) -> list[ConsistencyIssue]:
        issues: list[ConsistencyIssue] = []
        by_source: dict[str, list[str]] = {}
        for r in self.entries:
            by_source.setdefault(source_key(r.source), []).append(r.source)
        issues += [ConsistencyIssue(kind="duplicate_source", sources=tuple(v))
                   for v in by_source.values() if len(v) > 1]
        approved = [r for r in self.entries if r.status == "approved"]
        by_target: dict[str, list[TermRecord]] = {}
        for r in approved:
            if not (r.target and r.approved_by and r.approved_at):
                issues.append(ConsistencyIssue(kind="approval_incomplete", sources=(r.source,), target=r.target))
                continue
            by_target.setdefault(target_key(r.target), []).append(r)
            if target_key(r.target) in {target_key(v) for v in r.forbidden_variants}:
                issues.append(ConsistencyIssue(kind="forbidden_target", sources=(r.source,), target=r.target))
        for records in by_target.values():
            # intended reuse: every record but (at most) the first owner allows sharing
            if len(records) > 1 and sum(not r.allow_shared_target for r in records) > 1:
                issues.append(ConsistencyIssue(kind="shared_target",
                                               sources=tuple(r.source for r in records),
                                               target=records[0].target))
        return issues

    # ------------------------------------------------------------------ contracts / artifact
    def to_contract_entries(self) -> list[TermbaseEntry]:
        return [TermbaseEntry(term_en=r.source, term_ar=r.target,
                              definition_en=r.candidate.definition_context or r.source,
                              forbidden_variants_ar=list(r.forbidden_variants),
                              do_not_translate=r.do_not_translate, approved_by=r.approved_by)
                for r in self.entries
                if r.status == "approved" and r.target and r.kind in TERM_KINDS]

    def _content(self) -> dict:
        return self.model_dump(mode="json")

    def sha256(self) -> str:
        return hashlib.sha256(_canonical(self._content()).encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        doc = self._content()
        doc["sha256"] = self.sha256()
        return _canonical(doc)

    @classmethod
    def from_json(cls, raw: str | bytes) -> BookTermbase:
        doc = json.loads(raw)
        if doc.get("format") != ARTIFACT_FORMAT or doc.get("format_version") != ARTIFACT_FORMAT_VERSION:
            raise TermbaseError(f"unsupported termbase artifact {doc.get('format')!r} "
                                f"v{doc.get('format_version')!r}")
        expected = doc.pop("sha256", None)
        try:
            termbase = cls.model_validate(doc)
        except ValueError as exc:
            raise TermbaseError(f"invalid termbase artifact: {exc}") from exc
        if expected != termbase.sha256():
            raise TermbaseError("termbase artifact SHA-256 mismatch")
        return termbase


def _canonical(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _order_proposals(proposals: Iterable[Proposal]) -> list[Proposal]:
    seen: set[tuple[str, str]] = set()
    out = []
    for p in sorted(proposals, key=lambda p: PROVENANCE_PRIORITY[p.provenance]):  # stable
        key = (p.provenance, target_key(p.target))
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _source_occurs(text: str, term: str) -> bool:
    words = [re.escape(w) for w in term.split()]
    if not words:
        return False
    pattern = r"(?<![\w])" + r"[\s-]+".join(words) + r"(?:s|es|'s|’s)?(?![\w])"
    return re.search(pattern, text, re.I) is not None
