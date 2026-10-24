from __future__ import annotations

import json
import pytest
from datetime import UTC, datetime

from b2c_contracts import ArtifactRef, Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook
from b2c_orchestration.audio import GenerateMP3Input, MP3Activities, dominant_script_language


class Store:
    bucket = "artifacts"

    def __init__(self, outline, qa, parsed_ref, parsed):
        self.payloads = {
            ("approve-outline", "approved-outline.json"): json.dumps({
                "outline": outline.model_dump(mode="json"),
                "spine_parsed_ref": parsed_ref.model_dump(mode="json"),
            }).encode(),
            ("generate-course-qa", "course-qa.json"): json.dumps(qa).encode(),
            ("ingest-parse-pdf", "parsed-book.json"): parsed.model_dump_json().encode(),
        }
        self.files = {}

    def read(self, ref, name):
        if ref.stage == "generate-mp3-track":
            return self.files[ref.key][name]
        return self.payloads[(ref.stage, name)]

    def get_or_compute(self, course, stage, key, compute):
        hit = key in self.files
        if not hit:
            self.files[key] = compute()
        result = ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/{course}/{stage}/{key}/",
                            sha256="f" * 64, created_at=datetime.now(UTC))
        return result, hit


def ref(stage, key, course="c"):
    return ArtifactRef(stage=stage, key=key * 64, uri=f"s3://artifacts/{course}/{stage}/{key * 64}/",
                       sha256=key * 64, created_at=datetime.now(UTC))


def test_course_mp3_is_split_as_lesson_then_lesson_qa_and_extra_scopes(monkeypatch):
    outline = CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[], requires_review=False,
        modules=[Module(module_id="m1", title="Unit One", order=1, lessons=[
            Lesson(lesson_id="m1.l1", title="First lesson", order=1,
                   spine_span=LessonSpan(start_block="b:1:0", end_block="b:1:0", pages=(1, 1)), provenance="headings")])])
    parsed_ref = ref("ingest-parse-pdf", "c")
    parsed = ParsedBook(book_id="b", title="Course book", role="spine", page_count=1, toc_source="headings",
        blocks=[Block(block_id="b:1:0", type="paragraph", text="Lesson content.",
                      refs=[PageRef(book_id="b", page_no=1)], section_path=["Unit One"], ocr=False)],
        parser={"name": "fixture", "version": "1", "options_hash": "d" * 64})
    outline_ref = ref("approve-outline", "b")
    qa = {"status": "final", "spine_parsed_sha256": parsed_ref.sha256,
          "approved_outline_sha256": outline_ref.sha256,
          "lesson_assets": [{"lesson_id": "m1.l1",
              "draft": {"sentences": [{"sid": "s1", "text_en": "Lesson content."}]},
              "summary": {"points": []}, "glossary": [],
              "questions": [{"stem": "MCQ?", "options": ["yes", "no"], "answer_index": 0, "rationale": "Evidence."}]}],
          "scopes": [{"scope_id": "m1.l1", "kind": "lesson", "items": [{"question": "Explain?", "answer": "Because."}]},
                     {"scope_id": "m1", "kind": "group", "scope_title": "Unit One", "items": [{"question": "Group?", "answer": "Group answer."}]},
                     {"scope_id": "c", "kind": "course", "scope_title": "Whole course", "items": [{"question": "Course?", "answer": "Course answer."}]}]}
    store = Store(outline, qa, parsed_ref, parsed)
    activity = MP3Activities(store)
    rendered = []
    monkeypatch.setattr(activity, "_render", lambda text, language, progress_callback=None: (rendered.append(text) or b"ID3" + b"\0" * 300))
    qa_ref = ref("generate-course-qa", "a")
    inp = GenerateMP3Input(course_id="c", source_artifact_ref=qa_ref.model_dump(mode="json"),
        source_kind="course", language="en", approved_outline_ref=outline_ref.model_dump(mode="json"),
        source_qa_artifact_ref=qa_ref.model_dump(mode="json"), source_book_ref=parsed_ref.model_dump(mode="json"))
    result = activity.generate(inp)
    tracks = result["manifest"]["tracks"]
    assert [x["kind"] for x in tracks] == ["lesson_source_text", "lesson_attachment", "group_questions", "course_questions"]
    assert tracks[0]["lesson_id"] == tracks[1]["lesson_id"] == "m1.l1"
    assert "m1.l1-source-text" in tracks[0]["filename"]
    assert all(x["artifact_ref"]["stage"] == "generate-mp3-track" for x in tracks)
    questions = store.files[tracks[1]["artifact_ref"]["key"]]["track.txt"].decode()
    assert "سؤال: Explain?\nالإجابة: Because." in questions
    activity.generate(inp)
    assert len(rendered) == 4

