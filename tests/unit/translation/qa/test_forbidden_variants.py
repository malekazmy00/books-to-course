"""Forbidden variants of a locked term are flagged even when the locked form is also present."""
from b2c_contracts import TranslationUnit
from b2c_translation.qa import CheckContext
from b2c_translation.qa.checks import run_checks

UNIT = TranslationUnit(unit_id="b:u1", book_id="b", block_ids=["b:1:0"], kind="prose", pages=[1],
                       section_path=[], source_text="Run a Premortem Analysis before deciding.")
TERMS = {"Premortem Analysis": "تحليل ما قبل الفشل"}
FORBIDDEN = {"Premortem Analysis": ("ما قبل الموت",)}


def issues(target, forbidden=FORBIDDEN):
    return [i for i in run_checks(CheckContext(unit=UNIT, target=target, terms=TERMS, forbidden=forbidden))
            if i.category == "terminology"]


def test_forbidden_variant_is_flagged():
    found = issues("أجرِ تحليل ما قبل الموت قبل اتخاذ القرار.")
    assert any(i.check_id == "forbidden_variant" and i.severity == "major" for i in found)


def test_forbidden_variant_flagged_even_next_to_the_locked_form():
    found = issues("أجرِ تحليل ما قبل الفشل (ما قبل الموت) قبل القرار.")
    assert [i.check_id for i in found] == ["forbidden_variant"]


def test_correct_target_passes():
    assert issues("أجرِ تحليل ما قبل الفشل قبل اتخاذ القرار.") == []


def test_variant_only_checked_when_the_source_term_occurs():
    other = UNIT.model_copy(update={"source_text": "Decide carefully."})
    found = [i for i in run_checks(CheckContext(unit=other, target="ما قبل الموت", terms=TERMS, forbidden=FORBIDDEN))
             if i.category == "terminology"]
    assert found == []
