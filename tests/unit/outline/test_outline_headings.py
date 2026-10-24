from b2c_contracts import Block, PageRef, ParsedBook, CourseOutline, Lesson, LessonSpan, Module
from b2c_outline.review import merge_bodyless_lessons, review_outline
from b2c_outline import outline_from_book


def parsed(blocks):
    return ParsedBook(book_id="spine", title="Book", role="spine", page_count=2,
        toc_source="pdf_outline", parser={"name": "fixture", "version": "1"}, blocks=blocks)


def block(id, kind, text, level=None):
    values = {"block_id": id, "type": kind, "text": text,
        "refs": [PageRef(book_id="spine", page_no=1 if id != "b5" else 2)],
        "section_path": [], "ocr": False}
    if level is not None:
        values["level"] = level
    return Block(**values)


def test_adjacent_ocr_spaced_duplicate_root_heading_merges_into_content_span():
    book = parsed([
        block("b1", "heading", "Preface", 1),
        block("b2", "heading", "PR EF A C E", 1),
        block("b3", "paragraph", "The substantive preface contains the course concepts.",),
        block("b4", "heading", "Chapter One", 1),
        block("b5", "paragraph", "Chapter one explains a substantive method.",),
    ])
    outline = outline_from_book(book, course_id="course")
    assert [module.module_id for module in outline.modules] == ["m01-chapter-one"]
    assert outline.excluded_prefix_block_ids == ["b1", "b2", "b3"]
    assert outline.excluded_prefix_policy == "publisher-front-matter-v1"
    first_lesson = outline.modules[0].lessons[0]
    assert first_lesson.title == "Chapter One"
    assert first_lesson.spine_span.start_block == "b4"
    assert first_lesson.spine_span.end_block == "b5"


def test_same_heading_after_body_content_is_not_merged():
    book = parsed([
        block("b1", "heading", "Overview", 1),
        block("b2", "paragraph", "First substantive section has content."),
        block("b3", "heading", "Overview", 1),
        block("b4", "paragraph", "Second substantive section has content."),
    ])
    outline = outline_from_book(book, course_id="course")
    assert len(outline.modules) == 2
    assert [module.module_id for module in outline.modules] == [
        "m01-overview", "m02-overview"]


def test_actual_introduction_after_publisher_front_matter_is_first_course_lesson():
    book = parsed([
        block("b1", "heading", "Publisher's Preface", 1),
        block("b2", "paragraph", "This edition was prepared by the publisher."),
        block("b3", "heading", "Introduction", 1),
        block("b4", "paragraph", "The introduction explains the analytical method."),
        block("b5", "heading", "Chapter One", 1),
        block("b6", "paragraph", "The chapter applies that method to evidence."),
    ])
    outline = outline_from_book(book, course_id="course")
    assert outline.modules[0].title == "Introduction"
    assert outline.modules[0].lessons[0].spine_span.start_block == "b3"
    assert outline.excluded_prefix_block_ids == ["b1", "b2"]


def test_arabic_publisher_preface_is_excluded_but_book_introduction_is_kept():
    book = parsed([
        block("b1", "heading", "مقدمة الناشر", 1),
        block("b2", "paragraph", "كلمة الناشر عن هذه الطبعة."),
        block("b3", "heading", "مقدمة الكتاب", 1),
        block("b4", "paragraph", "تشرح المقدمة المفهوم الأساسي وطريقة التحليل."),
    ])
    outline = outline_from_book(book, course_id="course")
    assert [module.title for module in outline.modules] == ["مقدمة الكتاب"]
    assert outline.excluded_prefix_block_ids == ["b1", "b2"]


def test_heading_without_body_does_not_become_a_separate_lesson():
    book = parsed([
        block("b1", "heading", "Chapter One", 1),
        block("b2", "heading", "First Topic", 2),
        block("b3", "paragraph", "The first topic has substantive instructional content."),
        block("b4", "heading", "Heading Without Content", 2),
        block("b5", "heading", "Second Topic", 2),
        block("b6", "paragraph", "The second topic also has substantive instructional content."),
    ])
    outline = outline_from_book(book, course_id="course")
    lessons = outline.modules[0].lessons
    assert [lesson.title for lesson in lessons] == ["First Topic", "Second Topic"]
    assert lessons[0].spine_span.end_block == "b4"


def test_empty_top_level_heading_is_not_an_empty_lesson_and_source_stays_covered():
    book = parsed([
        block("b1", "heading", "Chapter One", 1),
        block("b2", "paragraph", "First chapter has substantive content."),
        block("b3", "heading", "Repeated Chapter Heading", 1),
        block("b4", "heading", "Chapter Two", 1),
        block("b5", "paragraph", "Second chapter has substantive content."),
    ])
    outline = outline_from_book(book, course_id="course")
    assert [module.title for module in outline.modules] == ["Chapter One", "Chapter Two"]
    assert outline.modules[0].lessons[0].spine_span.end_block == "b3"
    assert outline.modules[1].lessons[0].spine_span.start_block == "b4"


def test_empty_leading_top_level_heading_is_covered_by_first_real_lesson():
    book = parsed([
        block("b1", "heading", "Repeated Chapter Heading", 1),
        block("b2", "heading", "Chapter One", 1),
        block("b3", "paragraph", "Chapter one has substantive content."),
    ])
    outline = outline_from_book(book, course_id="course")
    assert [module.title for module in outline.modules] == ["Chapter One"]
    assert outline.modules[0].lessons[0].spine_span.start_block == "b1"



def test_system_outline_repair_merges_empty_module_into_next_substantive_lesson():
    book = parsed([
        block("b1", "heading", "Chapter One", 1),
        block("b2", "paragraph", "Chapter one has substantive material."),
        block("b3", "heading", "Chapter Heading Without Body", 1),
        block("b4", "heading", "Chapter Two", 1),
        block("b5", "paragraph", "Chapter two has substantive material."),
    ])
    draft = CourseOutline(
        course_id="course", spine_book_id="spine", supplementary_book_ids=[], requires_review=True,
        modules=[
            Module(module_id="m1", title="Chapter One", order=1, lessons=[Lesson(
                lesson_id="m1.l1", title="Chapter One", order=1,
                spine_span=LessonSpan(start_block="b1", end_block="b2", pages=(1, 1)),
                provenance="pdf_outline")]),
            Module(module_id="m2", title="Empty chapter", order=2, lessons=[Lesson(
                lesson_id="m2.l1", title="Chapter Heading Without Body", order=1,
                spine_span=LessonSpan(start_block="b3", end_block="b3", pages=(1, 1)),
                provenance="pdf_outline", prior_lessons=["m1.l1"])]),
            Module(module_id="m3", title="Chapter Two", order=3, lessons=[Lesson(
                lesson_id="m3.l1", title="Chapter Two", order=1,
                spine_span=LessonSpan(start_block="b4", end_block="b5", pages=(1, 1)),
                provenance="pdf_outline", prior_lessons=["m1.l1", "m2.l1"])]),
        ])
    repaired = merge_bodyless_lessons(book, draft)
    assert [module.title for module in repaired.modules] == ["Chapter One", "Chapter Two"]
    assert repaired.modules[1].lessons[0].spine_span.start_block == "b3"
    assert repaired.modules[1].lessons[0].prior_lessons == ["m1.l1"]
    review_outline(book, repaired)


