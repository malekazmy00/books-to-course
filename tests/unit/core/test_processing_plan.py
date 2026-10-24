import pytest
from b2c_contracts import Block, PageRef, ParsedBook
from b2c_core.processing_plan import PageEvidence, build_processing_plan, detect_source_language, plan_parsed_book

EN = "The book is for the reader and the author. This is the explanation of the method with examples in the chapter."
AR = "هذا الكتاب يشرح المفاهيم الأساسية للقارئ ويقدم أمثلة تعليمية واضحة لكل موضوع مع شرح تفصيلي للمعلومات والأسئلة"


def page(text=AR, **kwargs):
    return PageEvidence(page_no=1, text=text, native_text_usable=True, **kwargs)


def test_arabic_audio_skips_translation_and_keeps_questions():
    plan = build_processing_plan(pages=[page()], target_output="audio", target_language="ar")
    assert plan.source_language == "ar"
    assert plan.required_stages == ["ocr", "outline", "rag", "course", "questions", "audio"]
    assert "translation" in plan.skipped_stages
    assert not plan.requires_user_input
    assert plan.model_dump()["document_profile"]["classification"] == "digital"


def test_english_audio_requires_translation():
    plan = build_processing_plan(pages=[page(EN)], target_output="audio", target_language="ar")
    assert plan.source_language == "en"
    assert "translation" in plan.required_stages


def test_native_audio_book_does_not_force_rag_or_questions():
    plan = build_processing_plan(pages=[page()], target_output="audio", target_language="ar", content_kind="book")
    assert plan.required_stages == ["ocr", "audio"]


def test_rag_output_requires_extraction_only():
    plan = build_processing_plan(pages=[page()], target_output="rag")
    assert plan.required_stages == ["ocr", "rag"]
    assert plan.document_profile.ocr_required_pages == []


def test_scanned_page_requires_ocr_while_native_page_reuses_text():
    pages = [page(), PageEvidence(page_no=2, has_images=True, native_text_usable=False)]
    plan = build_processing_plan(pages=pages, target_output="rag")
    assert plan.document_profile.classification == "mixed"
    assert plan.document_profile.ocr_required_pages == [2]
    assert plan.document_profile.digital_pages == [1]


def test_blank_page_never_inferred_from_missing_text():
    pages = [page(), PageEvidence(page_no=3, verified_blank=True)]
    plan = build_processing_plan(pages=pages, target_output="ocr")
    assert plan.document_profile.unknown_pages == [2]
    assert plan.document_profile.blank_pages == [3]
    assert plan.requires_user_input


def test_nonenglish_latin_never_assumed_english():
    text = "Ce livre explique les principes essentiels pour comprendre chaque chapitre avec plusieurs exemples pratiques."
    assert detect_source_language(text)[0] == "und"
    plan = build_processing_plan(pages=[page(text)], target_output="audio", target_language="ar")
    assert "confirm_source_language" in plan.reasons
    assert next(s for s in plan.stages if s.stage == "audio").action == "await_user"


def test_mixed_language_requires_user_decision():
    plan = build_processing_plan(pages=[page(AR + EN)], target_output="audio", target_language="ar")
    assert plan.source_language == "mul"
    assert plan.requires_user_input


def test_explicit_user_language_override():
    plan = build_processing_plan(pages=[page("اسمي")], target_output="audio", target_language="ar", source_language="ar")
    assert plan.confidence == 1
    assert not plan.requires_user_input


def test_missing_target_language_does_not_guess():
    plan = build_processing_plan(pages=[page()], target_output="audio")
    assert "choose_output_language" in plan.reasons


def test_verified_imported_rag_can_be_exported_without_reprocessing():
    plan = build_processing_plan(pages=[], target_output="rag", available_stages=["rag"])
    assert plan.required_stages == []
    assert plan.reused_stages == ["rag"]
    assert not plan.requires_user_input


def test_missing_source_requires_input():
    plan = build_processing_plan(pages=[], target_output="ocr")
    assert "provide_source_or_verified_extraction" in plan.reasons


def test_duplicate_page_numbers_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_processing_plan(pages=[page(), page()], target_output="ocr")


def test_parsed_scan_keeps_scan_classification_but_ocr_is_complete():
    book = ParsedBook(book_id="b", title="test", role="spine", page_count=1, toc_source="headings", parser={},
        blocks=[Block(block_id="b:1:0", type="paragraph", text=AR, refs=[PageRef(book_id="b", page_no=1)], section_path=[], ocr=True)])
    plan = plan_parsed_book(book, "audio", "ar", available_stages=["ocr"])
    assert plan.document_profile.classification == "scanned"
    assert plan.document_profile.ocr_completed_pages == [1]
    assert plan.document_profile.ocr_required_pages == []
    assert plan.reused_stages == ["ocr"]


def test_same_language_translation_is_content_export():
    plan = build_processing_plan(pages=[page()], target_output="translation", target_language="ar", content_kind="book")
    assert plan.required_stages == ["ocr"]
    assert "translation" in plan.skipped_stages


def test_course_output_does_not_force_question_generation():
    plan = build_processing_plan(pages=[page()], target_output="course")
    assert plan.required_stages == ["ocr", "outline", "rag", "course"]
    assert "questions" in plan.skipped_stages


def test_translation_waiting_for_language_does_not_schedule_audio():
    plan = build_processing_plan(pages=[page()], target_output="translation")
    assert "audio" in plan.skipped_stages
    assert "audio" not in plan.required_stages
