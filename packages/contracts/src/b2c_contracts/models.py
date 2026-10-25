"""b2c_contracts — single source of truth for data exchanged between layers.

Rules:
- No business logic here. No imports from other project packages.
- Any change => bump SCHEMA_VERSION and update docs/02-CONTRACTS.md.
- All datetimes are timezone-aware UTC.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.19.0"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str = SCHEMA_VERSION


def _utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() != timezone.utc.utcoffset(v):
        raise ValueError("datetime must be timezone-aware UTC")
    return v


# ---------------------------------------------------------------- idempotency
def stage_key(
    stage: str,
    stage_version: str,
    input_shas: list[str],
    config: dict[str, Any],
    model_id: str | None = None,
    prompt_version: str | None = None,
) -> str:
    """Deterministic idempotency key: same inputs+config => same key."""
    payload = {
        "stage": stage,
        "stage_version": stage_version,
        "inputs": sorted(input_shas),
        "config": config,
        "model_id": model_id,
        "prompt_version": prompt_version,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ArtifactRef(Contract):
    stage: str
    key: str  # stage_key(...)
    uri: str  # s3://artifacts/{course_id}/{stage}/{key}/...
    sha256: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def check_created_utc(cls, v: datetime) -> datetime:
        return _utc(v)


# ---------------------------------------------------------------- stage acceptance (R1)
# Audit 08 C1: an output becomes an input of the next stage only through an explicit, recorded
# promotion, never by merely existing. Legacy outputs made before any gate are quarantined.
GateKind = Literal["deterministic", "semantic", "human"]
StageOutputStatus = Literal["candidate", "accepted", "rejected", "quarantined"]
# Allowed status changes. Legacy output is re-gated (back to candidate), never promoted directly;
# an accepted output can only be revoked; a rejected one is final (a fix is a new artifact).
STAGE_OUTPUT_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"accepted", "rejected"}),
    "accepted": frozenset({"rejected"}),
    "rejected": frozenset(),
    "quarantined": frozenset({"candidate"}),
}
_GATE_ID = r"^[a-z0-9][a-z0-9._-]{0,119}$"
# commit sha of the running image; "-dirty" = built from a modified tree; "unknown" = unstamped
CODE_REVISION_PATTERN = r"^([0-9a-f]{40}(-dirty)?|unknown)$"


class GateResult(Contract):
    """One gate decision about one stored artifact (P2)."""
    stage: str
    artifact: ArtifactRef
    gate_id: str = Field(pattern=_GATE_ID)
    gate_version: str = Field(min_length=1, max_length=120)  # policy version / digest of the checks
    kind: GateKind
    passed: bool
    metrics: dict[str, Any] = {}
    evidence_ref: str | None = None  # s3:// URI (or registry id) of the evidence
    decider: Literal["system", "human"]
    decided_by: str = Field(min_length=1, max_length=120)  # policy/system id, or reviewer id
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def check_decided_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def _gate_invariants(self) -> "GateResult":
        if self.stage != self.artifact.stage:
            raise ValueError("gate stage must match the artifact stage")
        if (self.kind == "human") != (self.decider == "human"):
            raise ValueError("a human gate needs a human decision; machine gates are decided by the system")
        return self


class ProducerIdentity(Contract):
    """A model/prompt (or tool) identity that contributed to a stage output."""
    role: str = Field(min_length=1, max_length=60)  # engine | qe | judge | proposer | tool ...
    provider: str | None = None
    model_id: str = Field(min_length=1)
    revision: str | None = None
    prompt_version: str | None = None


class StageOutputRecord(Contract):
    """Registry view of one stage output: lineage, producing code, gates and lifecycle status."""
    course_id: str
    artifact: ArtifactRef
    inputs: list[ArtifactRef] = []  # parent artifacts (wasDerivedFrom)
    code_revision: str = Field(pattern=CODE_REVISION_PATTERN)
    config: dict[str, Any] = {}
    identities: list[ProducerIdentity] = []
    required_gates: list[str] = []
    gate_results: list[GateResult] = []
    status: StageOutputStatus
    status_reason: str | None = None
    status_changed_at: datetime
    status_changed_by: str = Field(min_length=1, max_length=120)

    @property
    def stage(self) -> str:
        return self.artifact.stage

    @property
    def stage_key(self) -> str:
        return self.artifact.key

    @field_validator("status_changed_at")
    @classmethod
    def check_changed_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    def latest_gate_results(self) -> dict[str, GateResult]:
        """The most recent result of each gate (a re-run gate supersedes earlier results)."""
        latest: dict[str, GateResult] = {}
        for result in self.gate_results:
            seen = latest.get(result.gate_id)
            if seen is None or result.decided_at >= seen.decided_at:
                latest[result.gate_id] = result
        return latest

    @model_validator(mode="after")
    def _status_invariants(self) -> "StageOutputRecord":
        for result in self.gate_results:
            if (result.artifact.uri, result.artifact.key, result.artifact.sha256) != (
                    self.artifact.uri, self.artifact.key, self.artifact.sha256):
                raise ValueError("gate results must belong to the record's artifact")
        if self.status in ("rejected", "quarantined") and not (self.status_reason or "").strip():
            raise ValueError(f"a {self.status} output needs a status_reason")
        if self.status == "accepted":
            if not self.required_gates:
                raise ValueError("acceptance needs at least one required gate")
            latest = self.latest_gate_results()
            missing = [g for g in self.required_gates if g not in latest or not latest[g].passed]
            if missing:
                raise ValueError(f"acceptance needs every required gate passed: {missing}")
            if self.code_revision == "unknown" or self.code_revision.endswith("-dirty"):
                raise ValueError("acceptance needs output produced by a clean, known code revision (P8)")
        return self


# ---------------------------------------------------------------- L1 ingestion
RightsStatus = Literal["cleared", "pending", "internal_test_only"]


class BookUpload(Contract):
    book_id: str
    role: Literal["spine", "supplementary"]
    file_sha256: str
    mime: Literal[
        "application/pdf",
        "application/epub+zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    title: str
    rights_status: RightsStatus  # D-002
    ocr_language: Literal["en", "ar"] = "en"


class PageRef(Contract):
    book_id: str
    page_no: int = Field(ge=1)  # physical page index
    printed_label: str | None = None  # page number as printed
    bbox: tuple[float, float, float, float] | None = None


BlockType = Literal[
    "heading", "paragraph", "list_item", "table", "figure", "caption",
    "equation", "code", "footnote", "page_header", "page_footer",
]


class Block(Contract):
    block_id: str  # f"{book_id}:{page}:{seq}"
    type: BlockType
    level: int | None = None  # heading level
    text: str
    refs: list[PageRef] = Field(min_length=1)
    section_path: list[str]
    ocr: bool
    confidence: float | None = Field(default=None, ge=0, le=1)


class ParsedBook(Contract):
    book_id: str
    title: str
    role: Literal["spine", "supplementary"]
    page_count: int = Field(ge=1)
    toc_source: Literal["pdf_outline", "epub_nav", "headings", "llm_inferred"]
    blocks: list[Block]
    parser: dict[str, str]  # {"name": "docling", "version": "...", "options_hash": "..."}


# ---------------------------------------------------------------- L1 ingestion v2 (1.3.0)
# R3 / docs/audits/2026-10-24/01-ingest.md §5-6. A v2 block is a logical unit (paragraph,
# heading, note, entry), not a page slice: it may span pages and columns. Nothing the parser
# saw is dropped: running heads and page numbers are kept as furniture-zone blocks, notes,
# bibliography, TOC and index entries have explicit types. `raw_text` is the parser's text
# (fragments joined by "\n"), `text` the deterministic normalization of it.
PARSED_BOOK_V2_FORMAT = "parsed-book/2"

BlockTypeV2 = Literal[
    "heading", "paragraph", "list_item", "table", "figure", "caption", "equation", "code",
    "footnote", "endnote", "bibliography_entry", "toc_entry", "index_entry",
    "running_header", "running_footer", "page_number",
]
FURNITURE_TYPES = frozenset({"running_header", "running_footer", "page_number"})
NOTE_TYPES = frozenset({"footnote", "endnote"})
Zone = Literal["front_matter", "body", "back_matter", "furniture"]
LevelSource = Literal["pdf_outline", "layout", "numbering", "section_word"]


class NoteRef(Contract):
    """An in-text note marker. It is removed from `BlockV2.text`; `offset` is where it stood."""

    label: str = Field(min_length=1)  # as printed: "87", "*", "a"
    offset: int = Field(ge=0)
    note_id: str | None = None  # None = no note found for this marker (counts against the gate)


class BlockV2(Contract):
    block_id: str  # f"{book_id}:b{seq:05d}", reading order; not tied to a page
    type: BlockTypeV2
    zone: Zone
    level: int | None = Field(default=None, ge=1)  # headings only
    level_source: LevelSource | None = None
    text: str  # normalized
    raw_text: str  # as parsed, fragments joined by "\n"
    refs: list[PageRef] = Field(min_length=1)  # every page/column fragment, in reading order
    section_path: list[str]
    ocr: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    note_label: str | None = None  # printed label of a footnote/endnote block
    note_refs: list[NoteRef] = []
    normalization: list[str] = []  # normalizer steps that changed this block
    quality_flags: list[str] = []  # residual defects detected by the normalizer
    source_items: list[str] = []  # parser item references (e.g. "#/texts/12@b0001") for audit

    @model_validator(mode="after")
    def check_block(self) -> "BlockV2":
        if (self.type == "heading") != (self.level is not None):
            raise ValueError("a heading needs a level and only headings have one")
        if (self.zone == "furniture") != (self.type in FURNITURE_TYPES):
            raise ValueError("zone 'furniture' is exactly the running header/footer/page-number types")
        if self.note_label is not None and self.type not in NOTE_TYPES:
            raise ValueError("note_label is only for footnote/endnote blocks")
        if any(r.offset > len(self.text) for r in self.note_refs):
            raise ValueError("note marker offset outside the block text")
        return self


class Note(Contract):
    note_id: str  # f"{book_id}:n{seq:04d}"
    kind: Literal["footnote", "endnote"]
    label: str = Field(min_length=1)
    block_ids: list[str] = Field(min_length=1)  # the note's own text block(s)
    cited_by: list[str] = []  # blocks whose NoteRef links here


class PageInfo(Contract):
    page_no: int = Field(ge=1)
    printed_label: str | None = None  # PDF /PageLabels (ISO 32000-1 §12.4.2)
    status: Literal["content", "furniture_only", "empty"]
    ocr: bool = False


class OutlineEntry(Contract):
    """A PDF bookmark and the heading block it was reconciled with (None = not found)."""

    title: str
    level: int = Field(ge=1)
    page_no: int = Field(ge=1)
    block_id: str | None = None


class ParsedBookV2(Contract):
    format: Literal["parsed-book/2"] = PARSED_BOOK_V2_FORMAT
    book_id: str
    title: str
    role: Literal["spine", "supplementary"]
    page_count: int = Field(ge=1)
    toc_source: Literal["pdf_outline", "epub_nav", "headings", "llm_inferred"]
    pages: list[PageInfo]
    outline: list[OutlineEntry] = []
    blocks: list[BlockV2]
    notes: list[Note] = []
    parser: dict[str, str]
    normalizer: dict[str, str]  # {"name": ..., "version": ...}

    @model_validator(mode="after")
    def check_book(self) -> "ParsedBookV2":
        if [p.page_no for p in self.pages] != list(range(1, self.page_count + 1)):
            raise ValueError("pages must list every page 1..page_count in order")
        ids = [b.block_id for b in self.blocks]
        if len(set(ids)) != len(ids):
            raise ValueError("block_id values must be unique")
        for b in self.blocks:
            for r in b.refs:
                if r.book_id != self.book_id or r.page_no > self.page_count:
                    raise ValueError(f"block {b.block_id} refers outside this book")
        by_id = {b.block_id: b for b in self.blocks}
        note_ids = {n.note_id for n in self.notes}
        if len(note_ids) != len(self.notes):
            raise ValueError("note_id values must be unique")
        for n in self.notes:
            if any(by_id.get(bid) is None or by_id[bid].type not in NOTE_TYPES for bid in n.block_ids):
                raise ValueError(f"note {n.note_id} must point to footnote/endnote blocks")
            if any(bid not in by_id for bid in n.cited_by):
                raise ValueError(f"note {n.note_id} is cited by an unknown block")
        for b in self.blocks:
            for r in b.note_refs:
                if r.note_id is not None and r.note_id not in note_ids:
                    raise ValueError(f"block {b.block_id} links to unknown note {r.note_id}")
        for e in self.outline:
            if e.block_id is not None and e.block_id not in by_id:
                raise ValueError(f"outline entry {e.title!r} points to an unknown block")
        return self


_V1_TYPE: dict[str, str] = {
    "running_header": "page_header", "running_footer": "page_footer", "page_number": "page_footer",
    "endnote": "footnote", "bibliography_entry": "list_item", "toc_entry": "list_item",
    "index_entry": "list_item",
}


def parsed_book_v1_from_v2(book: ParsedBookV2) -> ParsedBook:
    """Project ParsedBook v2 onto the v1 contract for consumers that have not migrated.

    A pure schema projection (no inference): types outside the v1 set map to their nearest
    v1 type, ids become f"{book_id}:{first_page}:{seq}", removed note markers are put back
    glued at their offsets and note labels are prefixed again ("1. Ibid."), so v1 consumers
    that find markers in text (translation units) keep working.
    """
    blocks: list[Block] = []
    per_page: dict[int, int] = {}
    for b in book.blocks:
        page = b.refs[0].page_no
        seq = per_page.get(page, 0)
        per_page[page] = seq + 1
        text = b.text
        for r in sorted(b.note_refs, key=lambda r: r.offset, reverse=True):
            text = text[: r.offset] + r.label + text[r.offset:]
        if b.note_label is not None:
            text = f"{b.note_label}. {text}"
        blocks.append(Block(
            block_id=f"{book.book_id}:{page}:{seq}", type=_V1_TYPE.get(b.type, b.type),  # type: ignore[arg-type]
            level=b.level, text=text, refs=list(b.refs), section_path=list(b.section_path),
            ocr=b.ocr, confidence=b.confidence,
        ))
    return ParsedBook(
        book_id=book.book_id, title=book.title, role=book.role, page_count=book.page_count,
        toc_source=book.toc_source, blocks=blocks,
        parser={**book.parser, "normalizer": book.normalizer.get("name", ""),
                "normalizer_version": book.normalizer.get("version", ""), "format": "parsed-book/2->1"},
    )


class QualityCheck(Contract):
    """One deterministic gate check. `value=None` means not measurable here (passed=None)."""

    name: str
    value: float | None
    op: Literal["<=", ">=", "=="]
    threshold: float
    passed: bool | None
    detail: str = ""

    @model_validator(mode="after")
    def check_verdict(self) -> "QualityCheck":
        if self.value is None:
            if self.passed is not None:
                raise ValueError("an unmeasured check has no verdict")
            return self
        ok = {"<=": self.value <= self.threshold, ">=": self.value >= self.threshold,
              "==": self.value == self.threshold}[self.op]
        if self.passed is not ok:
            raise ValueError(f"check {self.name}: verdict does not match {self.value} {self.op} {self.threshold}")
        return self


class IngestQualityReport(Contract):
    """Stored next to every ParsedBook v2; `accepted` is False whenever any check fails."""

    book_id: str
    report_version: str
    parsed_sha256: str | None = None
    metrics: dict[str, float | int | None]
    checks: list[QualityCheck]
    accepted: bool
    # 1.19.0 (D-043): the physical pages each per-page check found (review list), by check name
    page_findings: dict[str, list[int]] = {}

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if c.passed is False]

    @model_validator(mode="after")
    def check_acceptance(self) -> "IngestQualityReport":
        if self.accepted and (self.failed or not any(c.passed for c in self.checks)):
            raise ValueError("a report with failed (or no passing) checks cannot be accepted")
        names = {c.name for c in self.checks}
        for name, pages in self.page_findings.items():
            if name not in names:
                raise ValueError(f"page findings for {name!r}, which is not a check of this report")
            if any(p < 1 for p in pages):
                raise ValueError("page findings are 1-based physical page numbers")
        return self


# ---------------------------------------------------------------- L2 outline
class LessonSpan(Contract):
    start_block: str
    end_block: str
    pages: tuple[int, int]


class Lesson(Contract):
    lesson_id: str  # "m03.l02"
    title: str
    order: int = Field(ge=1)
    spine_span: LessonSpan
    provenance: Literal["pdf_outline", "epub_nav", "headings", "llm_inferred"]
    prior_lessons: list[str] = []


class Module(Contract):
    module_id: str
    title: str
    order: int = Field(ge=1)
    lessons: list[Lesson] = Field(min_length=1)


class OutlineApprovalProvenance(Contract):
    """Who approved an outline and whether review was human or policy-based."""
    review_type: Literal["human", "system"]
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=120)
    system_id: str | None = Field(default=None, min_length=1, max_length=120)
    policy_version: str | None = None
    evidence_sha256: str | None = None

    @model_validator(mode="after")
    def _reviewer_kind(self) -> "OutlineApprovalProvenance":
        if self.review_type == "human":
            if not self.reviewer_id or any((self.system_id, self.policy_version, self.evidence_sha256)):
                raise ValueError("human review requires reviewer_id and forbids system provenance")
        elif self.reviewer_id is not None or not all((self.system_id, self.policy_version, self.evidence_sha256)):
            raise ValueError("system review requires policy provenance and forbids reviewer_id")
        return self


class CourseOutline(Contract):
    course_id: str
    spine_book_id: str
    supplementary_book_ids: list[str]
    modules: list[Module] = Field(min_length=1)
    requires_review: bool  # True until a human or approved system policy accepts structure; inferred lessons always require human review
    excluded_prefix_block_ids: list[str] = Field(default_factory=list)
    excluded_prefix_policy: Literal["publisher-front-matter-v1"] | None = None

    @model_validator(mode="after")
    def _review_flag(self) -> "CourseOutline":
        inferred = any(l.provenance == "llm_inferred" for m in self.modules for l in m.lessons)
        if inferred and not self.requires_review:
            raise ValueError("llm_inferred outline must set requires_review=True")
        return self


# ---------------------------------------------------------------- L2 outline v2 (1.5.0)
# R4 / docs/audits/2026-10-24/02-outline.md §5-6. Built from ParsedBook v2: the blocks of the book
# are partitioned into lesson spans and explicitly excluded ranges (zone + reason), so
# included + excluded = 100% of the book by construction. Every node records the evidence it
# came from and keeps the source title next to the cleaned display title. Ids are derived from
# the book's structure (titles), not from block ids, so a re-parse keeps them. A v2 outline is
# never accepted by a flag in itself: acceptance is decided in the stage-output registry (§7) by
# system GateResults (structure gate + independent cross-check; 1.5.1: no human reviewer).
COURSE_OUTLINE_V2_FORMAT = "course-outline/2"
OutlineSource = Literal["pdf_outline", "printed_toc", "heading", "llm_inferred"]
ExclusionReason = Literal[
    "cover", "title_page", "copyright", "dedication", "epigraph", "toc", "list_of_figures",
    "acknowledgments", "foreword", "preface", "about_author", "front_matter",
    "endnotes", "bibliography", "index", "glossary", "appendix", "figure_descriptions", "back_matter",
]
LessonKind = Literal["section", "chapter_intro", "chapter", "front_matter"]  # front_matter: 1.8.0
SizeStatus = Literal["ok", "below_min", "above_max"]
_OUTLINE_ID = r"^[a-z0-9][a-z0-9._-]{0,119}$"
_V2_BLOCK_ID = re.compile(r"^(?P<book>.+):b(?P<seq>\d{5,})$")


def v2_block_seq(block_id: str) -> int:
    """Reading-order position of a ParsedBook v2 block id (f"{book_id}:b{seq:05d}")."""
    m = _V2_BLOCK_ID.match(block_id)
    if m is None:
        raise ValueError(f"not a ParsedBook v2 block id: {block_id!r}")
    return int(m.group("seq"))


def _v2_block_book(block_id: str) -> str:
    m = _V2_BLOCK_ID.match(block_id)
    if m is None:
        raise ValueError(f"not a ParsedBook v2 block id: {block_id!r}")
    return m.group("book")


class OutlineSpanV2(Contract):
    """Inclusive range of ParsedBook v2 blocks (reading order) and the physical pages it touches."""
    start_block: str
    end_block: str
    pages: tuple[int, int]

    @model_validator(mode="after")
    def _span(self) -> "OutlineSpanV2":
        if _v2_block_book(self.start_block) != _v2_block_book(self.end_block):
            raise ValueError("span endpoints belong to different books")
        if v2_block_seq(self.end_block) < v2_block_seq(self.start_block):
            raise ValueError("span is reversed")
        if not 1 <= self.pages[0] <= self.pages[1]:
            raise ValueError("span pages must be 1 <= first <= last")
        return self

    @property
    def seqs(self) -> tuple[int, int]:
        return v2_block_seq(self.start_block), v2_block_seq(self.end_block)


class ExcludedRange(OutlineSpanV2):
    """Blocks that are deliberately not course content, and why (EPUB SSV-like reason)."""
    zone: Literal["front_matter", "body", "back_matter"]
    reason: ExclusionReason
    title: str | None = None  # heading that opened the range, if any
    rule: str = Field(min_length=1, max_length=120)  # the deterministic rule that excluded it


def _check_title(value: str) -> str:
    if not value.strip():
        raise ValueError("title must not be blank")
    if "\n" in value or "\r" in value:
        raise ValueError("a title is a single line")
    return value


class _OutlineNode(Contract):
    order: int = Field(ge=1)
    level: int = Field(ge=1)  # structural depth in the book (1 = part/chapter)
    source: OutlineSource  # evidence for this boundary
    source_title: str  # the title as found in the source (bookmark text or heading block)
    display_title: str  # cleaned single-line title shown to learners and reviewers
    title_source: OutlineSource  # where the display title was taken from
    anchor_block: str  # the heading block that opens the node

    @field_validator("display_title")
    @classmethod
    def _display(cls, v: str) -> str:
        return _check_title(v)


# 1.7.0 (D-036 (1)): figure and table descriptions are lesson content, read in reading order and
# marked so speech can introduce them ("وصف الشكل:"). A chapter's description section that the book
# prints after its (excluded) notes is an extra span of the chapter's last lesson.
LessonExtraRole = Literal["figure_descriptions"]
BlockRole = Literal["figure_caption", "figure_description_heading", "figure_description_marker",
                    "figure_description"]


class LessonExtraSpan(OutlineSpanV2):
    """Blocks of a lesson outside its own span (after it, before the next lesson), read after it."""
    role: LessonExtraRole


class BlockRoleRun(Contract):
    """Inclusive run of a lesson's blocks with the role they play (how speech introduces them)."""
    start_block: str
    end_block: str
    role: BlockRole

    @property
    def seqs(self) -> tuple[int, int]:
        return v2_block_seq(self.start_block), v2_block_seq(self.end_block)


