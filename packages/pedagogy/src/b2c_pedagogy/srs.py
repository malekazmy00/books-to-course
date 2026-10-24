"""Review-quiz scoping and deterministic FSRS scheduling helpers."""
from __future__ import annotations

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


def review_card(
    *,
    user_id: str,
    rating: int,
    reviewed_at: datetime,
    card_json: str | None = None,
) -> tuple[str, ReviewLogEntry, datetime]:
    """Apply one py-fsrs review with deterministic scheduling and UTC-only time."""
    if reviewed_at.tzinfo is None or reviewed_at.utcoffset() != timezone.utc.utcoffset(reviewed_at):
        raise ValueError("reviewed_at must be timezone-aware UTC")
    card = Card.from_json(card_json) if card_json is not None else Card()
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
