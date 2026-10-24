"""Review-quiz scoping and deterministic FSRS scheduling helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Sequence

from b2c_contracts import CourseOutline, Question, ReviewLogEntry
from fsrs import Card, Rating, Scheduler


def build_review_quiz(
    outline: CourseOutline,
    lesson_id: str,
    question_bank: Sequence[Question],
    *,
    limit: int = 10,
) -> list[Question]:
    """Build a review quiz from generated questions in prerequisite lessons only."""
    if limit < 1:
        raise ValueError("limit must be positive")
    lessons = {lesson.lesson_id: lesson for module in outline.modules for lesson in module.lessons}
    lesson = lessons.get(lesson_id)
    if lesson is None:
        raise ValueError(f"unknown lesson: {lesson_id}")
    allowed = set(lesson.prior_lessons)
    if not allowed:
        return []
    selected = [question.model_copy(update={"purpose": "review_quiz"})
                for question in question_bank if question.lesson_id in allowed]
    return selected[:limit]


def stable_card_id(user_id: str, question_id: str) -> int:
    """Deterministic positive 63-bit card id for one (user, question) pair."""
    key = json.dumps([user_id, question_id], ensure_ascii=True, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(key.encode("ascii")).digest()[:8], "big") & ((1 << 63) - 1)


def review_card(
    *,
    user_id: str,
    rating: int,
    reviewed_at: datetime,
    card_json: str | None = None,
    question_id: str | None = None,
) -> tuple[str, ReviewLogEntry, datetime]:
    """Apply one py-fsrs review with deterministic scheduling and UTC-only time.

    A new card's id is derived from (user_id, question_id). py-fsrs' default id is
    the wall-clock time in milliseconds, so callers must pass question_id for a
    reproducible id; without it the legacy time-based id is kept. Existing cards
    keep the id stored in card_json.
    """
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() != timezone.utc.utcoffset(reviewed_at):
        raise ValueError("reviewed_at must be timezone-aware UTC")
    if card_json is not None:
        card = Card.from_json(card_json)
    elif question_id is not None:
        card = Card(card_id=stable_card_id(user_id, question_id))
    else:
        card = Card()
    updated, _fsrs_log = Scheduler(enable_fuzzing=False).review_card(
        card, Rating(rating), review_datetime=reviewed_at
    )
    log = ReviewLogEntry(
        card_id=str(updated.card_id),
        user_id=user_id,
        rating=rating,
        reviewed_at=reviewed_at,
    )
    return updated.to_json(), log, updated.due
