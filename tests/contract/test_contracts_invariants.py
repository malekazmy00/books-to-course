"""Contract invariants (Phase 0). Each test is a negative control that MUST raise."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime.now(timezone.utc)
VOICE = c.VoiceSpec(provider="supertonic3", voice_id="M1", cloned=False)


def test_stage_key_is_deterministic_and_order_independent():
    a = c.stage_key("ingest", "1", ["b", "a"], {"x": 1}, "m", "p1")
    b = c.stage_key("ingest", "1", ["a", "b"], {"x": 1}, "m", "p1")
    assert a == b
    assert a != c.stage_key("ingest", "1", ["a", "b"], {"x": 2}, "m", "p1")


def test_cloned_voice_requires_consent():  # D-003
    with pytest.raises(ValidationError):
        c.VoiceSpec(provider="omnivoice", voice_id="v1", cloned=True)


def test_sentence_requires_citation():  # grounding
    with pytest.raises(ValidationError):
        c.LessonSentence(sid="s", text_en="t", citations=[], kind="primary")


def test_naive_datetime_rejected():  # UTC only (py-fsrs requirement)
    with pytest.raises(ValidationError):
        c.ArtifactRef(stage="s", key="k", uri="u", sha256="h", created_at=datetime.now())


def test_release_blocks_uncleared_books():  # D-002 / T-REL-01
    with pytest.raises(ValidationError):
        c.CourseRelease(release_id="r", course_id="c", lesson_versions={}, model_versions={},
                        voice=VOICE, human_reviewed_ratio=0.1,
                        book_rights={"b": "pending"}, created_at=NOW)


def test_mcq_shape():
    with pytest.raises(ValidationError):
        c.Question(qid="q", lesson_id="l", purpose="end_of_lesson", type="mcq", bloom="apply",
                   stem="s", options=["a", "b"], answer_index=0, rationale="r",
                   evidence_sids=["s1"], validation={})


def test_inferred_outline_requires_review():  # Gate G0
    lesson = c.Lesson(lesson_id="m1.l1", title="t", order=1,
                      spine_span=c.LessonSpan(start_block="a", end_block="b", pages=(1, 2)),
                      provenance="llm_inferred")
    with pytest.raises(ValidationError):
        c.CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[],
                        modules=[c.Module(module_id="m1", title="t", order=1, lessons=[lesson])],
                        requires_review=False)


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        c.PageRef(book_id="b", page_no=1, unknown="x")
