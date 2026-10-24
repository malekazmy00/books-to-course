from __future__ import annotations

import json
from io import BytesIO
from datetime import UTC, datetime

import pytest
from b2c_contracts import (
    ArtifactRef,
    Block,
    CourseOutline,
    Lesson,
    LessonSpan,
    Module,
    PageRef,
    ParsedBook,
    StructuredChunk,
)
from b2c_orchestration.qa import CourseQAActivities, GenerateCourseQAInput
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment


def _fixtures():
    parsed = ParsedBook(book_id="spine", title="Course", role="spine", page_count=2,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="b1", type="paragraph", text=("Probability measures the chance of an event. "
                "A trial can have a possible outcome, and probability is a numeric chance measure."),
                refs=[PageRef(book_id="spine", page_no=1)], section_path=["Chance"], ocr=False),
            Block(block_id="b2", type="paragraph", text=("Independent events do not change each other's probability. "
                "A trial can have a possible outcome, and probability is a numeric chance measure."),
                refs=[PageRef(book_id="spine", page_no=2)], section_path=["Independence"], ocr=False),
        ])
    outline = CourseOutline(course_id="course", spine_book_id="spine", supplementary_book_ids=[],
        requires_review=True, modules=[Module(module_id="unit", title="Core", order=1, lessons=[
            Lesson(lesson_id="unit.l1", title="Chance", order=1,
                spine_span=LessonSpan(start_block="b1", end_block="b1", pages=(1, 1)),
                provenance="llm_inferred"),
            Lesson(lesson_id="unit.l2", title="Independence", order=2,
                spine_span=LessonSpan(start_block="b2", end_block="b2", pages=(2, 2)),
                provenance="llm_inferred")])])
    parsed_ref = ArtifactRef(stage="ingest-parse-pdf", key="a" * 64,
        uri="s3://artifacts/course/ingest-parse-pdf/" + "a" * 64 + "/",
        sha256="b" * 64, created_at=datetime.now(UTC))
    approved_ref = ArtifactRef(stage="approve-outline", key="c" * 64,
        uri="s3://artifacts/course/approve-outline/" + "c" * 64 + "/",
        sha256="d" * 64, created_at=datetime.now(UTC))
    envelope = {"outline": outline.model_dump(mode="json"), "reviewer_id": "reviewer",
        "approval_provenance": {"review_type": "human", "reviewer_id": "reviewer"},
        "approved_at": "2026-10-04T00:00:00Z",
        "spine_parsed_ref": parsed_ref.model_dump(mode="json"), "supplementary_parsed_refs": {}}
    chunks = [
        StructuredChunk(chunk_id="c1", book_id="spine", role="spine", section_path=["Chance"],
            block_ids=["b1"], pages=[1], text=parsed.blocks[0].text, token_count=7, embedding_model="fixture"),
        StructuredChunk(chunk_id="c2", book_id="spine", role="spine", section_path=["Independence"],
            block_ids=["b2"], pages=[2], text=parsed.blocks[1].text, token_count=8, embedding_model="fixture"),
    ]
    return approved_ref, parsed_ref, envelope, parsed, chunks


class FakeS3:
    def __init__(self):
        self.objects = {}
    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body
    def get_object(self, *, Bucket, Key):
        return {"Body": BytesIO(self.objects[Key])}
    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        truncated = start + 2 < len(keys)
        return {"Contents": [{"Key": key} for key in page], "IsTruncated": truncated,
                "NextContinuationToken": str(start + 2) if truncated else None}


class FakeStore:
    def __init__(self, approved_ref, parsed_ref, envelope, parsed):
        self.s3 = FakeS3()
        self.bucket = "artifacts"
        self.files = {(approved_ref.uri, "approved-outline.json"): json.dumps(envelope).encode(),
                      (parsed_ref.uri, "parsed-book.json"): parsed.model_dump_json().encode()}
        self.outputs = {}
    def read(self, ref, filename):
        if ref.uri.startswith("s3://artifacts/course/generate-course-qa/"):
            return self.outputs[(ref.uri, filename)]
        return self.files[(ref.uri, filename)]
    def get_or_compute(self, course_id, stage, key, compute):
        return self.put(course_id, stage, key, compute()), False
    def put(self, course_id, stage, key, files):
        ref = ArtifactRef(stage=stage, key=key,
            uri=f"s3://artifacts/{course_id}/{stage}/{key}/",
            sha256="e" * 64, created_at=datetime.now(UTC))
        self.outputs[(ref.uri, "course-qa.json")] = files["course-qa.json"]
        return ref