# 1.8.0 (D-036 (2)): the preface, foreword and acknowledgments are lessons. Blocks that do not suit
# listening as they are (a thank-you list of names, a table, a long list of credits, a legal note)
# are flagged so the speech/derived stage adapts them (e.g. summarises the thanks) without dropping
# information: the original text stays in the lesson.
AudioAdaptationReason = Literal["acknowledgements", "table", "long_list", "legal_notice", "reference_note"]


class AudioAdaptation(Contract):
    """Inclusive run of a lesson's blocks to adapt for listening, and why."""
    start_block: str
    end_block: str
    reason: AudioAdaptationReason
    note: str = Field(min_length=1, max_length=300)  # what the adaptation must keep

    @property
    def seqs(self) -> tuple[int, int]:
        return v2_block_seq(self.start_block), v2_block_seq(self.end_block)


class LessonV2(_OutlineNode):
    lesson_id: str = Field(pattern=_OUTLINE_ID)
    kind: LessonKind = "section"
    spine_span: OutlineSpanV2
    extra_spans: list[LessonExtraSpan] = []  # 1.7.0: read after spine_span, in reading order
    word_count: int = Field(ge=0)  # words of instructional blocks in the span (+ extra spans)
    size_status: SizeStatus
    size_exception: str | None = None  # documented reason a lesson is outside the size policy
    subheadings: list[str] = []  # heading blocks inside the lesson that are not lesson boundaries
    prior_lessons: list[str] = []  # empty unless there is evidence (audit 02 P12)
    block_roles: list[BlockRoleRun] = []  # 1.7.0: e.g. figure descriptions, in reading order
    audio_adaptations: list[AudioAdaptation] = []  # 1.8.0: blocks to adapt for listening, kept in place
    # 1.9.0 (D-036 (3)): every lesson is an audio episode. The number is 1-based over the whole course
    # in book order (front-matter lessons included); the title is for audio ("الحلقة 3: ..." is
    # rendered later, after translation). None only in outlines built before 1.9.0.
    episode: int | None = Field(default=None, ge=1)
    episode_title: str | None = None

    @property
    def spans(self) -> list[OutlineSpanV2]:
        """The lesson's blocks in reading order: its own span, then its extra spans."""
        return [self.spine_span, *self.extra_spans]

    @property
    def last_seq(self) -> int:
        return self.spans[-1].seqs[1]

    @field_validator("episode_title")
    @classmethod
    def _episode_title(cls, v: str | None) -> str | None:
        return None if v is None else _check_title(v)

    @model_validator(mode="after")
    def _lesson(self) -> "LessonV2":
        if self.episode is not None and self.episode_title is None:
            raise ValueError("an episode number needs an episode_title")
        if self.size_status != "ok" and not (self.size_exception or "").strip():
            raise ValueError("a lesson outside the size policy needs a size_exception")
        a, b = self.spine_span.seqs
        if not a <= v2_block_seq(self.anchor_block) <= b:
            raise ValueError("lesson anchor must lie inside its span")
        book = _v2_block_book(self.spine_span.start_block)
        previous = b
        for extra in self.extra_spans:
            if _v2_block_book(extra.start_block) != book:
                raise ValueError("an extra span belongs to another book")
            if extra.seqs[0] <= previous:
                raise ValueError("extra spans must come after the lesson's span and after each other")
            previous = extra.seqs[1]
        ranges = [s.seqs for s in self.spans]
        previous = -1
        for run in self.block_roles:
            x, y = run.seqs
            if y < x or x <= previous or not any(p <= x and y <= q for p, q in ranges):
                raise ValueError("block role runs must be ordered and lie inside the lesson's spans")
            previous = y
        previous = -1
        for flag in self.audio_adaptations:
            x, y = flag.seqs
            if y < x or x <= previous or not any(p <= x and y <= q for p, q in ranges):
                raise ValueError("audio adaptation runs must be ordered and lie inside the lesson's spans")
            previous = y
        return self