def test_partial_translation_cannot_be_used_for_audio():
    from b2c_orchestration.audio import validate_mp3_input_lineage

    qa_ref = ref("generate-course-qa", "a")
    translation_ref = ref("translate-content", "e")

    class PartialStore:
        bucket = "artifacts"

        def read(self, reference, name):
            if name == "parsed-book.json":
                return ParsedBook(book_id="b", title="Course book", role="spine", page_count=1,
                    toc_source="headings", blocks=[Block(block_id="b:1:0", type="paragraph",
                    text="Lesson content.", refs=[PageRef(book_id="b", page_no=1)],
                    section_path=["Unit One"], ocr=False)],
                    parser={"name": "fixture", "version": "1", "options_hash": "d" * 64}).model_dump_json().encode()
            if name == "translation.json":
                return json.dumps({"status": "partial", "total_segments": 2,
                    "missing_segment_ids": ["m1.l1:summary:2"],
                    "segments": [{"segment_id": "m1.l1:summary:1", "text": "Partial translation"}]}).encode()
            if name == "translation-source.json":
                return json.dumps({"source_artifact_ref": qa_ref.model_dump(mode="json")}).encode()
            raise AssertionError((reference.stage, name))

    inp = GenerateMP3Input(course_id="c", source_artifact_ref=translation_ref.model_dump(mode="json"),
        source_kind="course", language="ar", approved_outline_ref=ref("approve-outline", "b").model_dump(mode="json"),
        source_qa_artifact_ref=qa_ref.model_dump(mode="json"),
        source_book_ref=ref("ingest-parse-pdf", "c").model_dump(mode="json"))
    with pytest.raises(ValueError, match="incomplete"):
        validate_mp3_input_lineage(PartialStore(), inp)


def test_audio_language_gate_detects_short_english_that_heuristic_misses():
    assert dominant_script_language("STRUCTURED ANALYTIC TECHNIQUES") == "en"
    assert dominant_script_language("هذه فقرة عربية صالحة للقراءة الصوتية") == "ar"
    assert dominant_script_language("Mixed words كلمات مختلطة") is None



def test_english_course_audio_rejects_arabic_render_without_translation():
    from b2c_orchestration.audio import validate_mp3_input_lineage

    qa_ref = ref("generate-course-qa", "a")
    parsed_ref = ref("ingest-parse-pdf", "c")
    outline_ref = ref("approve-outline", "b")
    outline = CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[], requires_review=False,
        modules=[Module(module_id="m1", title="Unit", order=1, lessons=[Lesson(
            lesson_id="m1.l1", title="Lesson", order=1,
            spine_span=LessonSpan(start_block="b:1:0", end_block="b:1:0", pages=(1, 1)),
            provenance="headings")])])
    parsed = ParsedBook(book_id="b", title="Book", role="spine", page_count=1, toc_source="headings",
        blocks=[Block(block_id="b:1:0", type="paragraph", text="STRUCTURED ANALYTIC TECHNIQUES for practical use.",
            refs=[PageRef(book_id="b", page_no=1)], section_path=["Unit"], ocr=False)],
        parser={"name": "fixture", "version": "1", "options_hash": "d" * 64})
    qa = {"status": "final", "spine_parsed_sha256": parsed_ref.sha256,
          "approved_outline_sha256": outline_ref.sha256,
          "scopes": [{"scope_id": "m1.l1", "items": [{"question": "What is the method?", "answer": "It is a method."}]}]}
    store = Store(outline, qa, parsed_ref, parsed)
    store.payloads[("approve-outline", "approved-outline.json")] = json.dumps({
        "outline": outline.model_dump(mode="json"), "spine_parsed_ref": parsed_ref.model_dump(mode="json")
    }).encode()
    inp = GenerateMP3Input(course_id="c", source_artifact_ref=qa_ref.model_dump(mode="json"),
        source_kind="course", language="ar", approved_outline_ref=outline_ref.model_dump(mode="json"),
        source_qa_artifact_ref=qa_ref.model_dump(mode="json"), source_book_ref=parsed_ref.model_dump(mode="json"))
    with pytest.raises(ValueError, match="QA language differs"):
        validate_mp3_input_lineage(store, inp)


