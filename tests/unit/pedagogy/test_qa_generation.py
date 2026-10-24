from __future__ import annotations

import json

import httpx
import pytest
from b2c_contracts import (
    Block,
    CourseOutline,
    Lesson,
    LessonSpan,
    Module,
    PageRef,
    ParsedBook,
    StructuredChunk,
)
from b2c_llm_gateway import LLMGateway
from b2c_pedagogy.qa_generation import (
    _derive_scopes,
    _allocate_batch_counts,
    _is_title_meta_question,
    _quote_is_declarative,
    _quote_matches_source,
    _quote_options,
    GroundedQA,
    ScopeResult,
    generate_course_qa,
    persist_course_qa,
)


def fixture_sources():
    book = ParsedBook(book_id="primary", title="Probability", role="spine", page_count=2,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="p:1:1", type="paragraph", text="Probability measures the chance of an event.",
                refs=[PageRef(book_id="primary", page_no=1)], section_path=["Probability"], ocr=False),
            Block(block_id="p:2:1", type="paragraph", text="Independent events do not change each other's probability.",
                refs=[PageRef(book_id="primary", page_no=2)], section_path=["Independence"], ocr=False),
        ])
    outline = CourseOutline(course_id="course-1", spine_book_id="primary",
        supplementary_book_ids=["supp"], requires_review=False, modules=[
            Module(module_id="unit-1", title="Probability", order=1, lessons=[
                Lesson(lesson_id="lesson-1", title="Chance", order=1,
                    spine_span=LessonSpan(start_block="p:1:1", end_block="p:1:1", pages=(1, 1)),
                    provenance="headings"),
                Lesson(lesson_id="lesson-2", title="Independence", order=2,
                    spine_span=LessonSpan(start_block="p:2:1", end_block="p:2:1", pages=(2, 2)),
                    provenance="headings"),
            ])])
    chunks = [
        StructuredChunk(chunk_id="c1", book_id="primary", role="spine", section_path=["Probability", "Chance"],
            block_ids=["p:1:1"], pages=[1], text=book.blocks[0].text, token_count=7, embedding_model="test"),
        StructuredChunk(chunk_id="c2", book_id="primary", role="spine", section_path=["Independence"],
            block_ids=["p:2:1"], pages=[2], text=book.blocks[1].text, token_count=8, embedding_model="test"),
        StructuredChunk(chunk_id="s1", book_id="supp", role="supplementary", section_path=["Support"],
            block_ids=["supp:7:1"], pages=[7], text="A supplementary text gives a second explanation of probability.",
            token_count=11, embedding_model="test"),
    ]
    return outline, book, chunks


class FakeGateway:
    model_id = "mock-model"

    def __init__(self, *, bad_first=False, always_bad=False):
        self.bad_first = bad_first
        self.always_bad = always_bad
        self.generation_calls = 0
        self.input_chunk_batches = []

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        if schema.__name__ == "_CandidateSet":
            self.generation_calls += 1
            payload = json.loads(messages[1]["content"])
            self.input_chunk_batches.append([c["chunk_id"] for c in payload["source_chunks"]])
            chunk = payload["source_chunks"][0]
            bad = self.always_bad or (self.bad_first and self.generation_calls == 1)
            return schema(items=[{
                "question": f"In scope {payload['scope_id']}, what statement does source {chunk['chunk_id']} make?",
                "answer": "Probability is the chance that an event occurs.",
                "evidence_chunk_id": chunk["chunk_id"],
                "evidence_quote": "not present in source" if bad else chunk["text"],
            } for _ in range(payload["requested_count"])])
        assert schema.__name__ == "_JudgeSet"
        payload = json.loads(messages[1]["content"])
        return schema(judgments=[{
            "item_index": item["index"], "answer_supported": True,
            "question_clear_and_answerable": True, "relevant_to_scope": True,
            "rationale": "Source quote directly supports it.",
        } for item in payload])



def test_quote_options_are_exact_declarative_source_sentences():
    source = "Tiny. Probability measures the chance of an event. What is the probability?"
    options = _quote_options(source)
    assert options == ["Probability measures the chance of an event."]
    assert all(_quote_matches_source(option, source) for option in options)




