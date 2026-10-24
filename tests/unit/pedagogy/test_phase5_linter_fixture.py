from b2c_contracts import Question
from b2c_pedagogy.validation import lint_bloom_distribution, lint_mcq


def _question(index, *, stem="Which concept is described?", options=None):
    return Question(
        qid=f"fixture-{index}", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom="understand", stem=stem,
        options=options or ["A coherent definition", "A distinct example", "A useful measurement"],
        answer_index=0, rationale="The lesson supports the first answer.",
        evidence_sids=["l1:s001"], validation={},
    )


def _seeded_flawed_mcqs():
    cases = []
    for i in range(6):
        cases.append((_question(i, options=["Same answer", "Same answer", "A different answer"]),
                      "mcq.duplicate_option"))
    for i in range(6, 12):
        cases.append((_question(i, options=["First choice", "Second choice", "All of the above"]),
                      "mcq.all_none_of_above"))
    for i in range(12, 18):
        cases.append((_question(i, options=["First choice", "Second choice", "Both A and B"]),
                      "mcq.combination_option"))
    for i in range(18, 24):
        cases.append((_question(i, stem=f"Which statement is NOT correct, item {i}?"),
                      "mcq.negative_stem"))
    for i in range(24, 30):
        cases.append((_question(i, options=[
            "Yes", "No", "Maybe",
            f"A much longer and conspicuous option that gives away the answer in item {i}",
        ]), "mcq.option_length_cue"))
    return cases


def test_linter_detects_at_least_ninety_percent_of_thirty_seeded_flawed_mcqs():
    # These are controlled, synthetically injected rule violations, not human gold labels.
    cases = _seeded_flawed_mcqs()
    detected = [lint_mcq(item) for item, _ in cases]
    assert len(cases) == 30
    assert sum(bool(result.findings) for result in detected) / len(cases) >= 0.90
    for result, (_, expected_code) in zip(detected, cases, strict=True):
        assert expected_code in {finding.code for finding in result.findings}


def test_linter_leaves_a_structurally_clean_mcq_unflagged():
    assert lint_mcq(_question(99)).valid


def _bloom_question(index, bloom):
    return Question(
        qid=f"bloom-{index}", lesson_id="l1", purpose="end_of_lesson", type="mcq",
        bloom=bloom, stem=f"Apply the rule to case {index}?",
        options=["One", "Two", "Three", "Four"], answer_index=0,
        rationale="The source rule applies to this case.", evidence_sids=["l1:s001"], validation={},
    )


def test_phase5_bloom_gate_requires_eighty_percent_apply_or_analyze():
    passing = [_bloom_question(i, "apply" if i < 8 else "remember") for i in range(10)]
    failing = [_bloom_question(i, "apply" if i < 7 else "remember") for i in range(10)]

    assert lint_bloom_distribution(passing).valid
    report = lint_bloom_distribution(failing)
    assert not report.valid
    assert report.findings[0].code == "question.bloom_distribution"


def test_phase5_bloom_gate_rejects_all_remember_batch():
    report = lint_bloom_distribution([_bloom_question(i, "remember") for i in range(10)])

    assert not report.valid
    assert "found 0" in report.findings[0].message