class FakeChunkStore:
    def __init__(self, chunks): self.chunks = chunks
    def list_chunks(self, course_id, *, book_ids=None, roles=None):
        assert course_id == "course"
        return self.chunks


class FakeGateway:
    model_id = "fixture-model"
    def __init__(self):
        self.max_tokens_seen = []
        self.last_correct_indices = []
    def structured(self, messages, schema, *, prompt_version, temperature=0, max_tokens=None):
        self.max_tokens_seen.append(max_tokens)
        data = json.loads(messages[1]["content"])
        if schema.__name__ == "RepairLessonQuestion":
            rejected = data["rejected_proposal"]
            target = rejected["answer_text"]
            sentences = data["lesson_sentences"]
            sid = next(item["index"] for item in sentences if target.split()[-1].casefold() in item["text"].casefold())
            return schema(stem="Which source relation applies to this changed condition?", bloom="apply",
                answer_text=target, rationale="Apply the relation stated in the cited source to this condition.",
                sentence_indices=[sid])
        if schema.__name__ == "ExactQuestions":
            source = data["source_chunks"][0]
            text = source["text"]
            if "Independent events" in text:
                answers = ["independent events", "change probability", "possible outcome",
                           "numeric chance measure", "outcome of a trial"]
            else:
                answers = ["chance of event", "probability measures", "possible outcome",
                           "numeric chance measure", "outcome of a trial"]
            sentences = [part.strip() for part in text.split(". ") if part.strip()]
            if len(sentences) < 3:
                sentences.append(sentences[0])
            questions = []
            for i in range(data["question_count"]):
                answer = answers[i % len(answers)]
                answer_tokens = [token for token in answer.casefold().split() if token not in {"a", "an", "the", "of"}]
                sentence_index = next((j for j, sentence in enumerate(sentences)
                    if all(token in sentence.casefold() for token in answer_tokens)), 0)
                questions.append({"stem": f"Which source-specific relation applies in case {i}?",
                    "bloom": "apply", "answer_text": answer,
                    "rationale": f"Apply the cited relation to case {i}; the source states {answer}.",
                    "sentence_indices": [sentence_index]})
            return schema(
                sentences=[{"text_en": sentence, "citations": [source["chunk_id"]], "kind": "primary"}
                           for sentence in sentences],
                summary_points=[{"text": f"Summary fact {i} from the lesson.", "sentence_indices": [0]}
                                for i in range(3)],
                glossary=[], questions=questions,
            )
        if schema.__name__ == "ExactLessonEvaluation":
            source_text = data["source_chunks"][0]["text"]
            expected = (["independent events", "change probability", "possible outcome",
                         "numeric chance measure", "outcome of a trial"]
                        if "Independent events" in source_text else
                        ["chance of event", "probability measures", "possible outcome",
                         "numeric chance measure", "outcome of a trial"])
            choices = []
            for item in data["mcqs"]:
                case = int(item["stem"].rsplit(" ", 1)[1].rstrip("?"))
                target = expected[case % len(expected)]
                choices.append(next(i for i, option in enumerate(item["options"]) if option == target))
            self.last_correct_indices = choices
            return schema(support=[{"index": i, "supported_by_source": True}
                                    for i, _ in enumerate(data["assets"])],
                          with_lesson_choices=choices)
        if schema.__name__ == "ExactBlindSolver":
            return schema(choices=[(choice + 1) % len(item["options"])
                                   for choice, item in zip(self.last_correct_indices, data["mcqs"], strict=True)])
        if schema.__name__ == "_CandidateSet":
            source = data["source_chunks"][0]
            return schema(items=[{"question": f"Why does {data['scope_id']} matter?",
                "answer": source["text"], "evidence_chunk_id": source["chunk_id"],
                "evidence_quote": source["text"]} for _ in range(data["requested_count"])])
        return schema(judgments=[{"item_index": i, "answer_supported": True,
            "question_clear_and_answerable": True, "relevant_to_scope": True,
            "rationale": "The cited source supports this answer."} for i, _ in enumerate(data)])


