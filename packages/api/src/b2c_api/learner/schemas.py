"""Learner API v3 models (the contract the Android learner app is built against).

These are learner projections, not stored artifacts: every field is listed here explicitly and
nothing that answers a question appears in a GET model (R0, audits 06 C4 / 09 C4). The only model
that carries an answer is ``RevealResponse``, returned by the explicit reveal call.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "3.0.0"
MIN_CLIENT_VERSION = 1

Voice = Literal["author", "course", "external"]
SectionKind = Literal["intro", "book_text", "summary", "key_terms", "questions", "other"]
SegmentRole = Literal["text", "heading", "figure_description", "note", "question", "answer", "pause"]
EpisodeKind = Literal["course_intro", "lesson"]
EpisodeStatus = Literal["ready", "script_pending"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearnerMeta(_Model):
    api_version: str = API_VERSION
    min_client_version: int = MIN_CLIENT_VERSION
    mapping_version: str
    sources: dict[str, str] = Field(description="stage names each learner resource is read from")


class CourseSummary(_Model):
    course_id: str
    title: str
    language: str
    episode_count: int
    updated_at: str


class CourseList(_Model):
    courses: list[CourseSummary]


class EpisodeSummary(_Model):
    number: int = Field(ge=0, description="0 = course introduction (D-037/D-038), then 1..n")
    label: str = Field(description="zero-padded number, as in the MP3 file names (D-038)")
    title: str
    kind: EpisodeKind
    lesson_id: str | None = None
    script_available: bool
    audio_available: bool
    duration_s: float | None = None


class CourseDetail(_Model):
    course_id: str
    title: str
    language: str
    episodes: list[EpisodeSummary]
    concepts_available: bool
    updated_at: str


class Segment(_Model):
    segment_id: str
    voice: Voice
    role: SegmentRole
    text: str | None = Field(description="null when hidden (an answer before reveal)")
    hidden: bool = False
    start_s: float | None = None
    end_s: float | None = None
    source_label: str | None = Field(default=None, description="for the external voice: who says it")
    pages: list[int] = []


class Section(_Model):
    section_id: str
    kind: SectionKind
    title: str
    start_s: float | None = None
    end_s: float | None = None
    segments: list[Segment]


class KeyTerm(_Model):
    term: str
    original: str | None = None
    definition: str | None = None
    concept_id: str | None = None


class LearnerQuestion(_Model):
    question_id: str
    prompt: str
    options: list[str] | None = None
    question_segment_id: str | None = None
    answer_segment_id: str | None = Field(default=None, description="where the answer is spoken (text hidden)")


class EpisodeAudio(_Model):
    available: bool
    path: str | None = Field(default=None, description="API path of the MP3 (bearer token, Range supported)")
    duration_s: float | None = None
    size_bytes: int | None = None


class EpisodeDetail(_Model):
    course_id: str
    number: int
    label: str
    title: str
    kind: EpisodeKind
    lesson_id: str | None = None
    status: EpisodeStatus
    language: str
    duration_s: float | None = None
    audio: EpisodeAudio
    sections: list[Section]
    key_terms: list[KeyTerm]
    questions: list[LearnerQuestion]


class RevealRequest(_Model):
    selected_option: int | None = Field(default=None, ge=0)
    response_text: str | None = Field(default=None, max_length=2000)


class RevealResponse(_Model):
    question_id: str
    answer: str
    correct: bool | None = Field(description="null when the answer cannot be checked automatically")
    answer_segment_id: str | None = None
    answer_section_id: str | None = None


class AuthorQuote(_Model):
    quote: str
    pages: list[int] = []


class ExternalNote(_Model):
    statement: str
    urls: list[str]


class ConceptRelationOut(_Model):
    concept_id: str
    term: str
    relation: str


class ConceptCard(_Model):
    concept_id: str
    term: str = Field(description="the original (book-language) term")
    term_ar: str | None = None
    kind: str
    gloss: str | None = Field(default=None, description="course voice")
    author_quotes: list[AuthorQuote] = Field(default=[], description="author voice, verbatim")
    external_notes: list[ExternalNote] = Field(default=[], description="external voice, with sources")
    first_lesson_id: str | None = None
    first_episode: int | None = None
    relations: list[ConceptRelationOut] = []


class ConceptList(_Model):
    course_id: str
    language: str
    status: Literal["ready", "pending"]
    concepts: list[ConceptCard]
