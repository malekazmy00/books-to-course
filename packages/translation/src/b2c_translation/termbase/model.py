"""Per-book locked termbase + named-entity table (D-028 principle 5).

The termbase plays the role of a TBX termbase in a CAT tool: it is built before
translation from the book's own candidates, every Arabic equivalent is a *proposal*
with provenance until a human approves it (Gate G3, T-TR-01), and only approved
entries reach ``UnitTranslator`` (``locked_terms()`` -> ``terms``, ``entity_table()`` ->
``entities``, ``hint_terms()`` -> ``hints``) and the ``terminology`` / ``named_entity`` /
``terminology_hint`` QA checks.

Entry tiers (v3, D-039): an approved entry is ``locked`` (checked strictly; every approval
before v3) or ``hint`` (a preferred rendering for the prompt, checked only as a minor signal);
a rejected entry is ``dropped``. ``TermRecord.tier`` stores ``None`` for locked, and fields
added in v3 are left out of the serialised artifact while unset, so an artifact written before
v3 re-serialises byte for byte (same SHA-256, same approved digest).

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
from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

ARTIFACT_FORMAT = "b2c.book-termbase"
ARTIFACT_FORMAT_VERSION = 1

CandidateKind = Literal["term", "acronym", "person", "organization", "place", "work"]
Tier = Literal["locked", "hint", "dropped"]
APPROVAL_TIERS: frozenset[str] = frozenset({"locked", "hint"})
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


def _omit_unset(data: dict, fields: tuple[str, ...]) -> dict:
    """Leave v3 fields out of the serialised form while they are unset (artifact compatibility)."""
    for name in fields:
        if data.get(name) is None:
            data.pop(name, None)
    return data


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
    # v3: the termhood classifier's suggestion (``None`` = not classified) and its reason.
    termhood: Tier | None = None
    termhood_reason: str | None = None

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler) -> dict:
        return _omit_unset(handler(self), ("termhood", "termhood_reason"))


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
    # v3: ``None`` = locked (every approval before v3); "hint" = preferred rendering only.
    tier: Literal["hint"] | None = None
    # D-047: other acceptable spellings of an approved name; accepted by the check, never prompted.
    accepted_variants: list[str] = []

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler) -> dict:
        data = _omit_unset(handler(self), ("tier",))
        if not data.get("accepted_variants"):
            data.pop("accepted_variants", None)     # absent while empty: older artifacts keep their bytes
        return data

    @property
    def effective_tier(self) -> Tier | None:
        """locked / hint for an approved entry, dropped for a rejected one, None while pending."""
        if self.status == "approved":
            return self.tier or "locked"
        return "dropped" if self.status == "rejected" else None


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

    def _approved(self, kinds: frozenset[str], tier: str = "locked") -> dict[str, str]:
        return {r.source: r.target for r in self.entries
                if r.status == "approved" and r.approved_by and r.target and r.kind in kinds
                and r.effective_tier == tier}

    def locked_terms(self) -> dict[str, str]:
        """Approved locked terms and acronyms only: UnitTranslator(terms=...)."""
        return self._approved(TERM_KINDS)

    def entity_table(self) -> dict[str, str]:
        """Approved locked people, organisations, places and works only: UnitTranslator(entities=...)."""
        return self._approved(ENTITY_KINDS)

    def hint_terms(self) -> dict[str, str]:
        """Approved hint entries of any kind: UnitTranslator(hints=...), never checked strictly."""
        return self._approved(TERM_KINDS | ENTITY_KINDS, tier="hint")

    def entity_variants(self) -> dict[str, tuple[str, ...]]:
        """Accepted spellings of approved locked names (D-047): widen the check, never the prompt."""
        return {r.source: tuple(r.accepted_variants) for r in self.entries
                if r.status == "approved" and r.approved_by and r.target and r.kind in ENTITY_KINDS
                and r.effective_tier == "locked" and r.accepted_variants}

    def forbidden_variants(self) -> dict[str, tuple[str, ...]]:
        return {r.source: tuple(r.forbidden_variants) for r in self.entries
                if r.status == "approved" and r.effective_tier == "locked" and r.forbidden_variants}

    def forbidden_hits(self, source_text: str, target_text: str) -> list[tuple[str, str]]:
        """(source term, forbidden variant) pairs: the term occurs in the source and a
        forbidden rendering occurs in the target."""
        hits = []
        target = target_key(target_text)
        for record in self.entries:
            if record.status != "approved" or record.effective_tier != "locked" or not record.forbidden_variants:
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
                tier: str = "locked", accepted_variants: Sequence[str] = (),
                now: datetime | None = None) -> TermRecord:
        """Approve ``source`` as a ``locked`` (default) or ``hint`` entry; noise is ``reject``ed."""
        reviewer = _check_reviewer(reviewer)
        if tier not in APPROVAL_TIERS:
            raise TermbaseError(f"an approval tier is 'locked' or 'hint', not {tier!r}; reject noise instead")
        if tier == "hint" and (forbid_other_proposals or any(v.strip() for v in forbidden_variants)):
            raise TermbaseError("a hint is not checked strictly, so it takes no forbidden variants")
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
        if record.status == "approved" and record.effective_tier != tier and not replace:
            raise TermbaseError(f"{record.source!r} already approved as {record.effective_tier}; pass replace=True")
        variants = [v.strip() for v in forbidden_variants if v.strip()]
        if forbid_other_proposals:
            variants += [p.target for p in record.proposals if target_key(p.target) != target_key(target)]
        variants = list(dict.fromkeys(variants))
        if target_key(target) in {target_key(v) for v in variants}:
            raise TermbaseError("the approved target is one of its forbidden variants")
        accepted = list(dict.fromkeys(_WS.sub(" ", unicodedata.normalize("NFC", v)).strip()
                                      for v in accepted_variants if v.strip()))
        if accepted:
            if record.kind not in ENTITY_KINDS or tier != "locked":
                raise TermbaseError("accepted variants are for locked names (people, places, organisations, works)")
            if any(_LATIN.search(v) for v in accepted):
                raise TermbaseError("an accepted variant contains Latin letters")
            if target_key(target) in {target_key(v) for v in accepted}:
                raise TermbaseError("an accepted variant repeats the approved target")
            if {target_key(v) for v in accepted} & {target_key(v) for v in variants}:
                raise TermbaseError("an accepted variant is also a forbidden variant")
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
        record.tier = "hint" if tier == "hint" else None
        record.accepted_variants = accepted
        record.rejected_by = record.rejected_at = record.rejection_reason = None
        return record

    def reject(self, source: str, reviewer: str, reason: str, *, now: datetime | None = None) -> TermRecord:
        reviewer = _check_reviewer(reviewer)
        record = self.get(source)
        record.status = "rejected"
        record.target = None
        record.approved_by = None
        record.approved_at = None
        record.tier = None
        record.accepted_variants = []
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
        """Approved terms and acronyms (locked and hint) as ``TermbaseEntry`` contracts."""
        return [TermbaseEntry(term_en=r.source, term_ar=r.target,
                              definition_en=r.candidate.definition_context or r.source,
                              forbidden_variants_ar=list(r.forbidden_variants),
                              do_not_translate=r.do_not_translate, approved_by=r.approved_by,
                              tier=r.effective_tier, accepted_variants_ar=list(r.accepted_variants))
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