def test_phase5_activity_generates_and_persists_approved_course_questions():
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    gateway = FakeGateway()
    activity = CourseQAActivities(store, FakeChunkStore(chunks), gateway)
    result = ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
        course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
        index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
        lesson_question_count=1, group_question_count=1, course_question_count=1, max_tokens=1234))
    assert gateway.max_tokens_seen and set(gateway.max_tokens_seen) == {1234}
    assert result["status"] == "final"
    assert result["covered_lesson_count"] == 2
    persisted = json.loads(store.outputs[(result["artifact"]["uri"], "course-qa.json")])
    assert persisted["status"] == "final"
    assert all(scope["status"] == "final" for scope in persisted["scopes"])


def test_phase5_activity_skips_enrichment_when_option_is_off(monkeypatch):
    import b2c_orchestration.qa as qa
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    monkeypatch.setattr(qa, "generate_lesson_enrichments",
        lambda *args, **kwargs: pytest.fail("enrichment must be skipped when not selected"))
    activity = CourseQAActivities(store, FakeChunkStore(chunks), FakeGateway())
    result = ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
        course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
        index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        include_lesson_enrichment=False))
    artifact = json.loads(store.outputs[(result["artifact"]["uri"], "course-qa.json")])
    assert result["status"] == "final"
    assert artifact["lesson_assets"] == []


def test_phase5_activity_rejects_unindexed_approved_spine():
    approved_ref, parsed_ref, envelope, parsed, _ = _fixtures()
    activity = CourseQAActivities(FakeStore(approved_ref, parsed_ref, envelope, parsed),
        FakeChunkStore([]), FakeGateway())
    with pytest.raises(ApplicationError, match="invalid or incomplete"):
        ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
            course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
        lesson_question_count=1, group_question_count=1, course_question_count=1))


def test_phase5_activity_accepts_valid_system_structural_approval():
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    outline_data = envelope["outline"]
    outline_data["requires_review"] = False
    for module in outline_data["modules"]:
        for lesson in module["lessons"]:
            lesson["provenance"] = "headings"
    envelope["outline"] = outline_data
    envelope["reviewer_id"] = None
    envelope["approval_provenance"] = {
        "review_type": "system",
        "system_id": "b2c-outline-structural-review",
        "policy_version": "1.0.0",
        "evidence_sha256": "f" * 64,
    }
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    activity = CourseQAActivities(store, FakeChunkStore(chunks), FakeGateway())
    result = ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
        course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
        index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
        lesson_question_count=1, group_question_count=1, course_question_count=1))
    assert result["status"] == "final"
    assert result["covered_lesson_count"] == 2