@pytest.mark.parametrize("quote", [
    "Which outcome is most likely?",
    "Define multinomial outcomes",
    "4. Calculate the probability of the event.",
    "Suppose a die is rolled six times.",
])
def test_exercise_prompts_are_not_answer_evidence(quote):
    assert not _quote_is_declarative(quote)


def test_lesson_scopes_use_spine_until_supplementary_mapping_exists():
    outline, book, chunks = fixture_sources()
    scopes, _ = _derive_scopes(outline, book, chunks,
        lesson_question_count=1, group_question_count=1, course_question_count=1)
    lesson_scopes = [scope for scope in scopes if scope.kind == "lesson"]
    assert lesson_scopes
    assert all(all(chunk.role == "spine" for chunk in scope.chunks) for scope in lesson_scopes)
    course_scope = next(scope for scope in scopes if scope.kind == "course")
    assert any(chunk.role == "supplementary" for chunk in course_scope.chunks)


def test_optional_review_assessments_are_generated_from_contiguous_lesson_scopes():
    outline, book, chunks = fixture_sources()
    scopes, lesson_ids = _derive_scopes(outline, book, chunks,
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        include_review_assessments=True, assessment_question_count=5)
    exams = [scope for scope in scopes if scope.kind == "assessment"]
    assert [scope.scope_id for scope in exams] == [
        "assessment:midterm-1", "assessment:term-1", "assessment:midterm-2",
        "assessment:term-2", "assessment:final"]
    assert [scope.scope_title for scope in exams] == [
        "Midterm 1", "Term 1", "Midterm 2", "Term 2", "Final"]
    assert exams[0].lesson_ids == exams[1].lesson_ids == [lesson_ids[0]]
    assert exams[2].lesson_ids == exams[3].lesson_ids == [lesson_ids[1]]
    assert exams[-1].lesson_ids == lesson_ids
    assert all(scope.question_count == 5 for scope in exams)
    assert all(all(chunk.role == "spine" for chunk in scope.chunks) for scope in exams)


def test_review_assessments_are_absent_by_default():
    outline, book, chunks = fixture_sources()
    scopes, _ = _derive_scopes(outline, book, chunks,
        lesson_question_count=1, group_question_count=1, course_question_count=1)
    assert not any(scope.kind == "assessment" for scope in scopes)


def test_review_assessments_generate_source_cited_final_scopes():
    outline, book, chunks = fixture_sources()
    result = generate_course_qa(outline, book, chunks, FakeGateway(),
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        include_review_assessments=True, assessment_question_count=1)
    assessments = [scope for scope in result.scopes if scope.kind == "assessment"]
    assert result.status == "final"
    assert len(assessments) == 5
    assert all(scope.status == "final" and len(scope.items) == 1 for scope in assessments)
    assert all(item.evidence_chunk_ids and item.automated_checks["exact_source_quote"]
               for scope in assessments for item in scope.items)


def test_generates_all_outline_scopes_with_exact_page_and_role_provenance(tmp_path):
    outline, book, chunks = fixture_sources()
    gateway = FakeGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1)
    assert result.status == "final"
    assert result.covered_lesson_ids == ["lesson-1", "lesson-2"]
    assert [(scope.kind, scope.scope_id) for scope in result.scopes] == [
        ("lesson", "lesson-1"), ("lesson", "lesson-2"), ("group", "unit-1"), ("course", "course-1")]
    lesson1, lesson2 = result.scopes[:2]
    assert lesson1.items[0].pages == [1] and lesson1.items[0].role == "spine"
    assert lesson2.items[0].pages == [2] and lesson2.items[0].role == "spine"
    assert result.scopes[2].items == [lesson1.items[0]]
    assert result.scopes[3].items == [lesson1.items[0]]
    assert result.scopes[2].attempts == result.scopes[3].attempts == 0
    assert all(item.status == "final" and item.automated_checks["exact_source_quote"]
               for scope in result.scopes for item in scope.items)
    destination = tmp_path / "course-qa.json"
    persist_course_qa(result, destination)
    assert json.loads(destination.read_text())["status"] == "final"



