from __future__ import annotations

import json

import pytest
from b2c_contracts import (Block, CourseOutline, Lesson, LessonDraft, LessonSentence, LessonSpan, Module,
                           PageRef, ParsedBook, Question, StructuredChunk)
from b2c_pedagogy.lesson_enrichment import generate_lesson_enrichments


def _sources():
    book = ParsedBook(book_id="book", title="Probability", role="spine", page_count=1,
        toc_source="headings", parser={"name": "fixture", "version": "1"}, blocks=[
            Block(block_id="b1", type="paragraph", text="Probability measures the chance of an event. Event count records occurrences; sample size counts observations; outcome label names a possible result.",
                  refs=[PageRef(book_id="book", page_no=1)], section_path=["Probability"], ocr=False),
        ])
    outline = CourseOutline(course_id="course", spine_book_id="book", supplementary_book_ids=[],
        requires_review=False, modules=[Module(module_id="m1", title="Probability", order=1, lessons=[
            Lesson(lesson_id="l1", title="Chance", order=1,
                   spine_span=LessonSpan(start_block="b1", end_block="b1", pages=(1, 1)), provenance="headings"),
        ])])
    chunks = [StructuredChunk(chunk_id="c1", book_id="book", role="spine", section_path=["Probability"],
        block_ids=["b1"], pages=[1], text="Probability measures the chance of an event. Event count records occurrences; sample size counts observations; outcome label names a possible result.", token_count=22,
        embedding_model="test")]
    return outline, book, chunks


class _Gateway:
    model_id = "fixture"

    def __init__(self, *, unsupported=False, with_lesson_wrong=False):
        self.unsupported = unsupported
        self.with_lesson_wrong = with_lesson_wrong
        self.calls = []

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        self.calls.append((schema.__name__, prompt_version, messages, schema))
        payload = json.loads(messages[1]["content"])
        if schema.__name__ == "ExactQuestions":
            return schema(
                sentences=[
                    {"text_en": "Probability measures the chance of an event.", "citations": ["c1"], "kind": "primary"},
                    {"text_en": "Event count records occurrences.", "citations": ["c1"], "kind": "primary"},
                    {"text_en": "Sample size counts observations.", "citations": ["c1"], "kind": "primary"},
                    {"text_en": "Outcome label names a possible result.", "citations": ["c1"], "kind": "primary"},
                ],
                summary_points=[{"text": f"Probability describes event chance, point {i}.",
                                 "sentence_indices": [i - 1]} for i in range(1, 4)],
                glossary=[{"term_en": "Probability", "definition_en": "A measure of event chance.",
                           "sentence_index": 0}],
                questions=[
                {"stem": "Which phrase describes probability's likelihood?", "bloom": "apply",
                 "answer_text": "chance of an event", "rationale": "Probability is stated as chance of an event.",
                 "sentence_indices": [0]},
                {"stem": "What does the event count record?", "bloom": "apply",
                 "answer_text": "records occurrences", "rationale": "The sentence says event count records occurrences.",
                 "sentence_indices": [1]},
                {"stem": "What does the sample size measure?", "bloom": "analyze",
                 "answer_text": "counts observations", "rationale": "The sentence states sample size counts observations.",
                 "sentence_indices": [2]},
            ],
            )
        if schema.__name__ == "ExactLessonEvaluation":
            items = payload["assets"]
            return schema(
                support=[{"index": i, "supported_by_source": not self.unsupported}
                         for i in range(len(items))],
                with_lesson_choices=[next(i for i, option in enumerate(item["options"])
                    if option == ("chance of an event" if "probability" in item["stem"].lower() else
                                  "records occurrences" if "event count" in item["stem"].lower() else
                                  "counts observations")) if not self.with_lesson_wrong else 0
                    for item in payload["mcqs"]],
            )
        assert schema.__name__ == "ExactBlindSolver"
        # A low blind score demonstrates that the evaluation is context dependent.
        return schema(choices=[(next(i for i, option in enumerate(item["options"])
            if option == ("chance of an event" if "probability" in item["stem"].lower() else
                          "records occurrences" if "event count" in item["stem"].lower() else
                          "counts observations")) + 1) % len(item["options"])
            for item in payload["mcqs"]])