def test_course_qa_retrieval_keeps_primary_and_supplementary_source_attribution():
    from b2c_indexing import QueryRetrievalService

    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    supplement = ParsedBook(book_id="extra", title="Supplement", role="supplementary",
        page_count=1, toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="s1", type="paragraph",
                text="Chance examples explain probability with an independent supplementary view.",
                refs=[PageRef(book_id="extra", page_no=7)], section_path=["Chance"], ocr=False),
        ])
    supplement_ref = ArtifactRef(stage="ingest-parse-pdf", key="f" * 64,
        uri="s3://artifacts/course/ingest-parse-pdf/" + "f" * 64 + "/",
        sha256="a" * 64, created_at=datetime.now(UTC))
    outline = CourseOutline.model_validate(envelope["outline"]).model_copy(
        update={"supplementary_book_ids": ["extra"]})
    envelope["outline"] = outline.model_dump(mode="json")
    envelope["supplementary_parsed_refs"] = {
        "extra": supplement_ref.model_dump(mode="json")}
    chunks = [*chunks, StructuredChunk(chunk_id="extra:s1", book_id="extra",
        role="supplementary", section_path=["Chance"], block_ids=["s1"], pages=[7],
        text=supplement.blocks[0].text, token_count=11, embedding_model="fixture")]
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    store.files[(supplement_ref.uri, "parsed-book.json")] = supplement.model_dump_json().encode()

    class TwoSourceGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.course_roles = []

        def structured(self, messages, schema, *, prompt_version, temperature=0, max_tokens=None):
            if schema.__name__ not in {"_CandidateSet"}:
                return super().structured(messages, schema, prompt_version=prompt_version,
                                           temperature=temperature, max_tokens=max_tokens)
            self.max_tokens_seen.append(max_tokens)
            data = json.loads(messages[1]["content"])
            if schema.__name__ == "_CandidateSet":
                sources = data["source_chunks"]
                if data["scope_kind"] == "course":
                    self.course_roles = [item["role"] for item in sources]
                    chosen = [next(item for item in sources if item["role"] == "spine"),
                              next(item for item in sources if item["role"] == "supplementary")]
                    count = data["requested_count"]
                    return schema(items=[{
                        "question": f"What fact does source {index} add for {data['scope_id']}?",
                        "answer": chosen[index % len(chosen)]["text"],
                        "evidence_chunk_id": chosen[index % len(chosen)]["chunk_id"],
                        "evidence_quote": chosen[index % len(chosen)]["text"],
                    } for index in range(count)])
                source = sources[0]
                return schema(items=[{
                    "question": f"What fact does {data['scope_id']} explain?",
                    "answer": source["text"], "evidence_chunk_id": source["chunk_id"],
                    "evidence_quote": source["text"],
                } for _ in range(data["requested_count"])])
            return schema(judgments=[{"item_index": i, "answer_supported": True,
                "question_clear_and_answerable": True, "relevant_to_scope": True,
                "rationale": "The cited source supports this answer."} for i, _ in enumerate(data)])

    gateway = TwoSourceGateway()
    retrieval = QueryRetrievalService(FakeChunkStore(chunks),
        embed_query=lambda _: [1.0, 0.0], embed_passage=lambda _: [1.0, 0.0])
    activity = CourseQAActivities(store, FakeChunkStore(chunks), gateway,
                                  retrieval_service=retrieval)
    result = ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
        course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
        index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {
                "spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]},
                "extra": {"role": "supplementary", "chunk_count": 1, "chunk_ids": ["extra:s1"]},
            }},
        lesson_question_count=1, group_question_count=1, course_question_count=3,
        retrieval_mode="hybrid", retrieval_top_k=3))
    persisted = json.loads(store.outputs[(result["artifact"]["uri"], "course-qa.json")])
    course_scope = next(scope for scope in persisted["scopes"] if scope["kind"] == "course")
    assert set(gateway.course_roles) == {"spine", "supplementary"}
    items = course_scope["items"]
    assert {item["role"] for item in items} == {"spine", "supplementary"}
    assert all(item["book_id"] == ("spine" if item["role"] == "spine" else "extra")
               for item in items)
    assert all(item["pages"] == ([1] if item["role"] == "spine" else [7]) for item in items)


