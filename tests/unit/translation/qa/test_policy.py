"""Decision policy (D-028) incl. T-TR-04 negative controls. Synthetic text only."""
from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest

from b2c_contracts import EngineIdentity, QAIssue, QEResult, UnitTranslation
from b2c_translation.qa.checks import run_checks
from b2c_translation.qa.leakage import fingerprints_from_templates
from b2c_translation.qa.policy import REPAIRABLE_CATEGORIES, Decision, decide
from b2c_translation.qa.types import QEOutcome

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
ENGINE = EngineIdentity(provider="test", model_id="m", prompt_version="p1")


def issue(category="terminology", severity="major", origin="deterministic"):
    return QAIssue(check_id=f"c_{category}", category=category, severity=severity, origin=origin)


def qe(passed: bool, issues=()):
    route = "auto_accept" if passed else "retranslate"
    return QEOutcome(passed=passed, result=QEResult(deterministic_pass=False, route=route), issues=list(issues))


def as_translation(d: Decision, origin="engine") -> UnitTranslation:
    return d.to_unit_translation(unit_id="bk:u00001", target_text="نص", origin=origin,
                                 engine=ENGINE if origin == "engine" else None, attempts=1, decided_at=NOW)


# ------------------------------------------------------------------ core table
@pytest.mark.parametrize("issues,qe_outcome,attempts,max_attempts,repair,expected", [
    ([], qe(True), 1, 3, True, ("accepted", "accept")),
    ([issue(severity="minor")], qe(True), 1, 3, True, ("accepted", "accept")),
    ([], None, 1, 3, True, ("needs_review", "review")),                       # no QE -> never accepted
    ([], qe(False), 1, 3, True, ("needs_review", "retranslate")),
    ([], qe(False), 3, 3, True, ("needs_review", "review")),
    ([], qe(True, [issue("meaning_error", "major", "qe")]), 1, 3, True, ("needs_review", "retranslate")),
    ([issue("terminology")], qe(True), 1, 3, True, ("needs_review", "repair")),
    ([issue("terminology")], qe(True), 1, 3, False, ("needs_review", "retranslate")),
    ([issue("number_error")], None, 1, 3, True, ("needs_review", "repair")),
    ([issue("polarity_error")], qe(True), 1, 3, True, ("needs_review", "retranslate")),
    ([issue("prompt_leakage", "critical")], qe(True), 1, 3, True, ("needs_review", "retranslate")),
    ([issue("terminology"), issue("omission", "critical")], qe(True), 1, 3, True,
     ("needs_review", "retranslate")),
    ([issue("terminology")], qe(True), 3, 3, True, ("needs_review", "review")),
    ([issue("prompt_leakage", "critical")], qe(True), 3, 3, True, ("rejected", "reject")),
    ([issue("omission", "critical")], None, 5, 3, False, ("rejected", "reject")),
])
def test_decision_table(issues, qe_outcome, attempts, max_attempts, repair, expected):
    d = decide(issues, qe_outcome, attempts=attempts, max_attempts=max_attempts, engine_supports_repair=repair)
    assert (d.decision, d.next_action) == expected
    assert d.reasons
    as_translation(d)  # contract invariants hold for every outcome


def test_repairable_categories_exclude_meaning_level_errors():
    for category in ("prompt_leakage", "omission", "addition", "meaning_error", "polarity_error"):
        assert category not in REPAIRABLE_CATEGORIES
    for category in ("terminology", "named_entity", "number_error", "footnote_marker"):
        assert category in REPAIRABLE_CATEGORIES


def test_decision_carries_qe_with_true_deterministic_flag_and_route():
    d = decide([], qe(True), attempts=1, max_attempts=3, engine_supports_repair=True)
    assert d.qe is not None and d.qe.deterministic_pass is True and d.qe.route == "auto_accept"
    d = decide([issue()], qe(True), attempts=3, max_attempts=3, engine_supports_repair=True)
    assert d.qe.deterministic_pass is False and d.qe.route == "human_review"


def test_rule_and_memory_output_can_be_accepted_without_qe():
    for origin in ("rule", "memory"):
        d = decide([], None, attempts=0, max_attempts=3, engine_supports_repair=False, origin=origin)
        assert d.decision == "accepted"
        as_translation(d, origin=origin)


def test_human_output_is_never_accepted_by_policy():
    d = decide([], qe(True), attempts=0, max_attempts=3, engine_supports_repair=False, origin="human")
    assert d.decision == "needs_review"


@pytest.mark.parametrize("bad", [{"attempts": -1, "max_attempts": 3}, {"attempts": 0, "max_attempts": 0}])
def test_invalid_attempt_counts_raise(bad):
    with pytest.raises(ValueError):
        decide([], None, engine_supports_repair=True, **bad)


def test_exhaustive_grid_never_accepts_major_or_critical_or_unverified_engine_output():
    severities = [None, "minor", "major", "critical"]
    qes = [None, qe(True), qe(False), qe(True, [issue("meaning_error", "major", "qe")])]
    for sev, q, attempts, repair in itertools.product(severities, qes, range(0, 5), (True, False)):
        issues = [] if sev is None else [issue("terminology", sev)]
        d = decide(issues, q, attempts=attempts, max_attempts=3, engine_supports_repair=repair)
        if d.decision == "accepted":
            assert sev in (None, "minor") and q is not None and q.passed and not q.issues
        if attempts >= 3:
            assert d.next_action in ("accept", "review", "reject")
        as_translation(d)


# ------------------------------------------------------------------ T-TR-04 negative controls
SOURCE = ("The committee did not approve the budget in 2019. It met again on 12 March. "
          "Smith said the plan was sound.")
GOOD = ("لم توافق اللجنة على الميزانية في عام 2019. واجتمعت مرة أخرى في 12 مارس. "
        "وقال سميث إن الخطة سليمة.")
TERMS = {"budget": "الميزانية"}
ENTITIES = {"Smith": "سميث"}
FPS = fingerprints_from_templates(["Translate the source text into Arabic. Output the translation only."])

CORRUPTIONS = {
    "dropped_negation": GOOD.replace("لم توافق", "وافقت"),
    "wrong_term": GOOD.replace("الميزانية", "الموازنة"),
    "changed_number": GOOD.replace("2019", "2018"),
    "leaked_instruction": GOOD + " لا يحتوي هذا المصدر على علامات محمية. [نص المصدر]",
    "leaked_english_instruction": GOOD + " Output the translation only.",
    "omitted_sentence": "لم توافق اللجنة على الميزانية في عام 2019.",
    "empty": "",
}


@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_negative_controls_are_never_accepted_even_if_qe_is_fooled(make_ctx, name):
    ctx = make_ctx(SOURCE, CORRUPTIONS[name], terms=TERMS, entities=ENTITIES, fingerprints=FPS)
    issues = run_checks(ctx)
    assert any(i.severity in ("major", "critical") for i in issues), name
    for attempts in range(0, 4):
        d = decide(issues, qe(True), attempts=attempts, max_attempts=3, engine_supports_repair=True)
        assert d.decision != "accepted", (name, attempts)


def test_positive_control_is_accepted_with_passing_qe(make_ctx):
    issues = run_checks(make_ctx(SOURCE, GOOD, terms=TERMS, entities=ENTITIES, fingerprints=FPS))
    d = decide(issues, qe(True), attempts=1, max_attempts=3, engine_supports_repair=True)
    assert d.decision == "accepted", issues
