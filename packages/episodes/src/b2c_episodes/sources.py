"""Inputs of the episode generators, built by orchestration from stored artifacts (no other layer
is imported here): the lesson's English blocks, the previous lessons, the lesson's first-mentioned
concepts (ConceptLayer), the knowledge dossier, and the book-text translation units.

Grounding helpers: a quote is valid when it occurs in the cited block's text after normalizing
case, whitespace, quote marks and dashes (same rule as ``b2c_concepts.grounding``)."""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from b2c_contracts import BookSpan, KnowledgeDossier

_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "‚": "'",
                         "–": "-", "—": "-", "―": "-", "‐": "-", "‑": "-", "…": "..."})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    text = re.sub(r"\s*-\s*", " - ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    q = normalize(quote).strip(" .\"'")
    return len(q) >= 8 and q in normalize(text)


@dataclass(frozen=True)
class SourceBlock:
    block_id: str
    type: str
    text: str
    pages: tuple[int, ...] = ()


@dataclass(frozen=True)
class LessonSource:
    lesson_id: str
    episode: int
    title: str
    blocks: tuple[SourceBlock, ...]

    def text(self, limit: int | None = None) -> str:
        out = "\n".join(b.text for b in self.blocks)
        return out if limit is None else out[:limit]

    def words(self) -> int:
        return sum(len(b.text.split()) for b in self.blocks if b.type != "heading")


@dataclass(frozen=True)
class TermInfo:
    """A concept first mentioned in the lesson (ConceptLayer ``LessonConceptList`` + entry)."""
    concept_id: str
    term: str
    kind: str = "term"
    approved: bool = False
    definition: BookSpan | None = None   # the author's definition (verified span)
    gloss: str | None = None             # course-voice paraphrase of that definition
    introduced_at: BookSpan | None = None
    external: tuple[tuple[str, str, tuple[str, ...]], ...] = ()  # (claim_id, statement, urls)


@dataclass(frozen=True)
class BookTextRef:
    """The faithful core of the episode: lesson blocks and their translation units (book run)."""
    block_ids: tuple[str, ...]
    unit_ids: tuple[str, ...]


@dataclass
class EpisodePlan:
    book_id: str
    book_title: str
    lesson: LessonSource
    previous: list[LessonSource]
    terms: list[TermInfo]
    book_text: BookTextRef
    translation_run_id: str
    last_episode: int
    dossier: KnowledgeDossier | None = None


@dataclass
class CourseIntroPlan:
    book_id: str
    book_title: str
    authors: list[str]
    dossier: KnowledgeDossier | None
    front_lessons: list[LessonSource]          # foreword / preface / acknowledgments (author's words)
    episode_titles: list[tuple[int, str]]      # (episode, title) of the book lessons
    terms_total: int = 0
    acknowledgment_blocks: Sequence[str] = field(default_factory=tuple)


__all__ = ["BookTextRef", "CourseIntroPlan", "EpisodePlan", "LessonSource", "SourceBlock", "TermInfo",
           "normalize", "quote_in_text"]