class ModuleV2(_OutlineNode):
    module_id: str = Field(pattern=_OUTLINE_ID)
    lessons: list[LessonV2] = Field(min_length=1)


class LessonSizePolicy(Contract):
    """Lesson size bounds in words (owner decision Q9; defaults are a proposal)."""
    policy_id: str = Field(min_length=1)
    min_words: int = Field(ge=0)
    max_words: int = Field(ge=1)

    @model_validator(mode="after")
    def _bounds(self) -> "LessonSizePolicy":
        if self.min_words >= self.max_words:
            raise ValueError("min_words must be below max_words")
        return self


class CourseOutlineV2(Contract):
    format: Literal["course-outline/2"] = COURSE_OUTLINE_V2_FORMAT
    course_id: str
    spine_book_id: str
    supplementary_book_ids: list[str] = []
    parsed_sha256: str | None = None  # sha256 of the parsed-book-v2.json it was built from
    block_count: int = Field(ge=1)  # blocks in that ParsedBook v2 (ids are consecutive in reading order)
    builder: dict[str, str]  # {"name": ..., "version": ...}
    structure_source: OutlineSource  # main evidence for the tree
    size_policy: LessonSizePolicy
    modules: list[ModuleV2] = Field(min_length=1)
    excluded_ranges: list[ExcludedRange] = []
    requires_review: Literal[True] = True  # always a draft; acceptance lives in the registry (§7)
    # 1.11.0 (D-037) / 1.12.0 (D-038): episode 00 is the generated course introduction; book lessons
    # are episodes 1..n (files named with zero-padded numbers: 00, 01, 02, ... for MP3 players)
    course_intro_episode: bool = False

    @property
    def lessons(self) -> list[LessonV2]:
        return [lesson for module in self.modules for lesson in module.lessons]

    @property
    def lesson_count(self) -> int:
        return len(self.lessons)

    @model_validator(mode="after")
    def _outline(self) -> "CourseOutlineV2":
        if [m.order for m in self.modules] != list(range(1, len(self.modules) + 1)):
            raise ValueError("module order must be 1..n")
        ids: list[str] = [m.module_id for m in self.modules]
        for m in self.modules:
            if [lesson.order for lesson in m.lessons] != list(range(1, len(m.lessons) + 1)):
                raise ValueError(f"lesson order in {m.module_id} must be 1..n")
            ids.extend(lesson.lesson_id for lesson in m.lessons)
        if len(set(ids)) != len(ids):
            raise ValueError("module and lesson ids must be unique")
        episodes = [lesson.episode for lesson in self.lessons]
        if any(e is not None for e in episodes) and episodes != list(range(1, len(episodes) + 1)):
            raise ValueError("episode numbers must be 1..n over all lessons in reading order (or absent)")
        spans: list[tuple[int, int, str]] = []
        previous_end = -1
        seen: set[str] = set()
        for m in self.modules:
            for lesson in m.lessons:
                a, b = lesson.spine_span.seqs
                if a <= previous_end:
                    raise ValueError("lessons must follow the book's reading order")
                previous_end = lesson.last_seq
                if any(p not in seen for p in lesson.prior_lessons):
                    raise ValueError(f"prior_lessons of {lesson.lesson_id} must refer to earlier lessons")
                seen.add(lesson.lesson_id)
                spans.extend((*s.seqs, lesson.lesson_id) for s in lesson.spans)
            first, last = m.lessons[0].spine_span.seqs[0], m.lessons[-1].spine_span.seqs[1]
            if not first <= v2_block_seq(m.anchor_block) <= last:
                raise ValueError(f"module {m.module_id} anchor lies outside its lessons")
        spans.extend((*r.seqs, f"excluded:{r.reason}") for r in self.excluded_ranges)
        for item in [*(s for l in self.lessons for s in l.spans), *self.excluded_ranges]:
            for block_id in (item.start_block, item.end_block):
                if _v2_block_book(block_id) != self.spine_book_id:
                    raise ValueError(f"span block {block_id} is not in the spine book")
        ordered = sorted(spans)
        first = expected = ordered[0][0]
        for a, b, name in ordered:
            if a < expected:
                raise ValueError(f"{name} overlaps the previous range at block {a}")
            if a > expected:
                raise ValueError(f"gap: blocks {expected}..{a - 1} are neither included nor excluded")
            expected = b + 1
        if expected - first != self.block_count:
            raise ValueError(f"lessons + excluded ranges cover {expected - first} blocks, block_count is "
                             f"{self.block_count}")
        return self


