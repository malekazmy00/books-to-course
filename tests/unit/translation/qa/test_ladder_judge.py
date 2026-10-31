"""Escalation ladder of independent judges (D-033; learning-companion §5 step 4; D-050).

Two judges of different model families vote; agreement decides. A disagreement (or a missing
vote) goes to the tie-break judge; a doubt it cannot resolve fails closed, so the unit becomes a
translator note instead of a silent acceptance. Measured offline on planted errors:
recall 0.983 / false alarm 0.033, tie-break on 17 of 149 items (eval/reports/judge-bakeoff-2026-10-31.md).
"""
from __future__ import annotations

from b2c_contracts import QAIssue, QEResult, TranslationUnit
from b2c_translation.qa.estimators import LadderJudge
from b2c_translation.qa.types import QEOutcome

UNIT = TranslationUnit(unit_id="bk:u00001", book_id="bk", block_ids=["bk:1:0"], kind="prose",
                       source_text="The results were more accurate.", pages=[1], section_path=[])


class Judge:
    def __init__(self, name, verdict):
        self.name, self.verdict, self.calls = name, verdict, 0

    def estimate(self, unit, target):
        self.calls += 1
        if self.verdict == "down":
            issue = QAIssue(check_id="llm_judge_unavailable", category="other", severity="major", origin="qe",
                            evidence={"reason": "HTTP 429"})
            return QEOutcome(passed=False, result=QEResult(deterministic_pass=False, route="human_review"),
                             issues=[issue])
        ok = self.verdict == "pass"
        issues = [] if ok else [QAIssue(check_id="llm_mqm.meaning_error", category="meaning_error",
                                        severity="major", origin="qe", evidence={"span": "x"})]
        return QEOutcome(passed=ok, result=QEResult(deterministic_pass=False,
                                                    llm_mqm={"major": 0 if ok else 1, "model": self.name},
                                                    route="auto_accept" if ok else "retranslate"), issues=issues)


def run(a, b, t=None):
    judges = [Judge("deepseek", a), Judge("mercury", b)]
    tie = Judge("pro", t) if t is not None else None
    out = LadderJudge(judges, tiebreak=tie).estimate(UNIT, "النتائج أكثر دقة.")
    return out, judges, tie


def test_agreement_to_accept_needs_no_tiebreak():
    out, _, tie = run("pass", "pass", "fail")
    assert out.passed and out.result.route == "auto_accept" and tie.calls == 0
    assert out.result.llm_mqm["decided_by"] == "agreement"


def test_agreement_to_reject_keeps_the_issues_for_repair():
    out, _, tie = run("fail", "fail", "pass")
    assert not out.passed and out.result.route == "retranslate" and tie.calls == 0
    assert [i.check_id for i in out.issues] == ["llm_mqm.meaning_error", "llm_mqm.meaning_error"]


def test_disagreement_goes_to_the_tiebreak_judge():
    out, _, tie = run("pass", "fail", "pass")
    assert out.passed and tie.calls == 1 and out.result.llm_mqm["decided_by"] == "tiebreak"
    out, _, _ = run("pass", "fail", "fail")
    assert not out.passed and out.result.route == "retranslate"


def test_a_missing_vote_also_goes_to_the_tiebreak():
    out, _, tie = run("down", "pass", "pass")
    assert out.passed and tie.calls == 1


def test_unresolved_doubt_fails_closed():
    for args in (("pass", "fail", "down"), ("pass", "fail", None), ("down", "down", "pass")):
        out, _, _ = run(*args)
        assert not out.passed and out.result.route == "human_review", args
        assert out.result.llm_mqm["decided_by"] == "unresolved"


def test_every_vote_is_recorded():
    out, _, _ = run("pass", "fail", "pass")
    votes = out.result.llm_mqm["votes"]
    assert [(v["judge"], v["passed"]) for v in votes] == [("deepseek", True), ("mercury", False), ("pro", True)]