def test_group_and_course_do_not_inherit_from_an_incomplete_lesson_bank():
    outline, book, chunks = fixture_sources()

    class OneLessonFailureGateway(FakeGateway):
        def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
            if schema.__name__ == "_CandidateSet":
                payload = json.loads(messages[1]["content"])
                if payload["scope_id"] == "lesson-2":
                    chunk = payload["source_chunks"][0]
                    return schema(items=[{
                        "question": "For lesson-2, what statement does the source make?",
                        "answer": "A supported answer.",
                        "evidence_chunk_id": chunk["chunk_id"],
                        "evidence_quote": "not present in source",
                    } for _ in range(payload["requested_count"])])
            return super().structured(messages, schema, prompt_version=prompt_version,
                temperature=temperature, max_tokens=max_tokens)

    result = generate_course_qa(outline, book, chunks, OneLessonFailureGateway(),
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        max_regeneration_rounds=0)
    assert result.status == "failed"
    lesson1, lesson2, group, course = result.scopes
    assert lesson1.status == "final"
    assert lesson2.status == "failed"
    # Parent scopes must generate from their own complete source scope instead of inheriting
    # a misleadingly complete-looking bank from only the successful child lesson.
    assert group.status == course.status == "final"
    assert "unit-1" in group.items[0].question
    assert "course-1" in course.items[0].question
    assert group.items[0].qa_id != lesson1.items[0].qa_id
    assert course.items[0].qa_id != lesson1.items[0].qa_id


def test_precompleted_lesson_scopes_are_reused_and_parent_scopes_inherit():
    outline, book, chunks = fixture_sources()
    first_gateway = FakeGateway()
    first = generate_course_qa(outline, book, chunks, first_gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"])
    assert first.status == "final"
    second_gateway = FakeGateway()
    combined = generate_course_qa(outline, book, chunks, second_gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, precompleted_scopes=[first.scopes[0]])
    assert combined.status == "final"
    assert [(scope.kind, scope.scope_id) for scope in combined.scopes] == [
        ("lesson", "lesson-1"), ("lesson", "lesson-2"), ("group", "unit-1"), ("course", "course-1")]
    assert combined.scopes[0].items == first.scopes[0].items
    assert second_gateway.generation_calls == 1
    assert combined.scopes[2].items == [combined.scopes[0].items[0]]
    assert combined.scopes[3].items == [combined.scopes[0].items[0]]






def test_precompleted_supplementary_item_reuses_only_after_relevance_checks():
    outline, book, chunks = fixture_sources()
    source = next(chunk for chunk in chunks if chunk.role == "supplementary")
    item = GroundedQA(
        qa_id="qa:supplementary-seed", question="What does the second text provide?",
        answer="A second explanation of probability.", evidence_chunk_ids=[source.chunk_id],
        book_id=source.book_id, role=source.role, pages=source.pages,
        evidence_quote="A supplementary text gives a second explanation of probability.",
        model_id="mock-model", prompt_version="phase5-grounded-qa-v4",
        automated_checks={
            "exact_chunk_id": True, "exact_source_quote": True, "answer_supported": True,
            "question_clear_and_answerable": True, "relevant_to_scope": True, "deduplicated": True,
        },
    )
    seed = ScopeResult(scope_id="lesson-1", kind="lesson", lesson_ids=["lesson-1"],
        status="final", items=[item])
    gateway = FakeGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"],
        precompleted_scopes=[seed])
    assert result.scopes[0].status == "final"
    assert result.scopes[0].items == [item]
    assert gateway.generation_calls == 0


def test_failed_scope_preserves_automatically_validated_partial_items():
    outline, book, chunks = fixture_sources()

    class OneRejectedByJudge(FakeGateway):
        def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
            payload = json.loads(messages[1]["content"])
            if schema.__name__ == "_CandidateSet":
                self.generation_calls += 1
                chunk = payload["source_chunks"][0]
                return schema(items=[{
                    "question": f"Which statement does source {chunk['chunk_id']} make, option {index}?",
                    "answer": "Probability measures the chance of an event.",
                    "evidence_chunk_id": chunk["chunk_id"],
                    "evidence_quote": "Probability measures the chance of an event.",
                } for index in range(payload["requested_count"])])
            return schema(judgments=[{
                "item_index": item["index"], "answer_supported": item["index"] == 0,
                "question_clear_and_answerable": True, "relevant_to_scope": True,
                "rationale": "Deterministic fixture judgment.",
            } for item in payload])

    result = generate_course_qa(outline, book, chunks, OneRejectedByJudge(),
        lesson_question_count=2, group_question_count=1, course_question_count=1,
        lesson_ids=["lesson-1"], max_regeneration_rounds=0)
    lesson = result.scopes[0]
    assert lesson.status == "failed"
    assert len(lesson.items) == 1
    assert lesson.items[0].automated_checks["exact_source_quote"] is True