class OutlineQualityReport(Contract):
    """The deterministic outline gate (stored next to every CourseOutlineV2)."""
    book_id: str
    report_version: str
    outline_sha256: str | None = None
    reference: str | None = None  # reference the tree was compared with (e.g. "pdf_outline"), None = none
    metrics: dict[str, Any]
    checks: list[QualityCheck]
    accepted: bool  # this gate's verdict; acceptance also needs the cross-check gate (registry)

    @property
    def failed(self) -> list[str]:
        return [ch.name for ch in self.checks if ch.passed is False]

    @model_validator(mode="after")
    def check_acceptance(self) -> "OutlineQualityReport":
        if self.accepted and (self.failed or not any(ch.passed for ch in self.checks)):
            raise ValueError("a report with failed (or no passing) checks cannot be accepted")
        return self


_V1_PROVENANCE = {"pdf_outline": "pdf_outline", "printed_toc": "headings", "heading": "headings",
                  "llm_inferred": "llm_inferred"}


def course_outline_v1_from_v2(outline: CourseOutlineV2, book: ParsedBookV2) -> CourseOutline:
    """Project a v2 outline onto the v1 contract for consumers that have not migrated.

    Block ids become those of ``parsed_book_v1_from_v2(book)`` (same blocks, same order), titles
    are the display titles, ``prior_lessons`` stay empty. Excluded ranges before the first lesson
    become ``excluded_prefix_block_ids``; later excluded ranges are gaps between v1 lessons (the
    v1 contract has no field for them; the v2 outline remains the source of truth).
    """
    if book.book_id != outline.spine_book_id or len(book.blocks) != outline.block_count:
        raise ValueError("outline block_count / spine book does not match the ParsedBook v2")
    v1_ids = [b.block_id for b in parsed_book_v1_from_v2(book).blocks]
    position = {b.block_id: i for i, b in enumerate(book.blocks)}
    modules = []
    for m in outline.modules:
        lessons = []
        for lesson in m.lessons:
            if lesson.spine_span.start_block not in position or lesson.spine_span.end_block not in position:
                raise ValueError(f"lesson {lesson.lesson_id} refers to blocks outside the ParsedBook v2")
            a, b = position[lesson.spine_span.start_block], position[lesson.spine_span.end_block]
            lessons.append(Lesson(
                lesson_id=lesson.lesson_id, title=lesson.display_title, order=lesson.order,
                spine_span=LessonSpan(start_block=v1_ids[a], end_block=v1_ids[b], pages=lesson.spine_span.pages),
                provenance=_V1_PROVENANCE[lesson.source], prior_lessons=[]))  # type: ignore[arg-type]
        modules.append(Module(module_id=m.module_id, title=m.display_title, order=m.order, lessons=lessons))
    first = min(position[lesson.spine_span.start_block] for lesson in outline.lessons)
    prefix = v1_ids[:first]
    return CourseOutline(course_id=outline.course_id, spine_book_id=outline.spine_book_id,
                         supplementary_book_ids=list(outline.supplementary_book_ids), modules=modules,
                         requires_review=True, excluded_prefix_block_ids=prefix,
                         excluded_prefix_policy="publisher-front-matter-v1" if prefix else None)


# ---------------------------------------------------------------- L3 chunks
# 1.5.0 (R5, docs/audits/2026-10-24/03-indexing-retrieval.md §5): a chunk records exactly which
# character ranges of which blocks it holds (`parts`), so lesson coverage is checkable by
# construction (every body character of a lesson span in exactly one chunk), plus its evidence
# class, its lesson, a text fingerprint and the chunker version. All new fields have defaults:
# chunks written by 1.4.0 and earlier still validate (they simply carry no parts).
EvidenceClass = Literal["body", "note"]


class ChunkPart(Contract):
    """Characters [start, end) of one block's indexed text."""

    block_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def check_range(self) -> "ChunkPart":
        if self.end <= self.start:
            raise ValueError("a chunk part must be a non-empty range")
        return self


class StructuredChunk(Contract):
    chunk_id: str
    book_id: str
    role: Literal["spine", "supplementary"]
    section_path: list[str]
    block_ids: list[str] = Field(min_length=1)
    pages: list[int] = Field(min_length=1)
    text: str
    token_count: int = Field(ge=1)
    embedding_model: str  # "BAAI/bge-m3@<revision>"
    evidence_class: EvidenceClass = "body"  # notes are evidence, never lesson teaching content
    lesson_id: str | None = None  # the accepted-outline lesson whose span holds it (spine only)
    parts: list[ChunkPart] = []
    text_sha256: str | None = None
    chunker_version: str | None = None
    context_header: str | None = None  # "Book > Module > Lesson"; for retrieval text, not display
    quality_flags: list[str] = []

    @model_validator(mode="after")
    def check_parts(self) -> "StructuredChunk":
        if self.parts:
            order = list(dict.fromkeys(p.block_id for p in self.parts))
            if order != self.block_ids:
                raise ValueError("chunk parts must cover exactly block_ids, in order")
        if self.text_sha256 is not None and \
                self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("text_sha256 does not match the chunk text")
        return self


class IndexQualityReport(Contract):
    """Deterministic content gate of one book's index build; `accepted` only if every check passed."""

    book_id: str
    role: Literal["spine", "supplementary"]
    report_version: str
    chunker_version: str
    source_format: Literal["parsed-book/1", "parsed-book/2"]
    outline_sha256: str | None = None
    metrics: dict[str, float | int | None]
    checks: list[QualityCheck]
    accepted: bool

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if c.passed is False]

    @model_validator(mode="after")
    def check_acceptance(self) -> "IndexQualityReport":
        if self.accepted and (self.failed or not any(c.passed for c in self.checks)):
            raise ValueError("a report with failed (or no passing) checks cannot be accepted")
        return self


class SupplementLinkJudgement(Contract):
    """One recorded relevance decision lesson -> supplementary chunk (audit 03 H1).

    Below the threshold the chunk is not linked; "no supplementary evidence" is a valid outcome.
    `calibrated=False` says the threshold has not been fitted against human relevance labels yet.
    """

    lesson_id: str
    chunk_id: str
    book_id: str
    rank: int = Field(ge=1)
    method: str = Field(min_length=1)
    score: float
    threshold: float
    verdict: Literal["linked", "below_threshold"]
    calibrated: bool
    policy_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_verdict(self) -> "SupplementLinkJudgement":
        if (self.verdict == "linked") != (self.score >= self.threshold):
            raise ValueError("verdict must be 'linked' exactly when score >= threshold")
        return self


# ---------------------------------------------------------------- L4 KG
EntityType = Literal[
    "concept", "definition", "theorem_or_law", "formula", "method_or_algorithm",
    "example", "person", "tool_or_technology", "misconception",
]
ConceptEdgeType = Literal[
    "prerequisite_of", "example_of", "defines", "part_of", "contrasts_with", "applies_to",
]


class ConceptEdge(Contract):
    src_entity: str
    dst_entity: str
    type: ConceptEdgeType
    evidence_chunk_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    classifier: dict[str, str]  # {"model": ..., "prompt_version": ...}


class EntityMergeProposal(Contract):
    canonical: str
    aliases: list[str] = Field(min_length=1)
    cosine: float = Field(ge=-1, le=1)
    judge_verdict: Literal["same", "different", "unsure"]
    decision: Literal["auto_merge", "review", "reject"]


# ---------------------------------------------------------------- L5 enrichment
SupplementKind = Literal["new_info", "elaboration", "example", "redundant", "contradiction"]


class SupplementCandidate(Contract):
    lesson_id: str
    chunk_id: str
    book_id: str
    pages: list[int]
    kind: SupplementKind
    novelty: float = Field(ge=0, le=1)
    rerank_score: float
    anchor_block_id: str | None = None


class LessonSentence(Contract):
    sid: str  # "m03.l02:s017"
    text_en: str
    citations: list[str] = Field(min_length=1)  # chunk_ids — every sentence must be grounded
    kind: Literal["primary", "supplementary", "connective"]


class LessonDraft(Contract):
    lesson_id: str
    version: int = Field(ge=1)
    sentences: list[LessonSentence] = Field(min_length=1)
    contradictions: list[SupplementCandidate] = []
    faithfulness: dict[str, float]  # {"ragas_faithfulness": ..., "citation_support_rate": ...}


# ---------------------------------------------------------------- L6 pedagogy
Bloom = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]


class GlossaryTerm(Contract):
    term_en: str
    definition_en: str
    first_sid: str
    lesson_id: str