def test_option_label_normalization_preserves_plain_text():
    from b2c_pedagogy.lesson_enrichment import _normalize_option_label

    assert _normalize_option_label("A. Answer text") == "Answer text"
    assert _normalize_option_label("  3) Answer text") == "Answer text"
    assert _normalize_option_label("Answer text") == "Answer text"


def test_enrichment_generates_cited_assets_and_separates_blind_solver_call():
    outline, book, chunks = _sources()
    gateway = _Gateway()
    [lesson] = generate_lesson_enrichments(outline, book, chunks, gateway, question_count=3)

    assert lesson.status == "final"
    assert len(lesson.draft.sentences) == 4
    assert len(lesson.summary.points) == 3
    assert len(lesson.glossary) == 1
    assert len(lesson.questions) == 3
    assert sum(question.bloom in {"apply", "analyze"} for question in lesson.questions) == 3
    assert lesson.questions[0].options[lesson.questions[0].answer_index] == "chance of an event"
    assert all(q.option_evidence_sids and len(q.option_evidence_sids) == len(q.options) for q in lesson.questions)
    assert "options" not in gateway.calls[0][3].model_json_schema()["$defs"]["ScopedMCQProposal"]["properties"]
    assert lesson.automated_evaluation["with_lesson_solver_accuracy"] == 1.0
    assert lesson.automated_evaluation["without_lesson_solver_accuracy"] == 0.0
    assert [call[0] for call in gateway.calls] == ["ExactQuestions", "ExactLessonEvaluation", "ExactBlindSolver"]
    generation_schema = gateway.calls[0][3].model_json_schema()
    def find_enums(value):
        if isinstance(value, dict):
            if value.get("enum") == ["c1"] or value.get("const") == "c1":
                yield value.get("enum", [value.get("const")])
            for child in value.values():
                yield from find_enums(child)
        elif isinstance(value, list):
            for child in value:
                yield from find_enums(child)

    assert list(find_enums(generation_schema))
    evaluation_payload = json.loads(gateway.calls[1][2][1]["content"])
    assert "answer_indices_for_scoring" not in evaluation_payload
    blind_payload = json.loads(gateway.calls[-1][2][1]["content"])
    assert "source_chunks" not in blind_payload
    assert "answer_indices_for_scoring" not in blind_payload


@pytest.mark.parametrize("gateway, message", [
    (_Gateway(unsupported=True), "source support"),
    (_Gateway(with_lesson_wrong=True), "solver gate"),
])
def test_enrichment_fails_closed_when_automated_gate_fails(gateway, message):
    outline, book, chunks = _sources()
    with pytest.raises(ValueError, match=message):
        generate_lesson_enrichments(outline, book, chunks, gateway, question_count=3)


def test_enrichment_repairs_ungrounded_answer_proposal_before_building_choices():
    outline, book, chunks = _sources()

    class RepairGateway:
        model_id = "fixture"

        def __init__(self):
            self.delegate = _Gateway()
            self.generation_count = 0
            self.repair_count = 0
            self.repair_question = None

        @property
        def calls(self):
            return self.delegate.calls

        def structured(self, messages, schema, **kwargs):
            if schema.__name__ == "RepairLessonQuestion":
                self.repair_count += 1
                return schema(**self.repair_question)
            result = self.delegate.structured(messages, schema, **kwargs)
            if schema.__name__ == "ExactQuestions":
                self.generation_count += 1
                if self.generation_count == 1:
                    data = result.model_dump()
                    self.repair_question = dict(data["questions"][0])
                    data["questions"][0]["answer_text"] = "an unmentioned hook"
                    return schema(**data)
            return result

    gateway = RepairGateway()
    [lesson] = generate_lesson_enrichments(outline, book, chunks, gateway, question_count=3)
    assert lesson.status == "final"
    assert gateway.generation_count == 1
    assert gateway.repair_count == 1