def test_partial_precompleted_lesson_scope_reuses_validated_items():
    outline, book, chunks = fixture_sources()
    seed = generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"])
    seeded_item = seed.scopes[0].items[0]
    class UniqueQuestionGateway(FakeGateway):
        def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
            if schema.__name__ == "_CandidateSet":
                self.generation_calls += 1
                payload = json.loads(messages[1]["content"])
                chunk = payload["source_chunks"][0]
                return schema(items=[{
                    "question": f"For item {self.generation_calls}, what statement does source {chunk['chunk_id']} make?",
                    "answer": "Probability is the chance that an event occurs.",
                    "evidence_chunk_id": chunk["chunk_id"],
                    "evidence_quote": chunk["text"],
                } for _ in range(payload["requested_count"])])
            payload = json.loads(messages[1]["content"])
            return schema(judgments=[{
                "item_index": item["index"], "answer_supported": True,
                "question_clear_and_answerable": True, "relevant_to_scope": True,
                "rationale": "Source quote directly supports it.",
            } for item in payload])

    gateway = UniqueQuestionGateway()
    completed = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=2,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"],
        precompleted_scopes=[seed.scopes[0]])
    lesson = completed.scopes[0]
    assert lesson.status == "final"
    assert len(lesson.items) == 2
    assert lesson.items[0].question == seeded_item.question
    assert gateway.generation_calls == 1


def test_precompleted_scopes_reject_wrong_model_or_prompt():
    outline, book, chunks = fixture_sources()
    first = generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"])
    wrong = first.scopes[0].model_copy(update={"items":[
        first.scopes[0].items[0].model_copy(update={"model_id":"other-model"})
    ]})
    with pytest.raises(ValueError, match="model or prompt version"):
        generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
            group_question_count=1, course_question_count=1, precompleted_scopes=[wrong])


def test_scope_checkpoints_resume_without_regenerating_questions():
    outline, book, chunks = fixture_sources()
    saved = []
    first_gateway = FakeGateway()
    first = generate_course_qa(
        outline, book, chunks, first_gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"],
        scope_checkpoint_callback=saved.append,
    )
    assert first.status == "final"
    assert [item.scope_id for item in saved] == ["lesson-1"]
    assert first_gateway.generation_calls == 1

    resume_gateway = FakeGateway()
    resumed = generate_course_qa(
        outline, book, chunks, resume_gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"],
        precompleted_scopes=saved,
    )
    assert resumed.status == "final"
    assert resumed.scopes[0].items == first.scopes[0].items
    assert resume_gateway.generation_calls == 0

def test_course_scope_excludes_spine_chunks_outside_outline_spans():
    outline, book, chunks = fixture_sources()
    outside = Block(block_id="p:3:1", type="paragraph",
        text="An unrelated later chapter discusses regression.",
        refs=[PageRef(book_id="primary", page_no=3)], section_path=["Regression"], ocr=False)
    expanded_book = book.model_copy(update={"page_count": 3, "blocks":[*book.blocks, outside]})
    outside_chunk = StructuredChunk(chunk_id="c3", book_id="primary", role="spine",
        section_path=["Regression"], block_ids=["p:3:1"], pages=[3],
        text=outside.text, token_count=6, embedding_model="test")
    scopes, _ = _derive_scopes(outline, expanded_book, [*chunks, outside_chunk],
        lesson_question_count=1, group_question_count=1, course_question_count=1)
    course_scope = scopes[-1]
    assert course_scope.kind == "course"
    assert {chunk.chunk_id for chunk in course_scope.chunks} == {"c1", "c2", "s1"}
    assert "c3" not in {chunk.chunk_id for chunk in course_scope.chunks}


def test_retries_invalid_evidence_and_marks_final_only_after_checks_pass():
    outline, book, chunks = fixture_sources()
    gateway = FakeGateway(bad_first=True)
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, max_regeneration_rounds=1)
    assert result.status == "final"
    assert gateway.generation_calls == 3  # one retry, then the second lesson; parent scopes reuse child banks