class LessonSummary(Contract):
    lesson_id: str
    points: list[str] = Field(min_length=3, max_length=10)
    point_sids: list[list[str]]  # evidence per point


class Question(Contract):
    qid: str
    lesson_id: str
    purpose: Literal["end_of_lesson", "review_quiz"]
    type: Literal["mcq", "short_answer", "cloze"]
    bloom: Bloom
    stem: str
    options: list[str] | None = None
    answer_index: int | None = None
    answer_text: str | None = None
    rationale: str
    evidence_sids: list[str] = Field(min_length=1)
    option_evidence_sids: list[list[str]] | None = None  # source anchors aligned to options
    validation: dict[str, Any]  # solver results + linter findings

    @model_validator(mode="after")
    def _shape(self) -> "Question":
        if self.type == "mcq":
            if not self.options or not (3 <= len(self.options) <= 5):
                raise ValueError("mcq needs 3-5 options")
            if self.answer_index is None or not (0 <= self.answer_index < len(self.options)):
                raise ValueError("mcq needs valid answer_index")
            if self.option_evidence_sids is not None:
                if len(self.option_evidence_sids) != len(self.options) or any(not refs for refs in self.option_evidence_sids):
                    raise ValueError("mcq option evidence must provide nonempty citations for each option")
        elif self.answer_text is None:
            raise ValueError("non-mcq needs answer_text")
        return self


class ReviewLogEntry(Contract):
    card_id: str
    user_id: str
    rating: Literal[1, 2, 3, 4]  # FSRS: Again, Hard, Good, Easy
    reviewed_at: datetime

    @field_validator("reviewed_at")
    @classmethod
    def check_reviewed_utc(cls, v: datetime) -> datetime:
        return _utc(v)


# ---------------------------------------------------------------- L7 translation
TermTier = Literal["locked", "hint"]


class TermbaseEntry(Contract):
    term_en: str
    term_ar: str
    term_ar_diacritized: str | None = None
    definition_en: str
    forbidden_variants_ar: list[str] = []
    do_not_translate: bool = False
    approved_by: str | None = None  # None => not approved yet (Gate G3)
    # 1.15.0 (D-039): "locked" = checked strictly (the only tier before 1.15); "hint" = a
    # preferred rendering given to the engine and checked only as a minor, inflection-tolerant
    # signal. Noise ("dropped") never becomes an entry.
    tier: TermTier = "locked"

    @model_validator(mode="after")
    def check_hint_has_no_forbidden_variants(self) -> "TermbaseEntry":
        if self.tier == "hint" and self.forbidden_variants_ar:
            raise ValueError("a hint entry is not checked strictly, so it takes no forbidden variants")
        return self


class QEResult(Contract):
    deterministic_pass: bool
    term_violations: list[str] = []
    cometkiwi: float | None = None
    xcomet: float | None = None
    xcomet_error_spans: list[dict[str, Any]] = []
    llm_mqm: dict[str, Any] | None = None  # {"major": n, "minor": n, "categories": [...]}
    route: Literal["auto_accept", "retranslate", "human_review"]


# Translation rebuild (D-028, docs/proposals/2026-10-24-translation-rebuild.md).
TranslationUnitKind = Literal[
    "prose", "heading", "list_item", "caption", "table_cell",
    "footnote", "reference", "toc", "index", "noise",
]
# MQM-aligned categories, as used by the 2026-10-24 segment audit.
QACategory = Literal[
    "omission", "addition", "prompt_leakage", "meaning_error", "polarity_error", "number_error",
    "date_error", "footnote_marker", "named_entity", "terminology", "untranslated_english",
    "foreign_script", "quotation", "bibliographic", "structure_formatting", "grammar",
    "fluency_style", "repetition", "segmentation", "other",
]
Severity = Literal["critical", "major", "minor"]


class TranslationUnit(Contract):
    """One unit sent to (or handled instead of) a translation engine.

    Units never cross a paragraph boundary and always end at a footnote marker, so markers
    are restored by appending them in order after the translated text.
    """
    unit_id: str  # f"{book_id}:u{seq:05d}"
    book_id: str
    block_ids: list[str] = Field(min_length=1)
    kind: TranslationUnitKind
    source_text: str = Field(min_length=1)
    footnote_markers: list[str] = []
    pages: list[int] = Field(min_length=1)
    section_path: list[str]
    prev_unit_id: str | None = None
    next_unit_id: str | None = None
    normalization: list[str] = []  # applied source repairs, e.g. "joined_page_break"

    @field_validator("source_text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_text must not be blank")
        return v


class QAIssue(Contract):
    check_id: str
    category: QACategory
    severity: Severity
    origin: Literal["deterministic", "qe", "human"]
    evidence: dict[str, Any] = {}


class EngineIdentity(Contract):
    provider: str
    model_id: str
    revision: str | None = None
    prompt_version: str


class UnitTranslation(Contract):
    unit_id: str
    target_text: str
    origin: Literal["engine", "rule", "memory", "human"]
    engine: EngineIdentity | None = None
    attempts: int = Field(default=0, ge=0)
    issues: list[QAIssue] = []
    qe: QEResult | None = None
    decision: Literal["accepted", "needs_review", "rejected", "dropped"]
    decided_by: Literal["policy", "human"]
    decided_at: datetime

    @field_validator("decided_at")
    @classmethod
    def check_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def decision_invariants(self) -> "UnitTranslation":
        if self.origin == "engine" and self.engine is None:
            raise ValueError("engine output must record the engine identity")
        if self.decision == "dropped":
            if self.target_text:
                raise ValueError("dropped units carry no target text")
            return self
        if not self.target_text.strip() and self.decision != "rejected":
            raise ValueError("target_text must not be blank unless the unit is dropped or rejected")
        if self.decision == "accepted":
            if any(issue.severity in ("critical", "major") for issue in self.issues):
                raise ValueError("accepted translations cannot carry major or critical issues")
            if self.origin == "human" and self.decided_by != "human":
                raise ValueError("a human translation is accepted by a human decision")
            if self.origin == "engine" and self.decided_by == "policy" and self.qe is None:
                raise ValueError("policy acceptance of engine output requires semantic QE")
        return self


# 1.16.0 (D-040, docs/proposals/2026-10-25-learning-companion.md §4): transparent failure. There is
# no human reviewer in the product: a unit that was not accepted (needs_review / rejected) is
# neither hidden nor blocking. It becomes a translator note in place, spoken in the COURSE voice
# (not the author's), carrying the best available translation labelled approximate and the original.
TRANSLATOR_NOTE_LEAD_AR = "ملاحظة من الكورس:"
BEST_TRANSLATION_LABEL_AR = "ترجمة تقريبية"
# Learner-facing reason, primary first. no_translation: no candidate is fit to offer;
# unverified: no specific problem found but the translation could not be confirmed.
TranslatorNoteReason = Literal[
    "no_translation", "meaning", "completeness", "untranslated", "numbers", "names", "terminology", "wording",
    "unverified",
]
_LATIN_LETTER = re.compile(r"[A-Za-z]")
# critical_meaning: a critical meaning/polarity problem; fabrication_risk: a short source came back
# as a much longer text (book-main:u00628 "NOT ES"); noise_source: the source has no words.
TranslationWithheld = Literal["critical_meaning", "fabrication_risk", "noise_source"]


class TranslatorNote(Contract):
    """One annotated unit, anchored where the unit is (block, pages, section).

    Speech reads ``announcement_ar`` then ``best_translation`` (if any) as one course-voice span;
    the English ``source_text`` is kept for the written episode and the glossary, not voiced.
    """
    note_id: str = Field(min_length=1)  # f"{unit_id}:note"
    unit_id: str = Field(min_length=1)
    book_id: str = Field(min_length=1)
    block_ids: list[str] = Field(min_length=1)
    pages: list[int] = Field(min_length=1)
    section_path: list[str] = []
    unit_kind: TranslationUnitKind
    voice: Literal["course"] = "course"
    unit_decision: Literal["needs_review", "rejected"]
    reasons: list[TranslatorNoteReason] = Field(min_length=1)
    reason_ar: str = Field(min_length=1, max_length=300)
    source_language: str = "en"
    source_text: str = Field(min_length=1)
    best_translation: str | None = None
    best_translation_label_ar: str | None = None
    best_translation_from: Literal["final", "earlier_draft"] | None = None
    announcement_ar: str = Field(min_length=1, max_length=600)
    # 1.18.0 (D-041): why no translation is offered although a candidate existed (it may be invented).
    withheld: TranslationWithheld | None = None

    @model_validator(mode="after")
    def note_invariants(self) -> "TranslatorNote":
        if not self.announcement_ar.startswith(TRANSLATOR_NOTE_LEAD_AR):
            raise ValueError("a translator note is announced in the course voice")
        for name in ("announcement_ar", "reason_ar"):
            if _LATIN_LETTER.search(getattr(self, name)):
                raise ValueError(f"{name} is for the learner: no Latin text or technical codes")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must be unique")
        offered = (self.best_translation, self.best_translation_label_ar, self.best_translation_from)
        if any(v is None for v in offered) and any(v is not None for v in offered):
            raise ValueError("a best translation comes with its label and origin, or not at all")
        if self.best_translation is not None and not self.best_translation.strip():
            raise ValueError("best_translation must not be blank")
        if (self.best_translation is None) != ("no_translation" in self.reasons):
            raise ValueError("reason no_translation is used exactly when no translation is offered")
        if self.withheld is not None and self.best_translation is not None:
            raise ValueError("a withheld translation is not offered")
        return self