def test_enrichment_rejects_citation_outside_approved_scope():
    outline, book, chunks = _sources()

    class BadCitationGateway(_Gateway):
        def structured(self, messages, schema, **kwargs):
            result = super().structured(messages, schema, **kwargs)
            if schema.__name__ == "ExactQuestions":
                data = result.model_dump()
                data["sentences"][0]["citations"] = ["unknown"]
                return schema(**data)
            return result

    # The dynamic Literal schema rejects bad citations before the semantic scope guard.
    with pytest.raises(Exception):
        generate_lesson_enrichments(outline, book, chunks, BadCitationGateway(), question_count=3)



def test_t_syn_04_records_an_explicit_spine_supplement_negation_conflict():
    outline, book, chunks = _sources()
    supplement_id = "supplement-book"
    outline = outline.model_copy(update={"supplementary_book_ids": [supplement_id]})
    conflict_text = "The event is independent. Probability is the chance measure of an event. Event count records occurrences; sample size counts observations; outcome label names a possible result."
    primary = chunks[0].model_copy(update={"text": conflict_text})
    conflict = StructuredChunk(
        chunk_id="supp:conflict", book_id=supplement_id, role="supplementary",
        section_path=["Probability"], block_ids=["sb1"], pages=[9],
        text=conflict_text.replace("is independent", "is not independent"), token_count=24,
        embedding_model="test",
    )

    [lesson] = generate_lesson_enrichments(
        outline, book, [primary, conflict], _Gateway(), question_count=3,
        supplementary_chunks_by_lesson={"l1": [conflict]},
    )

    assert len(lesson.draft.contradictions) == 1
    item = lesson.draft.contradictions[0]
    assert item.kind == "contradiction"
    assert (item.chunk_id, item.book_id, item.pages, item.anchor_block_id) == (
        "supp:conflict", supplement_id, [9], "b1"
    )
    # Retrieved supplementary content is used for the conflict check only; lesson facts stay spine-grounded.
    assert all(sentence.citations == ["c1"] for sentence in lesson.draft.sentences)


def test_t_syn_04_rejects_unapproved_supplemental_conflict_candidates():
    outline, book, chunks = _sources()
    conflict = StructuredChunk(
        chunk_id="supp:conflict", book_id="other", role="supplementary",
        section_path=["Probability"], block_ids=["sb1"], pages=[9],
        text="Probability does not measure the chance of an event.", token_count=9,
        embedding_model="test",
    )

    with pytest.raises(ValueError, match="unapproved supplementary"):
        generate_lesson_enrichments(
            outline, book, chunks, _Gateway(), question_count=3,
            supplementary_chunks_by_lesson={"l1": [conflict]},
        )


def test_t_syn_05_unsupported_topic_judgment_fails_closed():
    outline, book, chunks = _sources()

    with pytest.raises(ValueError, match="source support"):
        generate_lesson_enrichments(
            outline, book, chunks, _Gateway(unsupported=True), question_count=3,
        )


def test_numeric_question_rejects_code_premise_missing_from_cited_sentence():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en="pmf.Total() returns 0.9 after the preceding operations.",
        citations=["c1"], kind="primary")], faithfulness={})
    question = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="apply", stem="What is pmf.Total() after pmf.Incr(2, 0.2)?",
        options=["0.9", "1.0", "0.6", "0.4"], answer_index=0, rationale="The total is 0.9.",
        evidence_sids=["l1:s1"], validation={})
    with pytest.raises(ValueError, match="code premise.*absent"):
        _validate_mcq_evidence_consistency(draft, [question])


def test_numeric_question_rejects_answer_index_that_disagrees_with_rationale():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en="pmf.Normalize() makes pmf.Total() equal 1.0.",
        citations=["c1"], kind="primary")], faithfulness={})
    question = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="apply", stem="What is pmf.Total() after pmf.Normalize()?",
        options=["1.0", "0.9", "0", "0.8"], answer_index=2, rationale="Normalize makes the total 1.0.",
        evidence_sids=["l1:s1"], validation={})
    with pytest.raises(ValueError, match="selected numeric answer disagrees"):
        _validate_mcq_evidence_consistency(draft, [question])


