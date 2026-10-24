from datetime import datetime, timezone

import pytest
from b2c_contracts import CourseOutline, Lesson, LessonSpan, Module, Question
from fsrs import Card

from b2c_pedagogy.srs import build_review_quiz, review_card


def _outline() -> CourseOutline:
    return CourseOutline(
        course_id="course",
        spine_book_id="book",
        supplementary_book_ids=[],
        requires_review=False,
        modules=[Module(module_id="m1", title="Module", order=1, lessons=[
            Lesson(lesson_id="l1", title="First", order=1,
                   spine_span=LessonSpan(start_block="b1", end_block="b1", pages=(1, 1)),
                   provenance="headings", prior_lessons=[]),
            Lesson(lesson_id="l2", title="Second", order=2,
                   spine_span=LessonSpan(start_block="b2", end_block="b2", pages=(2, 2)),
                   provenance="headings", prior_lessons=["l1"]),
        ])],
    )


def _question(qid: str, lesson_id: str, purpose: str = "review_quiz") -> Question:
    return Question(qid=qid, lesson_id=lesson_id, purpose=purpose, type="mcq", bloom="understand",
                    stem="Which concept is described?", options=["A", "B", "C"], answer_index=0,
                    rationale="The source supports option A.", evidence_sids=[f"{lesson_id}:s01"], validation={})


def test_review_quiz_includes_only_questions_from_prior_lessons() -> None:
    bank = [_question("old-review", "l1"), _question("current", "l2"),
            _question("old-lesson", "l1", purpose="end_of_lesson")]

    selected = build_review_quiz(_outline(), "l2", bank)
    assert [q.qid for q in selected] == ["old-review", "old-lesson"]
    assert all(q.purpose == "review_quiz" for q in selected)
    assert build_review_quiz(_outline(), "l1", bank) == []


def test_review_quiz_rejects_unknown_lesson_and_bad_limit() -> None:
    with pytest.raises(ValueError, match="unknown lesson"):
        build_review_quiz(_outline(), "missing", [])
    with pytest.raises(ValueError, match="limit"):
        build_review_quiz(_outline(), "l2", [], limit=0)


def test_fsrs_wrapper_matches_pinned_py_fsrs_deterministic_fixture() -> None:
    reviewed_at = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    card_json, log, due = review_card(user_id="learner", rating=3, reviewed_at=reviewed_at)
    actual = Card.from_json(card_json)

    # Golden output pinned to py-fsrs 6.3.2 with fuzzing disabled.
    assert int(actual.state) == 1  # Learning
    assert actual.stability == pytest.approx(2.3065)
    assert actual.difficulty == pytest.approx(2.118103970459016)
    assert actual.due == datetime(2026, 10, 5, 12, 10, tzinfo=timezone.utc) == due
    assert log.rating == 3 and log.reviewed_at == reviewed_at


def test_fsrs_wrapper_requires_utc_and_persists_card_state() -> None:
    local = datetime(2026, 10, 5, 12).astimezone()
    with pytest.raises(ValueError, match="UTC"):
        review_card(user_id="learner", rating=3, reviewed_at=local)

    reviewed_at = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    first_json, _, _ = review_card(user_id="learner", rating=3, reviewed_at=reviewed_at)
    second_json, _, _ = review_card(user_id="learner", rating=1, reviewed_at=reviewed_at,
                                    card_json=first_json)
    assert Card.from_json(second_json).state == Card.from_json(first_json).state


def test_new_card_id_is_stable_per_user_and_question() -> None:
    from b2c_pedagogy.srs import stable_card_id

    reviewed_at = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    first_json, first_log, _ = review_card(user_id="learner", rating=3, reviewed_at=reviewed_at,
                                           question_id="l1:q001")
    again_json, again_log, _ = review_card(user_id="learner", rating=3, reviewed_at=reviewed_at,
                                           question_id="l1:q001")
    other_q = review_card(user_id="learner", rating=3, reviewed_at=reviewed_at, question_id="l1:q002")[1]
    other_u = review_card(user_id="learner-2", rating=3, reviewed_at=reviewed_at, question_id="l1:q001")[1]
    assert first_json == again_json
    assert first_log.card_id == again_log.card_id == str(stable_card_id("learner", "l1:q001"))
    assert len({first_log.card_id, other_q.card_id, other_u.card_id}) == 3
    assert 0 < int(first_log.card_id) < 2 ** 63
    # The id survives later reviews through the persisted card state.
    _, later, _ = review_card(user_id="learner", rating=1, reviewed_at=reviewed_at, card_json=first_json,
                              question_id="l1:q001")
    assert later.card_id == first_log.card_id