def test_unresolved_generation_failure_is_machine_readable_and_not_final():
    outline, book, chunks = fixture_sources()
    result = generate_course_qa(outline, book, chunks, FakeGateway(always_bad=True),
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        max_regeneration_rounds=1)
    assert result.status == "failed"
    assert result.scopes[0].status == "failed" and result.scopes[0].items == []
    assert "not an exact source substring" in " ".join(result.scopes[0].errors)


def test_chunk_crossing_outline_lesson_boundary_fails_closed():
    outline, book, chunks = fixture_sources()
    crossing = chunks[0].model_copy(update={"block_ids": ["p:1:1", "p:2:1"]})
    with pytest.raises(ValueError, match="crosses accepted lesson boundaries"):
        generate_course_qa(outline, book, [crossing, chunks[1]], FakeGateway())


def test_unapproved_outline_cannot_be_used_for_final_course_qa():
    outline, book, chunks = fixture_sources()
    pending = outline.model_copy(update={"requires_review": True})
    with pytest.raises(ValueError, match="not approved"):
        generate_course_qa(pending, book, chunks, FakeGateway())


def test_bounds_each_provider_request_to_chunk_batches():
    outline, book, chunks = fixture_sources()
    # Every source goes through bounded batches; each resulting Q/A retains its source pages.
    gateway = FakeGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, max_input_chars=400)
    assert result.status == "final"
    assert all(len(batch) <= 1 for batch in gateway.input_chunk_batches)