def test_numeric_question_passes_when_code_premise_and_answer_are_supported():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en="pmf.Prob(2) returns 0.4 for the example; comparison values are 0.2, 0.6, and 0.8.",
        citations=["c1"], kind="primary")], faithfulness={})
    question = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="apply", stem="What is pmf.Prob(2)?",
        options=["0.4", "0.2", "0.6", "0.8"], answer_index=0, rationale="The example returns 0.4.",
        evidence_sids=["l1:s1"], validation={})
    _validate_mcq_evidence_consistency(draft, [question])


def test_mcq_detail_linter_rejects_generic_answers_and_unanchored_distractors():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en="The loom's reed has 12 dents per inch, while the warp beam stores the prepared warp.",
        citations=["c1"], kind="primary")], faithfulness={})
    generic = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="remember", stem="What does the lesson describe?",
        options=["A weaving technique", "The reed has 12 dents", "A general method", "An important tool"],
        answer_index=0, rationale="The lesson describes a weaving technique.",
        evidence_sids=["l1:s1"], validation={})
    with pytest.raises(ValueError, match="correct answer lacks a source-specific detail anchor"):
        _validate_mcq_evidence_consistency(draft, [generic])


def test_mcq_detail_linter_accepts_exact_fact_with_source_anchored_distractors():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en="The loom's reed has 12 dents per inch; the warp beam stores the prepared warp and the treadle lifts selected threads.",
        citations=["c1"], kind="primary")], faithfulness={})
    question = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="remember", stem="How many dents per inch does the reed have?",
        options=["12 dents", "prepared warp", "selected threads", "loom's reed"],
        answer_index=0, rationale="The source specifies 12 dents per inch.",
        evidence_sids=["l1:s1"], validation={})
    checks = _validate_mcq_evidence_consistency(draft, [question])
    assert checks == [{"source_detail_anchor_pass": True, "distractors_anchored_pass": True}]


def test_mcq_detail_linter_rejects_unsupported_word_even_when_two_tokens_match():
    from b2c_pedagogy.lesson_enrichment import _source_detail_supported

    assert not _source_detail_supported("secure binder cane", "The binder cane is attached to the frame.")


def test_single_role_citation_deterministically_corrects_model_lane_label():
    from b2c_pedagogy.lesson_enrichment import _AssetsCandidate, _make_lesson_assets

    outline, book, chunks = _sources()
    sentence = {"text_en": chunks[0].text, "citations": ["c1"], "kind": "supplementary"}
    candidate = _AssetsCandidate.model_validate({
        "sentences": [sentence] * 3,
        "summary_points": [{"text": f"Summary point {i} from the source.", "sentence_indices": [i]}
                           for i in range(3)],
        "glossary": [],
        "questions": [{"stem": f"Which outcome label does statement {i} use?", "bloom": "apply",
            "options": ["possible result", "event count", "sample size"], "answer_index": 0,
            "rationale": "The source names the possible result.", "sentence_indices": [i]}
            for i in range(3)],
    })
    result = _make_lesson_assets("l1", candidate, chunks, prompt_version="role-lane-test")
    assert all(item.kind == "primary" for item in result.draft.sentences)


def test_mixed_role_citation_lane_mismatch_fails_closed():
    from b2c_contracts import StructuredChunk
    from b2c_pedagogy.lesson_enrichment import _AssetsCandidate, _make_lesson_assets

    _, _, chunks = _sources()
    supplementary = StructuredChunk(chunk_id="c2", book_id="supp", role="supplementary",
        section_path=["Probability"], block_ids=["sb1"], pages=[1],
        text="Auxiliary source states a different supporting detail.", token_count=8,
        embedding_model="test")
    sentence = {"text_en": chunks[0].text, "citations": ["c1", "c2"], "kind": "primary"}
    candidate = _AssetsCandidate.model_validate({
        "sentences": [sentence] + [
            {"text_en": chunks[0].text, "citations": ["c1"], "kind": "primary"} for _ in range(2)
        ],
        "summary_points": [{"text": f"Summary point {i} from the source.", "sentence_indices": [i]}
                           for i in range(3)],
        "glossary": [],
        "questions": [{"stem": f"Which outcome label does statement {i} use?", "bloom": "apply",
            "options": ["possible result", "event count", "sample size"], "answer_index": 0,
            "rationale": "The source names the possible result.", "sentence_indices": [i]}
            for i in range(3)],
    })
    with pytest.raises(ValueError, match="primary lesson sentence cites a supplementary source"):
        _make_lesson_assets("l1", candidate, [*chunks, supplementary], prompt_version="mixed-role-lane")


