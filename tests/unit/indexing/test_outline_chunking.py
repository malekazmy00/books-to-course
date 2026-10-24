import pytest

from b2c_contracts.models import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_indexing import chunk_book


def make_no_toc_book():
    return ParsedBook(book_id="spine", title="No TOC", role="spine", page_count=2,
        toc_source="llm_inferred", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id=f"b{i}", type="paragraph", text=f"Text for concept {i}.",
                  refs=[PageRef(book_id="spine", page_no=1 if i < 3 else 2)], section_path=[], ocr=False)
            for i in range(1, 5)
        ])


def make_outline():
    def lesson(module_id, lesson_id, title, start, end, order):
        return Lesson(lesson_id=lesson_id, title=title, order=order,
            spine_span=LessonSpan(start_block=start, end_block=end, pages=(1, 2)),
            provenance="llm_inferred", prior_lessons=[])
    return CourseOutline(course_id="course", spine_book_id="spine", supplementary_book_ids=[],
        requires_review=True, modules=[
            Module(module_id="m1", title="Module A", order=1,
                   lessons=[lesson("m1", "m1.l1", "Concept A", "b1", "b2", 1)]),
            Module(module_id="m2", title="Module B", order=2,
                   lessons=[lesson("m2", "m2.l1", "Concept B", "b3", "b4", 1)]),
        ])


def test_rag_chunks_use_phase2_outline_as_section_boundaries():
    chunks = chunk_book(make_no_toc_book(), target_tokens=100, outline=make_outline())
    assert [chunk.section_path for chunk in chunks] == [["Module A", "Concept A"], ["Module B", "Concept B"]]
    assert chunks[0].block_ids == ["b1", "b2"]
    assert chunks[1].block_ids == ["b3", "b4"]


def test_chunker_rejects_outline_for_another_spine_book():
    book = make_no_toc_book().model_copy(update={"book_id": "other"})
    with pytest.raises(ValueError, match="does not match"):
        chunk_book(book, outline=make_outline())


def test_chunker_rejects_unknown_outline_span_endpoints():
    outline = make_outline()
    bad_lesson = outline.modules[0].lessons[0].model_copy(update={
        "spine_span": LessonSpan(start_block="unknown", end_block="b2", pages=(1, 2)),
    })
    bad_module = outline.modules[0].model_copy(update={"lessons": [bad_lesson]})
    bad_outline = outline.model_copy(update={"modules": [bad_module, outline.modules[1]]})
    with pytest.raises(ValueError, match="unknown block"):
        chunk_book(make_no_toc_book(), outline=bad_outline)