def test_runs_through_real_gateway_structured_api_with_mock_http_provider():
    outline, book, chunks = fixture_sources()

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        schema_name = body["response_format"]["json_schema"]["name"]
        prompt = json.loads(body["messages"][1]["content"])
        if schema_name == "_CandidateSet":
            source = prompt["source_chunks"][0]
            response = {"items": [{
                "question": f"For scope {prompt['scope_id']}, what statement does {source['chunk_id']} make?",
                "answer": "Probability measures the chance of an event.",
                "evidence_chunk_id": source["chunk_id"], "evidence_quote": source["text"],
            } for _ in range(prompt["requested_count"])]}
        else:
            response = {"judgments": [{"item_index": item["index"], "answer_supported": True,
                "question_clear_and_answerable": True, "relevant_to_scope": True,
                "rationale": "The source supports this answer."}
                for item in prompt]}
        return httpx.Response(200, json={"model": "mock-local", "choices": [{
            "message": {"content": json.dumps(response)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    gateway = LLMGateway("http://llm:8080/v1", "mock-local", transport=httpx.MockTransport(respond))
    try:
        result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
            group_question_count=1, course_question_count=1)
    finally:
        gateway._http.close()
    assert result.status == "final"
    assert len(result.scopes) == 4
    assert all(scope.status == "final" for scope in result.scopes)


def test_evidence_quote_accepts_only_canonical_unicode_and_whitespace_equivalence():
    source = "The coeﬃcient\nwas 0.5; the value is fixed."
    assert _quote_matches_source("The coefficient was 0.5; the value is fixed.", source)
    assert not _quote_matches_source("The coefficient was 0.6; the value is fixed.", source)


def test_evidence_quote_rejects_unanswered_exercise_prompts():
    assert not _quote_is_declarative("What is the probability that the sum is six?")
    assert not _quote_is_declarative("Find the probability from the data.")
    assert _quote_is_declarative("The probability is five out of thirty-six.")



def test_human_approval_envelope_can_unlock_inferred_outline_without_mutating_its_provenance():
    outline, book, chunks = fixture_sources()
    inferred = outline.model_copy(update={"requires_review": True, "modules": [
        module.model_copy(update={"lessons": [
            lesson.model_copy(update={"provenance": "llm_inferred"}) for lesson in module.lessons
        ]}) for module in outline.modules
    ]})
    result = generate_course_qa(inferred, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, outline_approved=True)
    assert result.status == "final"
    assert inferred.requires_review is True
    with pytest.raises(ValueError, match="not approved"):
        generate_course_qa(inferred, book, chunks, FakeGateway(), lesson_question_count=1,
            group_question_count=1, course_question_count=1)


def test_lesson_ids_run_only_selected_lesson_scope():
    outline, book, chunks = fixture_sources()
    gateway = FakeGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-2"])
    assert result.status == "final"
    assert result.expected_lesson_ids == ["lesson-2"]
    assert result.covered_lesson_ids == ["lesson-2"]
    assert [(scope.kind, scope.scope_id) for scope in result.scopes] == [("lesson", "lesson-2")]


def test_lesson_ids_reject_unknown_approved_lesson():
    outline, book, chunks = fixture_sources()
    with pytest.raises(ValueError, match="unknown approved lessons"):
        generate_course_qa(outline, book, chunks, FakeGateway(), lesson_ids=["made-up"])



def test_inception_requests_use_tighter_payload_batches():
    from b2c_llm_gateway import LLMRequestRejected
    outline, book, chunks = fixture_sources()
    chunks.extend([
        StructuredChunk(chunk_id="s2", book_id="supp", role="supplementary", section_path=["Support"],
            block_ids=["supp:8:1"], pages=[8], text="A" * 8500, token_count=1800, embedding_model="test"),
        StructuredChunk(chunk_id="s3", book_id="supp", role="supplementary", section_path=["Support"],
            block_ids=["supp:9:1"], pages=[9], text="B" * 8500, token_count=1800, embedding_model="test"),
    ])
    class CaptureGateway:
        model_id = "mercury-2"
        provider_name = "inceptionlabs"
        def __init__(self): self.user_lengths = []
        def structured(self, messages, schema, **kwargs):
            self.user_lengths.append(len(messages[1]["content"]))
            raise LLMRequestRejected(400, code="invalid_request_error")
    gateway = CaptureGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"],
        max_regeneration_rounds=0, max_input_chars=18_000, max_tokens=1024)
    assert result.status == "failed"
    assert gateway.user_lengths and max(gateway.user_lengths) <= 13_000

def test_provider_http_400_fails_scope_without_regeneration_retries():
    from b2c_llm_gateway import LLMRequestRejected
    outline, book, chunks = fixture_sources()
    class RejectedGateway:
        model_id = "mercury-2"
        usage_totals = {}
        def __init__(self):
            self.calls = 0
        def structured(self, *args, **kwargs):
            self.calls += 1
            raise LLMRequestRejected(400, code="invalid_schema")
    gateway = RejectedGateway()
    result = generate_course_qa(outline, book, chunks, gateway,
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        lesson_ids=["lesson-1"])
    assert result.status == "failed"
    assert gateway.calls == 1
    assert result.scopes[0].attempts == 1
    assert "invalid_schema" in result.scopes[0].errors[0]


def test_sparse_question_allocation_starts_with_primary_source_batch():
    assert _allocate_batch_counts(40, 1) == [(0, 1)]
    assert _allocate_batch_counts(40, 5) == [(0, 1), (8, 1), (16, 1), (24, 1), (32, 1)]


def test_resume_explicitly_trusts_old_model_provenance_without_rewriting_it():
    outline, book, chunks = fixture_sources()
    first = generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1)
    gateway = FakeGateway()
    gateway.model_id = "new-model"
    with pytest.raises(ValueError, match="model or prompt"):
        generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
            group_question_count=1, course_question_count=1, precompleted_scopes=first.scopes)
    resumed = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, precompleted_scopes=list(reversed(first.scopes)),
        accepted_precompleted_model_ids={"mock-model"})
    assert resumed.status == "final"
    assert gateway.generation_calls == 0
    assert all(item.model_id == "mock-model" for scope in resumed.scopes for item in scope.items)


def test_partial_batch_checkpoint_survives_interruption_and_resumes():
    from b2c_pedagogy.qa_generation import ScopeCheckpointError
    outline, book, chunks = fixture_sources()
    extra = chunks[0].model_copy(update={"chunk_id": "c1-extra"})
    chunks = [*chunks, extra]
    saved = []
    def checkpoint(result):
        saved.append(result)
        raise ScopeCheckpointError("simulated activity interruption")
    with pytest.raises(ScopeCheckpointError):
        generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=2,
            lesson_ids=["lesson-1"], max_input_chars=300, scope_checkpoint_callback=checkpoint)
    assert len(saved) == 1 and len(saved[0].items) == 1 and saved[0].status == "failed"
    class RemainingGateway(FakeGateway):
        def structured(self, messages, schema, **kwargs):
            if schema.__name__ == "_CandidateSet":
                data = json.loads(messages[1]["content"])
                data["scope_id"] += "-resumed"
                messages = [messages[0], {"role": "user", "content": json.dumps(data)}]
            return super().structured(messages, schema, **kwargs)
    resumed = generate_course_qa(outline, book, chunks, RemainingGateway(), lesson_question_count=2,
        lesson_ids=["lesson-1"], max_input_chars=300, precompleted_scopes=saved)
    assert resumed.status == "final"
    assert resumed.scopes[0].items[0] == saved[0].items[0]
    assert len(resumed.scopes[0].items) == 2