def test_summary_accepts_common_text_en_alias_and_normalizes_to_contract_field():
    from b2c_pedagogy.lesson_enrichment import _SummaryPoint

    point = _SummaryPoint.model_validate({"text_en": "A concise source-grounded summary point.",
                                          "sentence_indices": [0]})
    assert point.text == "A concise source-grounded summary point."


def test_mcq_distractors_can_be_grounded_in_other_approved_lesson_chunks():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_evidence_consistency

    cited = StructuredChunk(chunk_id="c1", book_id="book", role="spine", section_path=["Caning"],
        block_ids=["b1"], pages=[1], text="Step 7 pulls a cane up through a hole and over the binder cane.",
        token_count=12, embedding_model="test")
    distractor_source = StructuredChunk(chunk_id="c2", book_id="book", role="spine",
        section_path=["Caning"], block_ids=["b2"], pages=[2],
        text="Steps 4, 5, and 6 describe earlier operations using different cane directions.",
        token_count=14, embedding_model="test")
    draft = LessonDraft(lesson_id="l1", version=1, sentences=[LessonSentence(
        sid="l1:s1", text_en=cited.text, citations=["c1"], kind="primary")], faithfulness={})
    question = Question(qid="l1:q1", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="understand", stem="Which step pulls a cane up through a hole and over the binder cane?",
        options=["Step 7", "Step 4", "Step 5", "Step 6"], answer_index=0,
        rationale="The cited lesson sentence identifies Step 7.", evidence_sids=["l1:s1"], validation={})
    checks = _validate_mcq_evidence_consistency(draft, [question], [cited, distractor_source])
    assert checks == [{"source_detail_anchor_pass": True, "distractors_anchored_pass": True}]


def test_mcq_set_linter_rejects_answer_length_clue_and_position_skew():
    from b2c_pedagogy.lesson_enrichment import _validate_mcq_set_balance

    def make_question(index, options, answer_index=0):
        return Question(qid=f"l1:q{index}", lesson_id="l1", purpose="end_of_lesson", type="mcq",
            bloom="understand", stem="Which fact is stated?", options=options,
            answer_index=answer_index, rationale="The cited lesson gives this fact.",
            evidence_sids=["l1:s1"], validation={})

    too_long = make_question(1, ["the complete distinctive exact answer for this question", "other fact", "another one", "last fact"])
    with pytest.raises(ValueError, match="option length"):
        _validate_mcq_set_balance([too_long])

    skewed = [make_question(i, ["one fact", "two facts", "three facts", "four facts"])
              for i in range(8)]
    with pytest.raises(ValueError, match="positions are imbalanced"):
        _validate_mcq_set_balance(skewed)

    balanced = [make_question(i, ["one fact", "two facts", "three facts", "four facts"], answer_index=i % 4)
                for i in range(8)]
    assert all(item["answer_position_balance_pass"] for item in _validate_mcq_set_balance(balanced))


