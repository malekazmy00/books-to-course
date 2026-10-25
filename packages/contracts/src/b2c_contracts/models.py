"""b2c_contracts — single source of truth for data exchanged between layers.

Rules:
- No business logic here. No imports from other project packages.
- Any change => bump SCHEMA_VERSION and update docs/02-CONTRACTS.md.
- All datetimes are timezone-aware UTC.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.3.0"


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


# ---------------------------------------------------------------- L3 chunks
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
class TermbaseEntry(Contract):
    term_en: str
    term_ar: str
    term_ar_diacritized: str | None = None
    definition_en: str
    forbidden_variants_ar: list[str] = []
    do_not_translate: bool = False
    approved_by: str | None = None  # None => not approved yet (Gate G3)


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
