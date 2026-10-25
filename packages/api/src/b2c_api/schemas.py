"""Request bodies accepted by the HTTP API."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from b2c_contracts import ArtifactRef, CourseOutline
from b2c_translation.glossary import ContextualGlossary
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OutlineReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: Literal["human", "system"] = "human"
    reviewer_id: str | None = Field(default=None, min_length=1, max_length=120)
    outline: CourseOutline
    supplementary_workflow_ids: dict[str, str] = Field(default_factory=dict)
    target_tokens: int = Field(default=500, ge=300, le=800)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)
    index_after_approval: bool = True

    @model_validator(mode="after")
    def validate_reviewer(self) -> OutlineReview:
        if self.review_type == "human" and not self.reviewer_id:
            raise ValueError("human review requires reviewer_id")
        if self.review_type == "system" and self.reviewer_id is not None:
            raise ValueError("system review must not provide reviewer_id")
        return self


class SystemOutlineApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supplementary_workflow_ids: dict[str, str] = Field(default_factory=dict)
    target_tokens: int = Field(default=500, ge=300, le=800)
    overlap_ratio: float = Field(default=0.12, ge=0, lt=0.5)
    index_after_approval: bool = True


class CourseQAGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved_outline_ref: ArtifactRef
    index_workflow_id: str = Field(pattern=r"^b2c-index-[0-9a-f]{64}$")
    lesson_question_count: int = Field(default=5, ge=1, le=20)
    group_question_count: int = Field(default=5, ge=1, le=20)
    course_question_count: int = Field(default=10, ge=1, le=20)
    lesson_mcq_count: int = Field(default=5, ge=3, le=20)
    include_lesson_enrichment: bool = False
    include_review_assessments: bool = False
    assessment_question_count: int = Field(default=10, ge=5, le=20)
    max_tokens: int = Field(default=4096, ge=512, le=16384)
    lesson_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    force_regenerate: bool = False
    retrieval_mode: Literal["lexical", "hybrid"] | None = None
    retrieval_provider_id: Literal["lexical", "bge-m3", "embeddinggemma2-lite", "lightrag-157", "hipporag-2"] | None = None
    retrieval_top_k: int | None = Field(default=None, ge=1, le=100)


class TranslationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contextual_glossary: ContextualGlossary | None = None
    provider_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    source_artifact_ref: ArtifactRef
    source_kind: Literal["book", "course"] = "book"
    source_language: Literal["en", "ar"]
    target_language: Literal["en", "ar"]
    max_tokens: int = Field(default=8192, ge=512, le=16384)
    resume_from_workflow_id: str | None = Field(default=None, pattern=r"^b2c-translate-[0-9a-f]{64}$")
    force_regenerate: bool = False


class TranslationRetranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=160)
    # Optional client token: a new value requests another candidate for the
    # same unchanged segment; omitting it makes retries return the same one.
    request_nonce: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class TranslationRevisionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1,max_length=160)
    revision_id: str | None = Field(default=None,pattern=r"^[0-9a-f]{32,64}$")


class TranslationCandidateAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class TranslationCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1, max_length=160)
    translated_text: str = Field(min_length=1, max_length=24000)


class MP3GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_artifact_ref: ArtifactRef
    source_kind: Literal["book", "course"]
    language: Literal["en", "ar"]
    approved_outline_ref: ArtifactRef | None = None
    source_qa_artifact_ref: ArtifactRef | None = None
    source_book_ref: ArtifactRef | None = None
    source_book_translation_ref: ArtifactRef | None = None
    book_audio_layout: Literal["full_book", "lessons"] = "full_book"
    lesson_ids: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    allow_partial_qa: bool = False
    include_summaries: bool = True
    include_extra_scopes: bool = True
    # R0 (audits 07 C3, 09 C2): legacy translate-content sources are refused unless this is set;
    # the produced audio is then marked unverified_legacy.
    allow_unverified_legacy: bool = False

    @model_validator(mode="after")
    def validate_lesson_selection(self):
        if self.lesson_ids is not None and (self.source_kind == "book" and self.book_audio_layout != "lessons"):
            raise ValueError("lesson_ids require book audio with lessons layout")
        if self.lesson_ids is not None and len(set(self.lesson_ids)) != len(self.lesson_ids):
            raise ValueError("lesson_ids must be unique")
        return self

class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    question_id: str = Field(min_length=1, max_length=200)
    rating: Literal[1, 2, 3, 4]
    reviewed_at: datetime

    @field_validator("reviewed_at")
    @classmethod
    def check_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("reviewed_at must be timezone-aware UTC")
        return value


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    book_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    title: str = Field(min_length=1, max_length=300)
    role: Literal["spine", "supplementary"]
    rights_status: Literal["cleared", "pending", "internal_test_only"]
    ocr_language: Literal["en", "ar"] = "en"
    ocr_engine: Literal["rapidocr", "surya", "openrouter"] | None = None
    supplementary_book_ids: list[str] = Field(default_factory=list, max_length=100)
    force_reprocess: bool = False