def test_option_builder_uses_only_source_anchored_facts_and_emits_per_option_evidence():
    from b2c_pedagogy.lesson_enrichment import _MCQProposal, _Sentence, _build_source_grounded_mcqs

    sentences = [
        _Sentence(text_en="Probability measures the chance of an event.", citations=["c1"], kind="primary"),
        _Sentence(text_en="Event count records occurrences.", citations=["c1"], kind="primary"),
        _Sentence(text_en="Sample size counts observations.", citations=["c1"], kind="primary"),
    ]
    proposals = [
        _MCQProposal(stem="Which phrase describes probability likelihood?", bloom="apply",
            answer_text="chance of an event", rationale="Probability is stated as chance of an event.", sentence_indices=[0]),
        _MCQProposal(stem="What does the event count record?", bloom="apply",
            answer_text="records occurrences", rationale="The source says it records occurrences.", sentence_indices=[1]),
        _MCQProposal(stem="What does sample size measure?", bloom="analyze",
            answer_text="counts observations", rationale="The source says sample size counts observations.", sentence_indices=[2]),
    ]
    questions = _build_source_grounded_mcqs(proposals, sentences, 3)
    assert len(questions) == 3
    assert all(len(q.options) == 3 for q in questions)
    assert all(q.option_sentence_indices and len(q.option_sentence_indices) == len(q.options) for q in questions)
    assert all(q.options[q.answer_index] == proposals[i].answer_text for i, q in enumerate(questions))


def test_one_based_index_normalization_requires_uniform_out_of_range_evidence():
    from b2c_pedagogy.lesson_enrichment import _AssetsCandidate, _MCQCandidate, _SummaryPoint, _normalize_proven_one_based_references

    candidate = _AssetsCandidate(
        sentences=[{"text_en": f"Sentence carries fact number {i}.", "citations": ["c1"], "kind": "primary"}
                   for i in range(1, 4)],
        summary_points=[_SummaryPoint(text=f"The lesson states these facts {i}.", sentence_indices=[1, 2, 3]) for i in range(3)],
        glossary=[],
        questions=[_MCQCandidate(stem=f"Which fact from sentence {i + 1}?", bloom="apply",
            options=["fact number 1", "fact number 2", "fact number 3"], answer_index=0,
            rationale="The source says the fact number.", sentence_indices=[0]) for i in range(3)],
    )
    normalized, provenance = _normalize_proven_one_based_references(candidate)
    assert all(point.sentence_indices == [0, 1, 2] for point in normalized.summary_points)
    assert normalized.questions[0].sentence_indices == [0]
    assert provenance == {"summary": True, "glossary": False, "questions": False}


def test_question_top_up_rejects_duplicates_and_incorrect_final_count():
    from b2c_pedagogy.lesson_enrichment import _MCQCandidate, _merge_question_top_up

    def item(stem):
        return _MCQCandidate(stem=stem, bloom="apply", options=["one", "two", "three"],
            answer_index=0, rationale="The source supports this choice.", sentence_indices=[0])

    with pytest.raises(ValueError, match="duplicate"):
        _merge_question_top_up([item("Which step changes first?")],
                               [item("Which step changes first!")], expected_total=2)
    with pytest.raises(ValueError, match="count mismatch"):
        _merge_question_top_up([item("Which step changes first?")], [], expected_total=2)
    with pytest.raises(ValueError, match="duplicate"):
        _merge_question_top_up([], [item("Which step changes first?")], expected_total=1,
                               excluded_stems=["Which step changes first!"])


def test_lesson_asset_builder_rejects_answer_phrase_leaked_in_mcq_stem():
    from b2c_pedagogy.lesson_enrichment import _AssetsCandidate, _make_lesson_assets

    _, _, chunks = _sources()
    candidate = _AssetsCandidate.model_validate({
        "sentences": [{"text_en": chunks[0].text, "citations": ["c1"], "kind": "primary"}] * 3,
        "summary_points": [{"text": f"Summary point {i} from the source.", "sentence_indices": [i]}
                           for i in range(3)],
        "glossary": [],
        "questions": [{"stem": f"Which phrase is the possible result in case {i}?", "bloom": "apply",
            "options": ["possible result", "event count", "sample size"], "answer_index": 0,
            "rationale": "The source names the possible result.", "sentence_indices": [0]}
            for i in range(3)],
    })
    with pytest.raises(ValueError, match="stem exposes answer"):
        _make_lesson_assets("l1", candidate, chunks, prompt_version="answer-leak-test")