class TranslatorNotesDocument(Contract):
    """Artifact of stage ``translator-notes``: accounts for every unit of the book.

    accepted + dropped + annotated + pending == total_units; ``pending`` units have no final result
    yet and are listed by id, so nothing is lost silently.
    """
    format: Literal["b2c.translator-notes/1"] = "b2c.translator-notes/1"
    book_id: str = Field(min_length=1)
    run_id: str | None = None
    builder_version: str = Field(min_length=1)
    units_ref: ArtifactRef | None = None
    results_digest: str | None = None
    total_units: int = Field(ge=0)
    accepted: int = Field(ge=0)
    dropped: int = Field(ge=0)
    annotated: int = Field(ge=0)
    pending: int = Field(ge=0)
    notes: list[TranslatorNote] = []
    unannotated_unit_ids: list[str] = []

    @model_validator(mode="after")
    def accounts_for_every_unit(self) -> "TranslatorNotesDocument":
        ids = [n.unit_id for n in self.notes]
        if len(set(ids)) != len(ids):
            raise ValueError("one note per unit")
        if any(n.book_id != self.book_id for n in self.notes):
            raise ValueError("every note belongs to the document's book")
        if len(set(self.unannotated_unit_ids)) != len(self.unannotated_unit_ids):
            raise ValueError("unannotated unit ids must be unique")
        if set(ids) & set(self.unannotated_unit_ids):
            raise ValueError("a unit is either annotated or pending, not both")
        if self.annotated != len(self.notes) or self.pending != len(self.unannotated_unit_ids):
            raise ValueError("annotated/pending counts must match the listed notes and ids")
        if self.accepted + self.dropped + self.annotated + self.pending != self.total_units:
            raise ValueError("every unit must be accepted, dropped, annotated or pending")
        return self


# ---------------------------------------------------------------- L8 speech
class VoiceConsent(Contract):  # D-003
    consent_id: str
    artist_name: str
    scope: str  # e.g. "research platform narration"
    signed_at: datetime
    expires_at: datetime | None = None
    document_ref: str  # legal office reference
    sample_uris: list[str] = Field(min_length=1)

    @field_validator("signed_at", "expires_at")
    @classmethod
    def check_consent_utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else _utc(v)


class VoiceSpec(Contract):
    provider: Literal[
        "supertonic3", "omnivoice", "chatterbox_ml_v3", "silma_v1", "higgs_tts3", "fasih_v1",
    ]
    voice_id: str
    cloned: bool
    consent_id: str | None = None
    speed: float = Field(default=1.0, gt=0.5, lt=2.0)
    seed: int = 1234

    @model_validator(mode="after")
    def _consent(self) -> "VoiceSpec":
        if self.cloned and not self.consent_id:
            raise ValueError("cloned voice requires consent_id (D-003)")
        return self


class TTSRequest(Contract):
    segment_id: str
    text_ar_diacritized: str
    voice: VoiceSpec
    sample_rate: int = 44100


class TTSResult(Contract):
    segment_id: str
    wav_uri: str
    duration_s: float = Field(gt=0)
    provider_version: str
    rtf: float = Field(gt=0)


class AudioAsset(Contract):
    lesson_id: str
    mp3_uri: str
    segments: list[dict[str, Any]]  # [{"sid": ..., "start_s": ..., "end_s": ...}]
    rms_dbfs: float
    peak_dbfs: float
    noise_floor_dbfs: float
    acx_pass: bool
    asr_cer: float
    voice: VoiceSpec


# Speech-ready text (SCHEMA 1.5.0, R7 / audit 07 §5.2 S1). The TTS stage reads only this: spoken
# text made deterministically from ACCEPTED translation units, split into TTS segments, each one
# traced to the units it was made from (audit 07 C3: per-segment status and provenance).
SpeechStructure = Literal["heading", "paragraph", "list_item", "caption", "question", "answer"]
# SCHEMA 1.14.0: the prosodic boundary after a segment (speech readability proposal 2026-10-25). The
# break strength is SSML 1.1's; ``pause_after_ms`` is its realized silence for engines without SSML.
SpeechBoundary = Literal["phrase", "sentence", "list_item", "paragraph", "heading", "section"]
SpeechBreakStrength = Literal["none", "x-weak", "weak", "medium", "strong", "x-strong"]


class SpeechSegment(Contract):
    segment_id: str = Field(min_length=1, max_length=160)
    seq: int = Field(ge=0)
    unit_ids: list[str] = Field(min_length=1)  # accepted translation units (TranslationUnit.unit_id)
    block_ids: list[str] = []
    structure: SpeechStructure
    source_text: str = Field(min_length=1)  # accepted target text of this span, before normalization
    spoken_text: str = Field(min_length=1)  # what the TTS provider receives
    rules_applied: list[str] = []  # normalization rule ids, e.g. "year", "footnote_marker_removed"
    lexicon_hits: list[str] = []  # Latin source forms replaced from the pronunciation lexicon
    lexicon_misses: list[str] = []  # Latin words spoken through the documented fallback (not in lexicon)
    pause_after_ms: int = Field(ge=0, le=5000)
    boundary_after: SpeechBoundary | None = None  # None: made before 1.14.0 (no boundary plan)
    break_after: SpeechBreakStrength | None = None
    unvoiced_glosses: list[str] = []  # parenthesised original (Latin) terms not voiced: no reviewed pronunciation
    # 1.18.0 (D-041): a translator note is spoken in the course voice: its announcement, then the
    # approximate translation (if one is offered). Every other segment is the author's text.
    voice: Literal["author", "course"] = "author"
    note_id: str | None = None
    note_part: Literal["announcement", "approximate_translation"] | None = None

    @field_validator("spoken_text", "source_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("speech text must not be blank")
        return v

    @model_validator(mode="after")
    def _note(self) -> "SpeechSegment":
        flags = (self.voice == "course", self.note_id is not None, self.note_part is not None)
        if any(flags) and not all(flags):
            raise ValueError("a translator-note segment is the course voice and names its note and part")
        return self


class SpeechExclusion(Contract):
    unit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=120)  # dropped | not_spoken_kind:<kind> | wrong_language ...