def test_book_audio_saves_each_selected_lesson_as_its_own_checkpoint(monkeypatch):
    from types import SimpleNamespace
    from b2c_orchestration import audio as audio_module

    outline = CourseOutline(course_id="c", spine_book_id="b", supplementary_book_ids=[], requires_review=False,
        modules=[
            Module(module_id="front", title="Front matter", order=1, lessons=[
                Lesson(lesson_id="front.title", title="Publisher title", order=1,
                       spine_span=LessonSpan(start_block="b:1:0", end_block="b:1:0", pages=(1, 1)), provenance="headings")]),
            Module(module_id="m1", title="Body", order=2, lessons=[
                Lesson(lesson_id="m1.l1", title="Lesson one", order=1,
                       spine_span=LessonSpan(start_block="b:2:0", end_block="b:2:0", pages=(2, 2)), provenance="headings"),
                Lesson(lesson_id="m1.l2", title="Lesson two", order=2,
                       spine_span=LessonSpan(start_block="b:3:0", end_block="b:3:0", pages=(3, 3)), provenance="headings")])])
    parsed_ref = ref("ingest-parse-pdf", "c")
    parsed = ParsedBook(book_id="b", title="Book", role="spine", page_count=3, toc_source="headings",
        blocks=[Block(block_id=f"b:{i}:0", type="paragraph", text=f"Original {i}.",
                      refs=[PageRef(book_id="b", page_no=i)], section_path=["Body"], ocr=False)
                for i in (1, 2, 3)],
        parser={"name": "fixture", "version": "1", "options_hash": "d" * 64})
    outline_ref = ref("approve-outline", "b")
    translated_ref = ref("translate-content", "e")
    store = Store(outline, {}, parsed_ref, parsed)
    store.payloads[("translate-content", "translation.json")] = json.dumps({
        "status": "completed", "source_language": "en", "target_language": "ar", "total_segments": 3,
        "segments": [{"segment_id": f"b:{i}:0", "text": f"\u0627\u0644\u062f\u0631\u0633 \u0627\u0644\u0639\u0631\u0628\u064a {i}"} for i in (1, 2, 3)],
        "missing_segment_ids": [], "failed_segment_ids": [],
    }).encode()
    store.payloads[("translate-content", "translation-source.json")] = json.dumps({
        "source_artifact_ref": parsed_ref.model_dump(mode="json")
    }).encode()

    class Progress:
        def __init__(self): self.records = []
        def put_object(self, **kwargs): self.records.append(json.loads(kwargs["Body"]))
        def get_object(self, **kwargs):
            # Match S3's absent-key contract, required by snapshot preservation.
            missing = RuntimeError("NoSuchKey")
            missing.response = {"ResponseMetadata": {"HTTPStatusCode": 404}}
            raise missing
    store.s3 = Progress()
    monkeypatch.setattr(audio_module.activity, "info", lambda: SimpleNamespace(workflow_id="b2c-mp3-test"))
    activity = MP3Activities(store)
    rendered = []
    monkeypatch.setattr(activity, "_render", lambda text, language, progress_callback=None: (rendered.append(text) or b"ID3" + b"\0" * 300))
    inp = GenerateMP3Input(course_id="c", source_artifact_ref=translated_ref.model_dump(mode="json"),
        source_kind="book", language="ar", approved_outline_ref=outline_ref.model_dump(mode="json"),
        source_book_ref=parsed_ref.model_dump(mode="json"), book_audio_layout="lessons",
        lesson_ids=["m1.l1", "m1.l2"])
    result = activity.generate(inp)
    assert rendered == ["\u0627\u0644\u062f\u0631\u0633 \u0627\u0644\u0639\u0631\u0628\u064a 2", "\u0627\u0644\u062f\u0631\u0633 \u0627\u0644\u0639\u0631\u0628\u064a 3"]
    assert [track["lesson_id"] for track in result["manifest"]["tracks"]] == ["m1.l1", "m1.l2"]
    assert [len(record["tracks"]) for record in store.s3.records] == [0, 1, 2]
    assert all(record["track_count"] == 2 for record in store.s3.records)
    assert all(track["artifact_ref"]["stage"] == "generate-mp3-track"
               for record in store.s3.records for track in record["tracks"])