def test_checkpoint_loader_paginates_skips_enrichment_and_requires_explicit_switch():
    from b2c_orchestration.qa import _load_qa_checkpoints
    from b2c_pedagogy.qa_generation import ScopeResult, generate_course_qa
    from tests.unit.pedagogy.test_qa_generation import fixture_sources, FakeGateway as QAGateway
    outline, book, chunks = fixture_sources()
    scope = generate_course_qa(outline, book, chunks, QAGateway(), lesson_question_count=1,
        lesson_ids=["lesson-1"]).scopes[0]
    class Store:
        bucket = "artifacts"
        s3 = FakeS3()
    store = Store()
    identity = {"approved_outline_sha256": "outline", "indexed_sources_sha256": "sources",
        "index_result_sha256": "index", "model_id": "old", "qa_prompt_version": "v4",
        "config": {"model_id": "old", "max_tokens": 4096, "reasoning_effort": "none", "cache_bust": None, "retrieval_mode": "lexical"}}
    current = {**identity, "model_id": "new", "config": {**identity["config"], "model_id": "new", "max_tokens": 8192}}
    current["config"].pop("reasoning_effort")
    for key, payload in [
        ("a/enrichment/lesson.json", {"identity": identity, "enrichment": {}}),
        ("a/lesson.json", {"identity": identity, "scope_result": scope.model_dump()}),
        ("b/lesson.json", {"identity": identity, "scope_result": scope.model_copy(update={"status": "failed"}).model_dump()}),
        ("c/other.json", {"identity": {**identity, "indexed_sources_sha256": "different"}, "scope_result": scope.model_dump()})]:
        store.s3.put_object(Bucket=store.bucket, Key="_system/qa-checkpoints/workflow/" + key,
            Body=json.dumps(payload).encode())
    assert _load_qa_checkpoints(store, "workflow", current) == []
    restored = _load_qa_checkpoints(store, "workflow", current, allow_model_switch=True)
    assert restored == [scope]
    lineage = _load_qa_checkpoints(store, ["older", "workflow"], current, allow_model_switch=True)
    assert lineage == [scope]
    assert _load_qa_checkpoints(store, "workflow", {**current, "config": {**current["config"], "cache_bust": "forced"}}, allow_model_switch=True) == []
    assert _load_qa_checkpoints(store, "workflow", {**current, "config": {**current["config"], "retrieval_mode": "hybrid"}}, allow_model_switch=True) == []


def test_failed_qa_persists_partial_without_enrichment_or_completed_progress(monkeypatch):
    import b2c_orchestration.qa as qa
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    class BadGateway(FakeGateway):
        def structured(self, messages, schema, **kwargs):
            if schema.__name__ == "_CandidateSet":
                data = json.loads(messages[1]["content"])
                return schema(items=[{"question": "Which fact is supported here?", "answer": "wrong answer",
                    "evidence_chunk_id": "unknown", "evidence_quote": "wrong evidence quote"}
                    for _ in range(data["requested_count"])])
            raise AssertionError("Failed QA must not enrich")
    monkeypatch.setattr(qa, "generate_lesson_enrichments", lambda *args, **kwargs: pytest.fail("enrichment on failed QA"))
    result = ActivityEnvironment().run(CourseQAActivities(store, FakeChunkStore(chunks), BadGateway()).generate,
        GenerateCourseQAInput(course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
                "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
            lesson_question_count=1, group_question_count=1, course_question_count=1))
    assert result["status"] == "failed"
    progress = [json.loads(body) for key, body in store.s3.objects.items() if key.startswith("_system/qa-progress/")][-1]
    assert progress["status"] == "FAILED"
    assert progress["completed_scopes"] == 0
    assert progress["failed_scopes"] == progress["processed_scopes"]
    checkpoints = [json.loads(body) for key, body in store.s3.objects.items()
                   if key.startswith("_system/qa-checkpoints/")]
    assert len(checkpoints) == progress["processed_scopes"]
    assert all(payload["scope_result"]["status"] == "failed" and
               payload["scope_result"]["items"] == [] and
               payload["scope_result"]["errors"] and
               payload["scope_result"]["attempts"] > 0 and
               payload["identity"]["approved_outline_sha256"] == approved_ref.sha256 and
               payload["scope_result"]["lesson_ids"] for payload in checkpoints)


def test_pause_request_honors_failed_scope_boundary(monkeypatch):
    import b2c_orchestration.qa as qa
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    store.s3.put_object(Bucket=store.bucket, Key="_system/qa-controls/test.json",
        Body=json.dumps({"action": "pause_after_checkpoint"}).encode())

    def fail_one_scope(*args, progress_callback, **kwargs):
        progress_callback({"phase": "questions", "status": "running",
            "completed_scopes": 0, "failed_scopes": 0, "processed_scopes": 0})
        progress_callback({"phase": "questions", "status": "running",
            "completed_scopes": 0, "failed_scopes": 1, "processed_scopes": 1})
        raise AssertionError("the pause control must interrupt at the failed-scope boundary")

    monkeypatch.setattr(qa, "generate_course_qa", fail_one_scope)
    result = ActivityEnvironment().run(CourseQAActivities(
        store, FakeChunkStore(chunks), FakeGateway()).generate,
        GenerateCourseQAInput(course_id="course",
            approved_outline_ref=approved_ref.model_dump(mode="json"),
            index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
                "books": {"spine": {"role": "spine", "chunk_count": 2,
                    "chunk_ids": ["c1", "c2"]}}},
            lesson_question_count=1, group_question_count=1, course_question_count=1))
    assert result["status"] == "paused"
    artifact = json.loads(store.outputs[(result["artifact"]["uri"], "course-qa.json")])
    assert artifact["status"] == "paused"



