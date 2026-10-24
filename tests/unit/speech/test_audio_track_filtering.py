from types import SimpleNamespace

from b2c_orchestration.audio import is_front_matter_lesson, renderer_status_retryable


def test_skips_ocr_spaced_preface_but_keeps_real_introduction():
    assert is_front_matter_lesson(SimpleNamespace(lesson_id="m07-pr-ef-a-c-e.l01", title="PR EF A C E"))
    assert is_front_matter_lesson(SimpleNamespace(lesson_id="lesson-2", title="Publisher’s Introduction"))
    assert not is_front_matter_lesson(SimpleNamespace(lesson_id="lesson-3", title="1.1 Introduction to Analysis"))
    assert not is_front_matter_lesson(SimpleNamespace(lesson_id="lesson-4", title="Introduction and Overview"))


def test_renderer_http_retry_classification_is_bounded():
    assert renderer_status_retryable(408)
    assert renderer_status_retryable(429)
    assert renderer_status_retryable(503)
    assert not renderer_status_retryable(400)
    assert not renderer_status_retryable(404)
