"""Arabic inflection-tolerant matching of locked terms and the minor ``terminology_hint`` check
(term extraction v3, D-039). Synthetic sentences only.

A locked term is still required (major) when its source occurs, but the Arabic match tolerates
the article, the proclitics و ف ب ل ك, sound plurals and duals, the open taa of a construct
state with a pronoun suffix, and the gender/number agreement of a nisba adjective. A forbidden
variant keeps the strict (clitic-only) match.
"""
from __future__ import annotations

import pytest
from b2c_translation.qa import CheckContext
from b2c_translation.qa import checks as C
from b2c_translation.qa.checks import DEFAULT_CHECKS, contains_term, contains_term_inflected, run_checks

SOCIAL_GROUP = {"social group": "جماعة اجتماعية"}


def _ctx(make_unit, source, target, *, terms=None, hints=None, forbidden=None):
    return CheckContext(unit=make_unit(source), target=target, terms=terms or {}, hints=hints or {},
                        forbidden=forbidden or {})


# ------------------------------------------------------------------ known false alarm (book 2)
def test_social_groups_plural_with_article_does_not_alarm(make_unit):
    ctx = _ctx(make_unit, "Subaltern social groups resist.", "تقاوم الجماعات الاجتماعية التابعة.",
               terms=SOCIAL_GROUP)
    assert C.terminology(ctx) == []


@pytest.mark.parametrize("target", [
    "جماعة اجتماعية تتشكل.",                 # exact
    "الجماعة الاجتماعية تتشكل.",             # article on both words
    "وللجماعات الاجتماعية دور.",             # و + ل + ال -> لل, plural
    "بالجماعات الاجتماعية.",                 # ب + ال
    "فالجماعات الاجتماعية تتشكل.",           # ف + ال
    "كجماعة اجتماعية.",                      # ك
    "جماعات اجتماعية متعددة.",               # indefinite plural
    "جماعتان اجتماعيتان.",                   # dual (nominative)
    "بين جماعتين اجتماعيتين.",               # dual (oblique)
    "جماعتهم الاجتماعية.",                   # open taa before a pronoun suffix
])
def test_locked_term_inflections_are_accepted(make_unit, target):
    ctx = _ctx(make_unit, "A social group forms.", target, terms=SOCIAL_GROUP)
    assert C.terminology(ctx) == [], target


@pytest.mark.parametrize("target", [
    "التحليلات الاجتماعية مفيدة.",           # masculine noun, feminine plural; nisba agrees in gender
    "التحليل الاجتماعي مفيد.",
])
def test_nisba_adjective_agreement_is_accepted(make_unit, target):
    ctx = _ctx(make_unit, "Social analysis helps.", target, terms={"social analysis": "تحليل اجتماعي"})
    assert C.terminology(ctx) == [], target


def test_accusative_tanween_alif_is_accepted(make_unit):
    # seen in book 1: «سواء كان محللاً، أو قائداً، أو ميسراً» for the locked «ميسر» (facilitator)
    ctx = _ctx(make_unit, "whether analyst, leader or facilitator", "سواء كان محللاً، أو قائداً، أو ميسراً.",
               terms={"facilitator": "ميسر"})
    assert C.terminology(ctx) == []


@pytest.mark.parametrize("target", [
    "يقاوم المثقفون العضويون.",
    "مع المثقفين العضويين.",
])
def test_sound_masculine_plural_is_accepted(make_unit, target):
    ctx = _ctx(make_unit, "Organic intellectuals resist.", target,
               terms={"organic intellectual": "المثقف العضوي"})
    assert C.terminology(ctx) == [], target


@pytest.mark.parametrize("target", [
    "تقاوم الطبقات الشعبية.",                # a different noun
    "الجماعات السياسية تتشكل.",              # a different adjective
    "جماعة من الناس اجتماعية.",              # words not adjacent
])
def test_a_different_rendering_is_still_a_major_violation(make_unit, target):
    ctx = _ctx(make_unit, "A social group forms.", target, terms=SOCIAL_GROUP)
    issues = C.terminology(ctx)
    assert issues and issues[0].check_id == "terminology" and issues[0].severity == "major", target


def test_short_stems_do_not_overmatch():
    # a remainder shorter than three letters after stripping a suffix is never a match
    assert not contains_term_inflected("زارت الأم المدرسة.", "أمة")     # أمة -> ام is too short
    assert not contains_term_inflected("في البيت", "بيان")              # بيان -> بي is too short
    assert contains_term_inflected("قامت الأمم.", "الأمة") is False     # broken plural: not handled


def test_forbidden_variants_keep_the_strict_match(make_unit):
    terms = {"subaltern": "التابع"}
    forbidden = {"subaltern": ("المهمش",)}
    # the strict matcher is unchanged: an inflected form of the forbidden variant is not a hit
    assert contains_term("المهمشون", "المهمش") is False
    ctx = _ctx(make_unit, "The subaltern speaks.", "يتكلم التابع والمهمشون.", terms=terms, forbidden=forbidden)
    assert [i.check_id for i in C.terminology(ctx)] == []
    ctx = _ctx(make_unit, "The subaltern speaks.", "يتكلم التابع (المهمش).", terms=terms, forbidden=forbidden)
    assert [i.check_id for i in C.terminology(ctx)] == ["forbidden_variant"]


# ------------------------------------------------------------------ hints
def test_hint_check_is_registered_with_the_terminology_category():
    declared = {c.check_id: c.categories for c in DEFAULT_CHECKS}
    assert declared["terminology_hint"] == ("terminology",)


def test_a_missing_hint_is_only_minor(make_unit):
    ctx = _ctx(make_unit, "The analyst wrote a report.", "كتب الباحث تقريرًا.", hints={"analyst": "المحلل"})
    issues = C.terminology_hint(ctx)
    assert [(i.check_id, i.severity) for i in issues] == [("terminology_hint", "minor")]
    assert issues[0].evidence == {"term": "analyst", "preferred": "المحلل"}
    assert not any(i.severity in ("major", "critical") for i in run_checks(ctx) if i.category == "terminology")


def test_an_inflected_hint_passes(make_unit):
    ctx = _ctx(make_unit, "Analysts wrote reports.", "كتب المحللون تقارير.", hints={"analyst": "المحلل"})
    assert C.terminology_hint(ctx) == []


def test_hints_absent_from_the_source_are_ignored(make_unit):
    ctx = _ctx(make_unit, "The state is strong.", "الدولة قوية.", hints={"analyst": "المحلل"})
    assert C.terminology_hint(ctx) == []


def test_a_term_that_is_both_locked_and_hint_is_checked_as_locked(make_unit):
    ctx = _ctx(make_unit, "The analyst wrote.", "كتب الباحث.", terms={"analyst": "المحلل"},
               hints={"analyst": "المحلل"})
    assert [i.check_id for i in run_checks(ctx) if i.category == "terminology"] == ["terminology"]