@pytest.mark.asyncio
async def test_qa_workflow_uses_bounded_retry_and_long_external_timeout(monkeypatch):
    import b2c_orchestration.qa as qa
    captured = {}
    async def execute(*args, **kwargs):
        captured.update(kwargs)
        return {"status": "failed"}
    monkeypatch.setattr(qa.workflow, "execute_activity", execute)
    inp = GenerateCourseQAInput(course_id="course", approved_outline_ref={}, index_result={})
    await qa.GenerateCourseQAWorkflow().run(inp)
    assert captured["retry_policy"].maximum_attempts == 3
    assert captured["start_to_close_timeout"].total_seconds() == 24 * 3600
    assert captured["schedule_to_close_timeout"].total_seconds() == 48 * 3600



def test_pause_request_returns_partial_artifact_after_first_saved_scope():
    approved_ref, parsed_ref, envelope, parsed, chunks = _fixtures()
    store = FakeStore(approved_ref, parsed_ref, envelope, parsed)
    store.s3.put_object(Bucket=store.bucket, Key="_system/qa-controls/test.json",
        Body=json.dumps({"action": "pause_after_checkpoint"}).encode())
    activity = CourseQAActivities(store, FakeChunkStore(chunks), FakeGateway())
    result = ActivityEnvironment().run(activity.generate, GenerateCourseQAInput(
        course_id="course", approved_outline_ref=approved_ref.model_dump(mode="json"),
        index_result={"course_id": "course", "outline_sha256": approved_ref.sha256,
            "books": {"spine": {"role": "spine", "chunk_count": 2, "chunk_ids": ["c1", "c2"]}}},
        lesson_question_count=1, group_question_count=1, course_question_count=1))
    assert result["status"] == "paused"
    assert result["resume_context"]["checkpoint_source_workflow_ids"] == ["test"]
    artifact = json.loads(store.outputs[(result["artifact"]["uri"], "course-qa.json")])
    assert artifact["status"] == "paused"
    assert len(artifact["scopes"]) == 1
    assert artifact["lesson_assets"] == []


def test_checkpoint_loader_retains_empty_failure_diagnostics_across_sources():
    from b2c_orchestration.qa import _load_qa_checkpoints
    from b2c_pedagogy.qa_generation import ScopeResult, generate_course_qa
    from tests.unit.pedagogy.test_qa_generation import fixture_sources, FakeGateway as QAGateway
    outline, book, chunks = fixture_sources()
    final = generate_course_qa(outline, book, chunks, QAGateway(), lesson_question_count=1,
        lesson_ids=["lesson-1"]).scopes[0]
    empty = ScopeResult(scope_id="lesson-2", kind="lesson", lesson_ids=["lesson-2"],
        status="failed", items=[], errors=["LLMUnavailable: ReadTimeout"], attempts=3)
    store = type("Store", (), {"bucket": "artifacts", "s3": FakeS3()})()
    identity = {"source": "immutable"}
    for source, name, scope in [("old", "saved", final), ("new", "failed", empty),
                               ("new", "retry", final.model_copy(update={"status": "failed", "items": []}))]:
        store.s3.put_object(Bucket=store.bucket, Key=f"_system/qa-checkpoints/{source}/fingerprint/{name}.json",
            Body=json.dumps({"identity": identity, "scope_result": scope.model_dump()}).encode())
    restored = {scope.scope_id: scope for scope in _load_qa_checkpoints(store, ["old", "new"], identity)}
    assert restored["lesson-1"] == final
    assert restored["lesson-2"] == empty
    retried = generate_course_qa(outline, book, chunks, QAGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, precompleted_scopes=list(restored.values()))
    assert retried.status == "final"
    assert retried.scopes[0].items == final.items