class SpeechTextDocument(Contract):
    book_id: str = Field(min_length=1)
    language: Literal["ar", "en"]
    frontend_version: str = Field(min_length=1)
    lexicon_digest: str | None = None
    source: ArtifactRef  # the accepted translate-book assembly the units belong to
    segments: list[SpeechSegment] = Field(min_length=1)
    excluded: list[SpeechExclusion] = []
    notes_ref: ArtifactRef | None = None  # 1.18.0: accepted translator-notes artifact of the note segments

    @model_validator(mode="after")
    def _segments_invariants(self) -> "SpeechTextDocument":
        if any(s.note_id for s in self.segments) and self.notes_ref is None:
            raise ValueError("note segments need the translator-notes artifact (notes_ref)")
        parts: dict[str, list[str]] = {}
        for s in self.segments:
            if s.note_id:
                parts.setdefault(s.note_id, []).append(s.note_part or "")
        for seq in parts.values():
            if seq[0] != "announcement" or seq != sorted(seq, key=lambda p: p == "approximate_translation"):
                raise ValueError("a note is spoken announcement first, then its approximate translation")
        ids = [s.segment_id for s in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("segment_id must be unique")
        if [s.seq for s in self.segments] != list(range(len(self.segments))):
            raise ValueError("segments must be ordered with seq 0..n-1")
        spoken = {u for s in self.segments for u in s.unit_ids}
        both = sorted(spoken & {e.unit_id for e in self.excluded})
        if both:
            raise ValueError(f"a unit cannot be both spoken and excluded: {both[:3]}")
        return self


# ---------------------------------------------------------------- release
class CourseRelease(Contract):
    release_id: str
    course_id: str
    lesson_versions: dict[str, int]
    model_versions: dict[str, str]
    voice: VoiceSpec
    human_reviewed_ratio: float = Field(ge=0, le=1)  # D-016 research documentation
    book_rights: dict[str, RightsStatus]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def check_created_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    @model_validator(mode="after")
    def _rights(self) -> "CourseRelease":
        bad = [b for b, s in self.book_rights.items() if s != "cleared"]
        if bad:
            raise ValueError(f"books not cleared (D-002): {bad}")
        return self


# ---------------------------------------------------------------- knowledge research + concept layer (1.7.0)
# D-032..D-035, docs/proposals/2026-10-25-learning-companion.md §6, §10–§12. Both are produced in
# ENGLISH from the English source book and translated later through the translation pipeline.
# Three voices stay apart: the author's (book spans), the course's (glosses) and external sources
# (dossier claims with URL + quote + tier + fetch date).
SourceTier = Literal[1, 2, 3]  # 1 academic/encyclopedic/publisher, 2 published reviews/interviews, 3 blogs
DossierSection = Literal["book_card", "author", "context", "reception", "concept_in_field", "further_reading"]
_SOURCE_ID = r"^src_[0-9a-f]{16}$"
_CLAIM_ID = r"^clm_[0-9a-f]{12}$"
_CONCEPT_ID = r"^cpt_[0-9a-f]{12}$"
_PROVIDER = r"^[a-z0-9][a-z0-9._:-]{0,59}$"
_FAMILY = r"^[a-z0-9][a-z0-9._-]{0,59}$"


class ResearchSource(Contract):
    """One fetched external document. Its text is data, never instructions (stored beside the dossier)."""
    source_id: str = Field(pattern=_SOURCE_ID)
    url: str = Field(min_length=9, max_length=2000)
    provider: str = Field(pattern=_PROVIDER)  # openalex | wikidata | wikipedia | openlibrary | web:<engine> ...
    title: str | None = None
    tier: SourceTier
    fetched_at: datetime
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # sha256 of the sanitized plain text
    text_chars: int = Field(ge=0)
    flags: list[str] = []  # e.g. "injection_removed:2", "truncated"

    @field_validator("url")
    @classmethod
    def check_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("external source URLs must be https")
        return v

    @field_validator("fetched_at")
    @classmethod
    def check_fetched_utc(cls, v: datetime) -> datetime:
        return _utc(v)


class Evidence(Contract):
    """A verbatim quote from a fetched source, with the citation validator's verdicts."""
    source_id: str = Field(pattern=_SOURCE_ID)
    url: str
    quote: str = Field(min_length=1, max_length=800)
    tier: SourceTier
    fetched_at: datetime
    quote_found: bool  # the quote occurs (whitespace/quote-normalized) in the sanitized source text
    supports: bool | None = None  # independent judge: the quote supports the claim; None = not judged
    judged_by: str | None = None

    @field_validator("fetched_at")
    @classmethod
    def check_fetched_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    @property
    def valid(self) -> bool:
        return self.quote_found and self.supports is True


class DossierClaim(Contract):
    """One external claim. ``verified`` = a fact backed by one tier-1 source or two distinct sources;
    ``attributed`` = said in the name of its source (viewpoints, single lower-tier facts, conflicts);
    ``rejected`` = kept for audit only, never shown."""
    claim_id: str = Field(pattern=_CLAIM_ID)
    section: DossierSection
    statement: str = Field(min_length=1, max_length=1000)
    kind: Literal["fact", "viewpoint"]
    attributed_to: str | None = None
    concept: str | None = None  # concept_in_field: the book's term this note is about
    evidence: list[Evidence] = Field(min_length=1)
    status: Literal["verified", "attributed", "rejected"]
    status_reason: str | None = None
    conflict_group: str | None = None  # claims that contradict each other share a group

    @model_validator(mode="after")
    def _claim_invariants(self) -> "DossierClaim":
        if not self.statement.strip():
            raise ValueError("a claim needs a statement")
        if self.section == "concept_in_field" and not (self.concept or "").strip():
            raise ValueError("a concept_in_field claim names its concept")
        if self.status == "rejected":
            if not (self.status_reason or "").strip():
                raise ValueError("a rejected claim needs a status_reason")
            return self
        if not all(e.valid for e in self.evidence):
            raise ValueError("published evidence must be found in its source and supported by the judge")
        if self.status == "verified":
            if self.kind != "fact":
                raise ValueError("a viewpoint is never a verified fact; attribute it")
            if self.conflict_group is not None:
                raise ValueError("conflicting claims stay attributed viewpoints, never a fact")
            if not (any(e.tier == 1 for e in self.evidence) or len({e.source_id for e in self.evidence}) >= 2):
                raise ValueError("a verified fact needs one tier-1 source or two distinct sources")
        elif not (self.attributed_to or "").strip():
            raise ValueError("an attributed claim names who says it")
        return self


class FurtherReading(Contract):
    title: str = Field(min_length=1)
    authors: list[str] = []
    year: int | None = None
    url: str
    source_id: str = Field(pattern=_SOURCE_ID)
    tier: SourceTier
    note: str | None = None


class RejectedItem(Contract):
    """Something research refused: a URL (policy/fetch), removed injected text, or a failed claim."""
    url: str
    stage: Literal["policy", "fetch", "extract", "injection", "validation"]
    reason: str = Field(min_length=1)


class KnowledgeDossier(Contract):
    format: Literal["b2c.knowledge-dossier/1"] = "b2c.knowledge-dossier/1"
    book_id: str = Field(min_length=1)
    language: Literal["en"] = "en"
    title: str = Field(min_length=1)
    authors: list[str] = []
    identifiers: dict[str, str] = {}  # isbn / doi / openalex / wikidata / openlibrary
    created_at: datetime
    queries: list[str] = []
    sources: list[ResearchSource] = []
    claims: list[DossierClaim] = []
    further_reading: list[FurtherReading] = []
    rejected: list[RejectedItem] = []
    extractor: ProducerIdentity
    judge: ProducerIdentity
    extractor_family: str = Field(pattern=_FAMILY)
    judge_family: str = Field(pattern=_FAMILY)
    metrics: dict[str, Any] = {}

    @field_validator("created_at")
    @classmethod
    def check_created_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    def published(self) -> list[DossierClaim]:
        return [x for x in self.claims if x.status != "rejected"]

    @model_validator(mode="after")
    def _dossier_invariants(self) -> "KnowledgeDossier":
        if self.extractor_family == self.judge_family:
            raise ValueError("the citation judge must come from a different model family than the extractor")
        by_id = {s.source_id: s for s in self.sources}
        if len(by_id) != len(self.sources):
            raise ValueError("duplicate source_id")
        if len({x.claim_id for x in self.claims}) != len(self.claims):
            raise ValueError("duplicate claim_id")
        for x in self.claims:
            for e in x.evidence:
                s = by_id.get(e.source_id)
                if s is None:
                    raise ValueError(f"{x.claim_id}: evidence from an unlisted source {e.source_id}")
                if (s.url, s.tier, s.fetched_at) != (e.url, e.tier, e.fetched_at):
                    raise ValueError(f"{x.claim_id}: evidence provenance differs from its source record")
        for r in self.further_reading:
            if r.source_id not in by_id:
                raise ValueError(f"further reading from an unlisted source {r.source_id}")
        return self


class BookSpan(Contract):
    """A verbatim passage of the source book: ``quote`` is a substring of the block's text."""
    block_id: str = Field(min_length=1)
    pages: list[int] = []
    quote: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def _span(self) -> "BookSpan":
        if not self.quote.strip():
            raise ValueError("a book span quotes text")
        if any(p < 1 for p in self.pages):
            raise ValueError("pages are 1-based")
        return self


class AuthorDefinition(Contract):
    """The author's own definition (author voice = the spans); ``gloss`` is the course's paraphrase."""
    spans: list[BookSpan] = Field(min_length=1)
    gloss: str | None = None


ConceptRelationKind = Literal["prerequisite", "related", "part_of", "contrasts_with"]


class ConceptRelation(Contract):
    """``prerequisite``: the target must be understood first. Grounded in the book like any author statement."""
    target_id: str = Field(pattern=_CONCEPT_ID)
    relation: ConceptRelationKind
    evidence: list[BookSpan] = Field(min_length=1)


class ExternalConceptNote(Contract):
    """External context from a published dossier claim: never presented in the author's voice."""
    voice: Literal["external"] = "external"
    claim_id: str = Field(pattern=_CLAIM_ID)
    statement: str = Field(min_length=1)
    urls: list[str] = Field(min_length=1)


class ConceptEntry(Contract):
    concept_id: str = Field(pattern=_CONCEPT_ID)
    term: str = Field(min_length=1)
    kind: Literal["term", "acronym"]
    termbase_status: Literal["approved", "candidate"]
    introduced_at: BookSpan  # first occurrence in the lesson text of the book
    first_lesson_id: str | None = None
    occurrences: int = Field(ge=1)
    author_definition: AuthorDefinition | None = None
    relations: list[ConceptRelation] = []
    novelty: Literal["author_novel", "established", "unknown"] = "unknown"
    external_notes: list[ExternalConceptNote] = []

    @model_validator(mode="after")
    def _novelty(self) -> "ConceptEntry":
        if self.novelty != "unknown" and not self.external_notes:
            raise ValueError("novelty is only stated when supported by the dossier (external notes)")
        return self


class LessonMention(Contract):
    concept_id: str = Field(pattern=_CONCEPT_ID)
    block_id: str = Field(min_length=1)


class LessonConceptList(Contract):
    """Concepts first mentioned in a lesson (first mention per lesson, in reading order)."""
    lesson_id: str = Field(min_length=1)
    title: str = ""
    first_mentions: list[LessonMention] = []


class RejectedStatement(Contract):
    concept_id: str = Field(pattern=_CONCEPT_ID)
    kind: Literal["definition", "relation", "novelty"]
    reason: str = Field(min_length=1)


class ConceptLayer(Contract):
    format: Literal["b2c.concept-layer/1"] = "b2c.concept-layer/1"
    book_id: str = Field(min_length=1)
    language: Literal["en"] = "en"
    created_at: datetime
    termbase_basis: Literal["approved", "candidates"]
    termbase_sha256: str | None = None
    dossier_sha256: str | None = None
    concepts: list[ConceptEntry] = []
    lessons: list[LessonConceptList] = []
    rejected: list[RejectedStatement] = []
    extractor: ProducerIdentity | None = None
    metrics: dict[str, Any] = {}

    @field_validator("created_at")
    @classmethod
    def check_created_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    def concept(self, concept_id: str) -> ConceptEntry:
        for x in self.concepts:
            if x.concept_id == concept_id:
                return x
        raise KeyError(concept_id)

    @model_validator(mode="after")
    def _layer_invariants(self) -> "ConceptLayer":
        ids = [x.concept_id for x in self.concepts]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate concept_id")
        terms = [x.term.casefold() for x in self.concepts]
        if len(set(terms)) != len(terms):
            raise ValueError("duplicate concept term")
        known = set(ids)
        prereq: dict[str, set[str]] = {i: set() for i in ids}
        for x in self.concepts:
            for r in x.relations:
                if r.target_id not in known or r.target_id == x.concept_id:
                    raise ValueError(f"{x.concept_id}: relation target {r.target_id} unknown or self")
                if r.relation == "prerequisite":
                    prereq[x.concept_id].add(r.target_id)
        if prerequisite_cycle(prereq):
            raise ValueError("the prerequisite graph has a cycle")
        for lesson in self.lessons:
            seen = [m.concept_id for m in lesson.first_mentions]
            if len(set(seen)) != len(seen):
                raise ValueError(f"{lesson.lesson_id}: a concept is first-mentioned once per lesson")
            if not set(seen) <= known:
                raise ValueError(f"{lesson.lesson_id}: mention of an unknown concept")
        return self


def prerequisite_cycle(graph: dict[str, set[str]]) -> bool:
    """True if the directed graph (node -> targets) has a cycle (iterative DFS)."""
    state: dict[str, int] = {}
    for root in graph:
        if root in state:
            continue
        stack = [(root, iter(sorted(graph.get(root, ()))))]
        state[root] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
            elif state.get(nxt) == 1:
                return True
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(sorted(graph.get(nxt, ())))))
    return False