def test_second_group_inherits_its_own_lessons_when_other_lessons_already_final():
    outline, book, chunks = fixture_sources()
    first, second = outline.modules[0].lessons
    outline = outline.model_copy(update={"modules": [
        Module(module_id="group-1", title="Chance", order=1, lessons=[first]),
        Module(module_id="group-2", title="Independence", order=2, lessons=[second]),
    ]})
    gateway = FakeGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1)
    assert result.status == "final"
    assert gateway.generation_calls == 2
    second_group = next(scope for scope in result.scopes if scope.scope_id == "group-2")
    second_lesson = next(scope for scope in result.scopes if scope.scope_id == "lesson-2")
    assert second_group.attempts == 0 and second_group.items == second_lesson.items


def test_questions_target_topics_and_reject_title_recall():
    assert _is_title_meta_question("What is the title of this chapter?")
    assert _is_title_meta_question("ما عنوان هذا الدرس؟")
    assert _is_title_meta_question("بماذا يسمى هذا الفصل؟")
    assert not _is_title_meta_question("How does the method reduce analytical bias?")
    assert not _is_title_meta_question("كيف يقلل الأسلوب من التحيز في التحليل؟")


def test_generation_rejects_title_recall_candidates_before_judging():
    outline, book, chunks = fixture_sources()

    class TitleQuestionGateway(FakeGateway):
        def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
            if schema.__name__ == "_CandidateSet":
                payload = json.loads(messages[1]["content"])
                chunk = payload["source_chunks"][0]
                return schema(items=[{
                    "question": "What is the title of this chapter?",
                    "answer": "Probability measures the chance of an event.",
                    "evidence_chunk_id": chunk["chunk_id"],
                    "evidence_quote": "Probability measures the chance of an event.",
                } for _ in range(payload["requested_count"])])
            return super().structured(messages, schema, prompt_version=prompt_version,
                                      temperature=temperature, max_tokens=max_tokens)

    result = generate_course_qa(outline, book, chunks, TitleQuestionGateway(),
        lesson_question_count=1, group_question_count=1, course_question_count=1,
        max_regeneration_rounds=0)
    assert result.status == "failed"
    assert any("title/name/heading" in error for scope in result.scopes for error in scope.errors)


def test_avoid_prompt_is_bounded_and_prioritizes_current_scope():
    from b2c_pedagogy.qa_generation import _prompt_avoid_questions
    outline, book, chunks = fixture_sources()
    seed = generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"])
    item = seed.scopes[0].items[0]
    bank = seed.scopes[0].model_copy(update={"items": [item.model_copy(update={
        "question": f"Question {i} " + "concept " * 20}) for i in range(1000)]})
    scope = _derive_scopes(outline, book, chunks, lesson_question_count=1,
        group_question_count=1, course_question_count=1)[0][0]
    prompt = _prompt_avoid_questions(scope, [bank], {"current question"})
    assert "current question" in prompt
    assert len(prompt) <= 24
    assert len(json.dumps(sorted(prompt), ensure_ascii=False)) <= 3000
    unrelated = bank.model_copy(update={"lesson_ids": ["lesson-2"]})
    assert _prompt_avoid_questions(scope, [unrelated], set()) == set()


