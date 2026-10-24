from b2c_contracts import (
    GlossaryTerm,
    LessonDraft,
    LessonSentence,
    LessonSummary,
    Question,
)
from b2c_pedagogy.validation import lint_mcq, validate_lesson_assets


def question(*, stem: str = "Which choice describes probability?", bloom: str = "understand", options=None) -> Question:
    return Question(
        qid="q1",
        lesson_id="m01.l01",
        purpose="end_of_lesson",
        type="mcq",
        bloom=bloom,
        stem=stem,
        options=options or ["A chance measure", "A page count", "A chapter title"],
        answer_index=0,
        rationale="The lesson defines probability this way.",
        evidence_sids=["m01.l01:s01"],
        validation={},
    )


def draft() -> LessonDraft:
    return LessonDraft(
        lesson_id="m01.l01",
        version=1,
        sentences=[
            LessonSentence(
                sid=f"m01.l01:s0{i}",
                text_en=f"Lesson fact {i}.",
                citations=["chunk-1"],
                kind="primary",
            )
            for i in range(1, 4)
        ],
        faithfulness={"ragas_faithfulness": 0.9, "citation_support_rate": 1.0},
    )


def summary() -> LessonSummary:
    return LessonSummary(
        lesson_id="m01.l01",
        points=["Point one", "Point two", "Point three"],
        point_sids=[["m01.l01:s01"], ["m01.l01:s02"], ["m01.l01:s03"]],
    )


def test_mcq_linter_flags_duplicate_and_all_of_the_above() -> None:
    report = lint_mcq(question(options=["True", "True", "All of these"]))

    assert {finding.code for finding in report.findings} == {
        "mcq.duplicate_option",
        "mcq.all_none_of_above",
    }


def test_lesson_assets_pass_with_resolved_evidence_and_higher_bloom_question() -> None:
    report = validate_lesson_assets(
        draft(),
        summary(),
        [GlossaryTerm(term_en="probability", definition_en="A measure", first_sid="m01.l01:s01", lesson_id="m01.l01")],
        [question()],
    )

    assert report.valid


def test_lesson_assets_fail_closed_on_unknown_sentence_references_and_remember_only() -> None:
    bad_summary = LessonSummary(
        lesson_id="m01.l01",
        points=["P1", "P2", "P3"],
        point_sids=[["missing"], ["m01.l01:s02"], ["m01.l01:s03"]],
    )
    report = validate_lesson_assets(
        draft(),
        bad_summary,
        [GlossaryTerm(term_en="term", definition_en="def", first_sid="missing", lesson_id="m01.l01")],
        [question(bloom="remember")],
    )

    assert {finding.code for finding in report.findings} >= {
        "summary.unknown_sid",
        "glossary.unknown_sid",
        "question.no_higher_bloom",
    }


def test_mcq_linter_rejects_choice_lengths_over_eight_character_spread():
    result = lint_mcq(question(options=["Step 4", "Step 5", "The cane goes over the binder"] ))
    assert not result.valid
    assert any(f.code == "mcq.option_length_cue" for f in result.findings)


def test_mcq_linter_accepts_uniform_step_choices():
    assert lint_mcq(question(options=["Step 2", "Step 3", "Step 4", "Step 5"])).valid