# ---------------------------------------------------------------- derived formats: episode scripts (1.14.0)
# D-032/D-035..D-038, learning-companion §2, §12–§15. Every episode is written in ENGLISH from the
# English book (grounded, validated), then translated through the book's translation pipeline. The
# English and the Arabic script share this shape. The faithful core (the lesson's book text) is never
# generated: a ``book_text`` section names the lesson's blocks and their translation units; the Arabic
# script carries their ACCEPTED text (with the original term at its first mention, D-032 principle 3).
EPISODE_SCRIPT_FORMAT = "b2c.episode-script/1"
EpisodeVoice = Literal["author", "course", "external"]
EpisodeSectionKind = Literal[
    # book episodes (D-036 (3), D-038 (2))
    "episode_intro", "book_text", "figure_description", "lesson_summary", "key_terms", "questions",
    "others_say", "debate",
    # episode 00, the course introduction (D-037)
    "about_book", "about_author", "book_method", "course_guide", "acknowledgments",
]
SECTION_VOICES: dict[str, frozenset[str]] = {
    "episode_intro": frozenset({"course"}), "book_text": frozenset({"author"}),
    "figure_description": frozenset({"author"}), "lesson_summary": frozenset({"course"}),
    "key_terms": frozenset({"course"}), "questions": frozenset({"course"}),
    "others_say": frozenset({"external"}), "debate": frozenset({"external"}),
    "about_book": frozenset({"course", "external"}), "about_author": frozenset({"course", "external"}),
    "book_method": frozenset({"course"}), "course_guide": frozenset({"course"}),
    "acknowledgments": frozenset({"course"}),
}
BOOK_TEXT_KINDS = frozenset({"book_text", "figure_description"})
ScriptLineRole = Literal["text", "question", "answer", "term"]
QUESTIONS_PER_EPISODE = 5
_LINE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$"
# lesson episode: intro, book text (with figure descriptions), summary, key terms, questions, then
# optional "others say"/debate; course introduction: book, author, method, guide, optional external
# sections, brief acknowledgments last.
_LESSON_ORDER = re.compile(r"^I(?:B|F)*B(?:B|F)*SKQ(?:O|D)*$")
_INTRO_ORDER = re.compile(r"^AUMG(?:O|D)*C?$")
_ORDER_CODE = {"episode_intro": "I", "book_text": "B", "figure_description": "F", "lesson_summary": "S",
               "key_terms": "K", "questions": "Q", "others_say": "O", "debate": "D", "about_book": "A",
               "about_author": "U", "book_method": "M", "course_guide": "G", "acknowledgments": "C"}


class ScriptSource(Contract):
    """What a script line rests on (learning-companion §2.6: every claim has a source).

    ``book``: a verbatim ``quote`` of block ``block_id`` (of lesson ``lesson_id``); ``dossier``: a
    published KnowledgeDossier claim and its URL; ``concept``: a ConceptLayer entry; ``outline``: the
    episode list (titles, numbers); ``course``: a fixed course-voice line and the decision it states."""
    kind: Literal["book", "dossier", "concept", "outline", "course"]
    lesson_id: str | None = None
    block_id: str | None = None
    quote: str | None = Field(default=None, max_length=1200)
    claim_id: str | None = Field(default=None, pattern=_CLAIM_ID)
    url: str | None = None
    concept_id: str | None = Field(default=None, pattern=_CONCEPT_ID)
    episode: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _source(self) -> "ScriptSource":
        ok = {"book": bool(self.block_id and (self.quote or "").strip()),
              "dossier": bool(self.claim_id and (self.url or "").startswith("https://")),
              "concept": bool(self.concept_id), "outline": self.episode is not None,
              "course": bool((self.note or "").strip())}[self.kind]
        if not ok:
            raise ValueError(f"a {self.kind} source is missing its locator")
        return self


class ScriptLine(Contract):
    """One spoken unit of a section. English lines are generated (grounded); Arabic lines are their
    translation (``unit_ids`` = the translation units, ``status`` = the translation decision)."""
    line_id: str = Field(pattern=_LINE_ID)
    role: ScriptLineRole = "text"
    text: str = Field(min_length=1)
    sources: list[ScriptSource] = Field(min_length=1)
    original: str | None = None          # key term: the source-language term
    attributed_to: str | None = None     # external voice: who says it
    pause_after_ms: int = Field(default=0, ge=0, le=10000)  # questions: the thinking pause
    unit_ids: list[str] = []             # translation units this text came from (Arabic; book text)
    status: Literal["accepted", "needs_review", "rejected"] | None = None  # Arabic: translation decision
    inserted_originals: list[str] = []   # Arabic: originals inserted at a first mention (D-032 p.3)
    # 1.18.0 (D-041): Arabic book text only — this line is the translator note of a unit that was not
    # accepted (course voice: announcement, then the approximate translation if one is offered).
    note_id: str | None = None

    @field_validator("text")
    @classmethod
    def _text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a script line has text")
        return v


class EpisodeSection(Contract):
    section_id: str = Field(pattern=_LINE_ID)
    kind: EpisodeSectionKind
    voice: EpisodeVoice
    lines: list[ScriptLine] = []
    block_ids: list[str] = []  # book text: the lesson's blocks in reading order
    unit_ids: list[str] = []   # book text: their translation units (the book's translation run)

    @model_validator(mode="after")
    def _section(self) -> "EpisodeSection":
        if self.voice not in SECTION_VOICES[self.kind]:
            raise ValueError(f"{self.kind} is spoken in the {sorted(SECTION_VOICES[self.kind])} voice, "
                             f"not {self.voice}")
        if self.kind in BOOK_TEXT_KINDS:
            if not self.block_ids or not self.unit_ids:
                raise ValueError("book text names its blocks and translation units (the faithful core)")
            known = set(self.unit_ids)
            if any(not line.unit_ids or not set(line.unit_ids) <= known for line in self.lines):
                raise ValueError("a book-text line must come from the section's translation units")
            return self
        if any(line.note_id for line in self.lines):
            raise ValueError("only book-text lines carry a translator note")
        if self.block_ids or self.unit_ids:
            raise ValueError("only book text refers to book translation units")
        if not self.lines:
            raise ValueError(f"a {self.kind} section has at least one line")
        if self.voice == "external":
            for line in self.lines:
                if not (line.attributed_to or "").strip():
                    raise ValueError("an external-voice line is attributed to its source")
                if not any(s.kind == "dossier" for s in line.sources):
                    raise ValueError("an external-voice line rests on a published dossier claim")
        if self.kind == "key_terms":
            for line in self.lines:
                if line.role != "term" or not (line.original or "").strip():
                    raise ValueError("a key term line carries the original term")
                if not any(s.kind == "concept" for s in line.sources):
                    raise ValueError("a key term line names its concept")
        if self.kind == "questions":
            roles = [line.role for line in self.lines]
            if len(roles) % 2 or roles != ["question", "answer"] * (len(roles) // 2):
                raise ValueError("questions alternate: question, then its answer")
            if len(roles) // 2 != QUESTIONS_PER_EPISODE:
                raise ValueError("an episode ends with exactly five questions")
            for q, a in zip(self.lines[::2], self.lines[1::2]):
                if q.pause_after_ms < 1000:
                    raise ValueError("each question is followed by a thinking pause (>= 1000 ms)")
                if not any(s.kind == "book" for s in a.sources):
                    raise ValueError("each answer names its location in the lesson")
        return self


class EpisodeScript(Contract):
    format: Literal["b2c.episode-script/1"] = EPISODE_SCRIPT_FORMAT
    book_id: str = Field(min_length=1)
    language: Literal["en", "ar"]
    episode: int = Field(ge=0)  # 0 = course introduction (D-037/D-038); lessons are 1..n
    kind: Literal["course_intro", "lesson"]
    lesson_id: str | None = None
    title: str
    book_title: str = Field(min_length=1)
    sections: list[EpisodeSection] = Field(min_length=1)
    translation_run_id: str | None = None      # the book translation run of the book-text units
    source_script_sha256: str | None = None    # Arabic: sha256 of the English script it translates
    generator: ProducerIdentity | None = None
    validator: ProducerIdentity | None = None
    generator_family: str | None = None
    validator_family: str | None = None
    created_at: datetime
    metrics: dict[str, Any] = {}

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _check_title(v)

    @field_validator("created_at")
    @classmethod
    def check_created_utc(cls, v: datetime) -> datetime:
        return _utc(v)

    def label(self, *, last_episode: int) -> str:
        """Zero-padded episode number for file names (D-038): 00, 01, ... (3 digits from 100)."""
        return f"{self.episode:0{max(2, len(str(last_episode)))}d}"

    def all_lines(self) -> list[ScriptLine]:
        return [line for s in self.sections for line in s.lines]

    @model_validator(mode="after")
    def _script(self) -> "EpisodeScript":
        if (self.episode == 0) != (self.kind == "course_intro"):
            raise ValueError("episode 0 is the course introduction, and only it")
        if (self.lesson_id is None) != (self.kind == "course_intro"):
            raise ValueError("a lesson episode names its lesson; the course introduction has none")
        code = "".join(_ORDER_CODE[s.kind] for s in self.sections)
        pattern = _INTRO_ORDER if self.kind == "course_intro" else _LESSON_ORDER
        if not pattern.match(code):
            raise ValueError(f"section order {code} breaks the episode order (D-038)")
        ids = [s.section_id for s in self.sections]
        line_ids = [line.line_id for line in self.all_lines()]
        if len(set(ids)) != len(ids) or len(set(line_ids)) != len(line_ids):
            raise ValueError("section and line ids must be unique")
        if any(s.kind in BOOK_TEXT_KINDS for s in self.sections) and not self.translation_run_id:
            raise ValueError("book text needs the translation_run_id of its units")
        if self.generator_family and self.validator_family and self.generator_family == self.validator_family:
            raise ValueError("the validator must come from another model family than the generator")
        if self.language == "en":
            if any(s.lines for s in self.sections if s.kind in BOOK_TEXT_KINDS):
                raise ValueError("the English script refers to the book text, it does not hold generated book text")
            if any(line.status is not None or line.inserted_originals or line.note_id for line in self.all_lines()):
                raise ValueError("translation status and notes belong to the Arabic script")
        else:
            if not self.source_script_sha256:
                raise ValueError("an Arabic script names the English script it translates (source_script_sha256)")
            for line in self.all_lines():
                if not line.unit_ids or line.status is None:
                    raise ValueError(f"Arabic line {line.line_id} must name its translation unit(s) and decision")
                if line.note_id and line.status == "accepted":
                    raise ValueError(f"Arabic line {line.line_id}: a translator note is for a unit that was not accepted")
        return self