def test_omitted_unrelated_question_still_rejected_globally():
    outline, book, chunks = fixture_sources()
    seed = generate_course_qa(outline, book, chunks, FakeGateway(), lesson_question_count=1,
        group_question_count=1, course_question_count=1, lesson_ids=["lesson-1"])
    repeated = seed.scopes[0].items[0].question
    class DuplicateAcrossLessons(FakeGateway):
        def __init__(self):
            super().__init__()
            self.payloads = []
        def structured(self, messages, schema, **kwargs):
            if schema.__name__ == "_CandidateSet":
                payload = json.loads(messages[1]["content"])
                self.payloads.append(payload)
                chunk = payload["source_chunks"][0]
                return schema(items=[{"question": repeated, "answer": "A source-supported answer.",
                    "evidence_chunk_id": chunk["chunk_id"], "evidence_quote": chunk["text"]}])
            return super().structured(messages, schema, **kwargs)
    gateway = DuplicateAcrossLessons()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, max_regeneration_rounds=0,
        precompleted_scopes=seed.scopes)
    second = next(scope for scope in result.scopes if scope.scope_id == "lesson-2")
    assert second.status == "failed" and second.items == []
    assert any("duplicate question" in error for error in second.errors)
    prompts = [payload for payload in gateway.payloads if payload["scope_id"] == "lesson-2"]
    assert prompts and all(payload["questions_to_avoid"] == [] for payload in prompts)
    assert result.scopes[0].items == seed.scopes[0].items


def test_empty_scope_failure_checkpoint_precedes_pause_boundary():
    from b2c_pedagogy.qa_generation import ScopeCheckpointError
    outline, book, chunks = fixture_sources()
    persisted = []
    def boundary(progress):
        if progress.get("processed_scopes") == 1:
            assert len(persisted) == 1
            assert persisted[0].items == [] and persisted[0].errors
            raise ScopeCheckpointError("pause after durable boundary")
    with pytest.raises(ScopeCheckpointError, match="pause after durable boundary"):
        generate_course_qa(outline, book, chunks, FakeGateway(always_bad=True), lesson_question_count=1,
            group_question_count=1, course_question_count=1, max_regeneration_rounds=0,
            scope_checkpoint_callback=persisted.append, progress_callback=boundary)
    assert persisted[0].scope_id == "lesson-1" and persisted[0].attempts == 1


def test_failure_checkpoint_write_error_is_not_swallowed():
    from b2c_pedagogy.qa_generation import ScopeCheckpointError
    outline, book, chunks = fixture_sources()
    def fail_write(scope):
        assert scope.status == "failed" and scope.items == []
        raise ScopeCheckpointError("durable write failed")
    with pytest.raises(ScopeCheckpointError, match="durable write failed"):
        generate_course_qa(outline, book, chunks, FakeGateway(always_bad=True), lesson_question_count=1,
            group_question_count=1, course_question_count=1, max_regeneration_rounds=0,
            scope_checkpoint_callback=fail_write)


@pytest.mark.parametrize(("candidates", "caller", "expected"), [
    (1,4096,2048), (2,4096,2048), (3,4096,2560), (5,4096,4096),
    (20,4096,4096), (1,1200,1200), (20,20000,15616),
])
def test_judge_budget_retains_headroom_and_never_exceeds_caller(candidates, caller, expected):
    from b2c_pedagogy.qa_generation import _judge_output_budget
    assert _judge_output_budget(candidates, caller) == expected


def test_generation_and_judge_callers_use_separate_output_budgets():
    outline, book, chunks = fixture_sources()
    class BudgetGateway(FakeGateway):
        def __init__(self):
            super().__init__()
            self.budgets = []
        def structured(self, messages, schema, **kwargs):
            self.budgets.append((schema.__name__, kwargs["max_tokens"]))
            result = super().structured(messages, schema, **kwargs)
            if schema.__name__ == "_JudgeSet":
                # Long permitted rationale remains schema-valid; no quality flag is removed.
                return schema(judgments=[item.model_copy(update={"rationale": "x" * 500})
                                         for item in result.judgments])
            return result
    gateway = BudgetGateway()
    result = generate_course_qa(outline, book, chunks, gateway, lesson_question_count=1,
        group_question_count=1, course_question_count=1, max_tokens=4096)
    assert result.status == "final"
    assert [budget for schema, budget in gateway.budgets if schema == "_CandidateSet"] == [4096,4096]
    assert [budget for schema, budget in gateway.budgets if schema == "_JudgeSet"] == [2048,2048]
    assert all(item.automated_checks["answer_supported"] for scope in result.scopes for item in scope.items)