def test_numbered_procedure_builder_uses_uniform_source_backed_step_choices():
    from b2c_pedagogy.lesson_enrichment import _Sentence, _build_numbered_procedure_mcqs

    sentences = []
    for step in range(1, 5):
        sentences.append(_Sentence(text_en=(
            f"Step {step}. The learner places a {['red', 'blue', 'green', 'white'][step - 1]} cane beside the parallel rail before continuing the woven pattern. "
            f"The strand is held along the {['outer', 'middle', 'inner', 'upper'][step - 1]} side while the learner completes this operation."
        ), citations=["c1"], kind="primary"))
    questions = _build_numbered_procedure_mcqs(sentences, 8)

    assert questions is not None and len(questions) == 8
    assert all(question.bloom == "apply" for question in questions)
    assert all(question.options and len(set(map(len, question.options))) == 1 for question in questions)
    assert all(len(question.option_sentence_indices) == len(question.options) for question in questions)
    assert all(all(option.startswith("Step ") for option in question.options) for question in questions)


def test_numbered_procedure_builder_falls_back_when_source_support_is_insufficient():
    from b2c_pedagogy.lesson_enrichment import _Sentence, _build_numbered_procedure_mcqs

    sentences = [_Sentence(text_en=f"Step {step}. A source step has a distinct operation.",
                           citations=["c1"], kind="primary") for step in range(1, 4)]
    assert _build_numbered_procedure_mcqs(sentences, 3) is None


def test_generation_prompt_is_coherent_and_versioned():
    from b2c_pedagogy.lesson_enrichment import LESSON_ENRICHMENT_PROMPT_VERSION

    outline, book, chunks = _sources()
    gateway = _Gateway()
    generate_lesson_enrichments(outline, book, chunks, gateway, question_count=3)
    system = gateway.calls[0][2][0]["content"]
    assert "Every summary, glossary item, and MCQ must reference sentence indices." in system
    assert "Every summary, Use primary" not in system
    assert LESSON_ENRICHMENT_PROMPT_VERSION == "phase5-lesson-enrichment-v4"
    assert gateway.calls[0][1] == "phase5-lesson-enrichment-v4:generate:attempt-1"


def test_numbered_procedure_questions_never_trigger_proposal_repair_calls(monkeypatch):
    import b2c_pedagogy.lesson_enrichment as mod

    built = object()
    monkeypatch.setattr(mod, "_build_numbered_procedure_mcqs", lambda sentences, count: [built] * count)
    monkeypatch.setattr(mod, "_AssetsCandidate", lambda **kwargs: kwargs)

    def failing_assets(scope_id, candidate, *args, **kwargs):
        assert candidate["questions"] == [built] * 3
        raise ValueError(f"{scope_id}:q001: stem exposes answer through the keyed option")

    monkeypatch.setattr(mod, "_make_lesson_assets", failing_assets)
    outline, book, chunks = _sources()
    gateway = _Gateway()
    with pytest.raises(ValueError, match="q001"):
        generate_lesson_enrichments(outline, book, chunks, gateway, question_count=3, question_repair_limit=3)
    assert [call[0] for call in gateway.calls] == ["ExactQuestions"]


def test_resumed_enrichment_reruns_mcq_evidence_consistency():
    outline, book, chunks = _sources()
    [lesson] = generate_lesson_enrichments(outline, book, chunks, _Gateway(), question_count=3)
    first = lesson.questions[0]
    tampered_question = first.model_copy(update={"stem": "After calling stats.chance(x), " + first.stem})
    tampered = lesson.model_copy(update={"questions": [tampered_question, *lesson.questions[1:]]})

    class NoCalls:
        model_id = "fixture"

        def structured(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("resumed lesson must not call the model")

    assert generate_lesson_enrichments(outline, book, chunks, NoCalls(), question_count=3,
                                       precompleted=[lesson]) == [lesson]
    with pytest.raises(ValueError, match="precompleted enrichment failed revalidation: l1: .*code premise"):
        generate_lesson_enrichments(outline, book, chunks, NoCalls(), question_count=3, precompleted=[tampered])
