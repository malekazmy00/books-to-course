"""R0 containment (audits 07 C3, 09 C2): audio consumes only explicitly accepted translations.

* The rebuilt translation (translate-book v2 assembly) is a valid audio source; only its blocks
  with ``complete=true`` are spoken, and a block that is not accepted refuses the request.
* A legacy translation (TranslateContentWorkflow, stage ``translate-content``) is refused unless
  the request carries ``allow_unverified_legacy=true``; that choice is then recorded in the
  progress record, the manifest and every produced track, so it can never pass as verified.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from b2c_contracts import Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_orchestration import audio as audio_module
from b2c_orchestration.audio import (
    ACCEPTED_TRANSLATION_STAGE,
    LEGACY_TRANSLATION_STAGE,
    VERIFICATION_FILE,
    GenerateMP3Input,
    MP3Activities,
    UnverifiedLegacySource,
    validate_mp3_input_lineage,
)
from b2c_orchestration.translate_book import ASSEMBLY_STAGE, UNITS_STAGE

from tests.unit.orchestration.test_audio_tracks import Store, ref

AR = ["الدرس العربي الأول", "الدرس العربي الثاني", "الدرس العربي الثالث"]


def _fixture(blocks_override=None, build_source_sha=None):
    outline = CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[], requires_review=False,
        modules=[Module(module_id="m1", title="Body", order=1, lessons=[
            Lesson(lesson_id="m1.l1", title="Lesson one", order=1,
                   spine_span=LessonSpan(start_block="b:1:0", end_block="b:2:0", pages=(1, 2)), provenance="headings"),
            Lesson(lesson_id="m1.l2", title="Lesson two", order=2,
                   spine_span=LessonSpan(start_block="b:3:0", end_block="b:4:0", pages=(3, 4)), provenance="headings")])])
    parsed_ref = ref("ingest-parse-pdf", "c")
    parsed = ParsedBook(book_id="b", title="Book", role="spine", page_count=4, toc_source="headings",
        blocks=[Block(block_id=f"b:{i}:0", type="paragraph", text=f"Original English paragraph number {i}.",
                      refs=[PageRef(book_id="b", page_no=i)], section_path=["Body"], ocr=False) for i in (1, 2, 3, 4)],
        parser={"name": "fixture", "version": "1", "options_hash": "d" * 64})
    store = Store(outline, {}, parsed_ref, parsed)
    units_ref = ref(UNITS_STAGE, "9")
    blocks = {
        "b:1:0": {"text": AR[0], "complete": True, "pending_unit_ids": [], "merged_into": None},
        # b:2:0 was joined into b:1:0 across a page break: its text lives in the owner block.
        "b:2:0": {"text": None, "complete": True, "pending_unit_ids": [], "merged_into": "b:1:0"},
        "b:3:0": {"text": AR[1], "complete": True, "pending_unit_ids": [], "merged_into": None},
        "b:4:0": {"text": AR[2], "complete": True, "pending_unit_ids": [], "merged_into": None},
    }
    blocks.update(blocks_override or {})
    store.payloads[(ACCEPTED_TRANSLATION_STAGE, "translation.json")] = json.dumps({
        "run_id": "r" * 64, "units_ref": units_ref.model_dump(mode="json"), "results_digest": "0" * 64,
        "assembly_version": "assemble-2026-10-24.1", "counts": {}, "engines": [], "total_units": 3,
        "blocks": blocks}).encode()
    source = parsed_ref if build_source_sha is None else parsed_ref.model_copy(update={"sha256": build_source_sha})
    store.payloads[(UNITS_STAGE, "build.json")] = json.dumps({
        "book_id": "b", "source_artifact_ref": source.model_dump(mode="json"), "unit_count": 3}).encode()
    store.payloads[(LEGACY_TRANSLATION_STAGE, "translation.json")] = json.dumps({
        "status": "completed", "source_language": "en", "target_language": "ar", "total_segments": 4,
        "segments": [{"segment_id": f"b:{i}:0", "text": f"ترجمة قديمة غير موثقة {i}"} for i in (1, 2, 3, 4)],
        "missing_segment_ids": [], "failed_segment_ids": []}).encode()
    store.payloads[(LEGACY_TRANSLATION_STAGE, "translation-source.json")] = json.dumps({
        "source_artifact_ref": parsed_ref.model_dump(mode="json")}).encode()

    class Progress:
        def __init__(self):
            self.records = []

        def put_object(self, **kwargs):
            self.records.append(json.loads(kwargs["Body"]))

        def get_object(self, **kwargs):
            missing = RuntimeError("NoSuchKey")
            missing.response = {"ResponseMetadata": {"HTTPStatusCode": 404}}
            raise missing

    store.s3 = Progress()
    return store, parsed_ref, ref("approve-outline", "b")


def _book_input(source, parsed_ref, outline_ref, layout="lessons", **kw):
    return GenerateMP3Input(course_id="c", source_artifact_ref=source.model_dump(mode="json"), source_kind="book",
        language="ar", approved_outline_ref=outline_ref.model_dump(mode="json") if layout == "lessons" else None,
        source_book_ref=parsed_ref.model_dump(mode="json") if layout == "lessons" else None,
        book_audio_layout=layout, **kw)


def _activity(store, monkeypatch):
    monkeypatch.setattr(audio_module.activity, "info", lambda: SimpleNamespace(workflow_id="b2c-mp3-test"))
    activity = MP3Activities(store)
    rendered = []
    monkeypatch.setattr(activity, "_render",
                        lambda text, language, progress_callback=None: (rendered.append(text) or b"ID3" + b"\0" * 300))
    return activity, rendered


def test_assembly_stage_name_matches_the_translate_book_pipeline():
    assert ACCEPTED_TRANSLATION_STAGE == ASSEMBLY_STAGE


def test_legacy_translation_is_refused_without_explicit_opt_in():
    store, parsed_ref, outline_ref = _fixture()
    inp = _book_input(ref(LEGACY_TRANSLATION_STAGE, "e"), parsed_ref, outline_ref)
    with pytest.raises(UnverifiedLegacySource, match="allow_unverified_legacy"):
        validate_mp3_input_lineage(store, inp)


def test_legacy_course_book_translation_is_refused_without_explicit_opt_in():
    store, parsed_ref, outline_ref = _fixture()
    qa_ref = ref("generate-course-qa", "a")
    inp = GenerateMP3Input(course_id="c", source_artifact_ref=qa_ref.model_dump(mode="json"), source_kind="course",
        language="ar", approved_outline_ref=outline_ref.model_dump(mode="json"),
        source_qa_artifact_ref=qa_ref.model_dump(mode="json"), source_book_ref=parsed_ref.model_dump(mode="json"),
        source_book_translation_ref=ref(LEGACY_TRANSLATION_STAGE, "e").model_dump(mode="json"))
    with pytest.raises(UnverifiedLegacySource):
        validate_mp3_input_lineage(store, inp)


def test_accepted_assembly_renders_only_complete_blocks_and_records_verification(monkeypatch):
    store, parsed_ref, outline_ref = _fixture()
    activity, rendered = _activity(store, monkeypatch)
    result = activity.generate(_book_input(ref(ACCEPTED_TRANSLATION_STAGE, "7"), parsed_ref, outline_ref))
    assert rendered == [AR[0], AR[1] + "\n" + AR[2]]
    assert result["unverified_legacy"] is False
    assert result["manifest"].get("unverified_legacy") is not True
    for track in result["manifest"]["tracks"]:
        record = json.loads(store.files[track["artifact_ref"]["key"]][VERIFICATION_FILE])
        assert record == {"unverified_legacy": False, "translation_source": ACCEPTED_TRANSLATION_STAGE,
                          "translation_sha256": "7" * 64}
        assert track["unverified_legacy"] is False
    assert all(record["unverified_legacy"] is False for record in store.s3.records)


def test_accepted_assembly_full_book_follows_parsed_order(monkeypatch):
    store, parsed_ref, outline_ref = _fixture()
    activity, rendered = _activity(store, monkeypatch)
    result = activity.generate(_book_input(ref(ACCEPTED_TRANSLATION_STAGE, "7"), parsed_ref, outline_ref,
                                           layout="full_book"))
    assert rendered == ["\n".join(AR)]
    assert result["unverified_legacy"] is False
    assert json.loads(store.files[result["artifact"]["key"]][VERIFICATION_FILE])["unverified_legacy"] is False


def test_assembly_with_a_block_not_accepted_is_refused():
    store, parsed_ref, outline_ref = _fixture(
        {"b:3:0": {"text": None, "complete": False, "pending_unit_ids": ["b:u00002"], "merged_into": None}})
    inp = _book_input(ref(ACCEPTED_TRANSLATION_STAGE, "7"), parsed_ref, outline_ref)
    with pytest.raises(ValueError, match="not accepted"):
        validate_mp3_input_lineage(store, inp)


def test_assembly_of_another_parsed_book_is_refused():
    store, parsed_ref, outline_ref = _fixture(build_source_sha="f" * 64)
    inp = _book_input(ref(ACCEPTED_TRANSLATION_STAGE, "7"), parsed_ref, outline_ref)
    with pytest.raises(ValueError, match="different source"):
        validate_mp3_input_lineage(store, inp)


def test_assembly_in_the_wrong_language_is_refused():
    store, parsed_ref, outline_ref = _fixture()
    inp = _book_input(ref(ACCEPTED_TRANSLATION_STAGE, "7"), parsed_ref, outline_ref).model_copy(
        update={"language": "en"})
    with pytest.raises(ValueError, match="language"):
        validate_mp3_input_lineage(store, inp)


def test_explicit_legacy_opt_in_is_recorded_everywhere(monkeypatch):
    store, parsed_ref, outline_ref = _fixture()
    activity, rendered = _activity(store, monkeypatch)
    result = activity.generate(_book_input(ref(LEGACY_TRANSLATION_STAGE, "e"), parsed_ref, outline_ref,
                                           allow_unverified_legacy=True))
    assert len(rendered) == 2
    assert result["unverified_legacy"] is True
    assert result["manifest"]["unverified_legacy"] is True
    assert store.s3.records and all(record["unverified_legacy"] is True for record in store.s3.records)
    for track in result["manifest"]["tracks"]:
        assert track["unverified_legacy"] is True
        record = json.loads(store.files[track["artifact_ref"]["key"]][VERIFICATION_FILE])
        assert record["unverified_legacy"] is True
        assert record["translation_source"] == LEGACY_TRANSLATION_STAGE