def test_partial_qa_audio_preserves_order_and_marks_missing_attachments(monkeypatch):
    outline_ref, parsed_ref, qa_ref = ref("approve-outline", "b"), ref("ingest-parse-pdf", "c"), ref("generate-course-qa", "a")
    lessons = [Lesson(lesson_id=f"m.l{i}", title=f"Lesson {i}", order=i,
        spine_span=LessonSpan(start_block=f"b:{i}:0", end_block=f"b:{i}:0", pages=(i,i)), provenance="headings") for i in (1,2)]
    outline = CourseOutline(course_id="c",spine_book_id="b",supplementary_book_ids=[],requires_review=False,
        modules=[Module(module_id="m",title="Unit",order=1,lessons=lessons)])
    parsed=ParsedBook(book_id="b",title="Book",role="spine",page_count=2,toc_source="headings",
        blocks=[Block(block_id=f"b:{i}:0",type="paragraph",text=f"Original lesson {i} content.",refs=[PageRef(book_id="b",page_no=i)],section_path=["Unit"],ocr=False) for i in (1,2)],
        parser={"name":"fixture","version":"1","options_hash":"d"*64})
    qa={"status":"failed","approved_outline_sha256":outline_ref.sha256,"spine_parsed_sha256":parsed_ref.sha256,
        "scopes":[{"scope_id":"m.l1","kind":"lesson","status":"final","items":[{"question":"What?","answer":"Answer."}]},
                  {"scope_id":"m.l2","kind":"lesson","status":"failed","items":[{"question":"Partial?","answer":"Partial."}]}],
        "lesson_assets":[{"lesson_id":"m.l1","summary":{"points":["Do not render this summary"]}}]}
    store=Store(outline,qa,parsed_ref,parsed); renderer=MP3Activities(store); rendered=[]
    monkeypatch.setattr(renderer,"_render",lambda text,language,progress_callback=None:(rendered.append(text) or b"ID3"+b"0"*300))
    inp=GenerateMP3Input(course_id="c",source_kind="course",language="en",source_artifact_ref=qa_ref.model_dump(mode="json"),
        source_qa_artifact_ref=qa_ref.model_dump(mode="json"),approved_outline_ref=outline_ref.model_dump(mode="json"),source_book_ref=parsed_ref.model_dump(mode="json"),
        lesson_ids=["m.l1","m.l2"],allow_partial_qa=True,include_summaries=False,include_extra_scopes=False)
    result=renderer.generate(inp); manifest=result["manifest"]
    assert [t["track_id"] for t in manifest["tracks"]]==["m.l1-source-text","m.l1-attachment","m.l2-source-text"]
    assert manifest["attachment_coverage"]=={"ready_lesson_ids":["m.l1"],"pending_lesson_ids":["m.l2"]}
    assert manifest["qa_status"]=="failed"
    assert all("summary" not in text and "Partial?" not in text for text in rendered)
    renderer.generate(inp)
    assert len(rendered)==3
    inp.allow_partial_qa=False
    from b2c_orchestration.audio import validate_mp3_input_lineage
    with pytest.raises(ValueError,match="incomplete"):
        validate_mp3_input_lineage(store,inp)


def test_legacy_qa_requires_exact_migrated_source_record(monkeypatch):
    from types import SimpleNamespace
    import io
    from b2c_orchestration import audio
    parsed, outline, qa = ref("ingest-parse-pdf","c"),ref("approve-outline","b"),ref("generate-course-qa","a")
    identity={"external_book_id":"book-legacy","source_file_sha256":"e"*64,"source_mapping_id":"migration"}
    record={"migration_id":"migration","stages":{"qa":{"source_parsed_sha256":parsed.sha256,
        "source_file_sha256":identity["source_file_sha256"],"artifact_ref":qa.model_dump(mode="json"),"approved_outline_ref":outline.model_dump(mode="json")}}}
    monkeypatch.setattr(audio,"artifact_source_identity",lambda store, reference:identity)
    store=SimpleNamespace(bucket="artifacts",s3=SimpleNamespace(get_object=lambda **kwargs:{"Body":io.BytesIO(json.dumps(record).encode())}))
    assert audio.qa_source_matches(store,{},qa,outline,parsed,"c")
    record["stages"]["qa"]["source_parsed_sha256"]="0"*64
    assert not audio.qa_source_matches(store,{},qa,outline,parsed,"c")
