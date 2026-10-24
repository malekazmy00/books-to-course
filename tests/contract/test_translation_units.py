"""Translation rebuild contracts (D-028). Negative controls MUST raise."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime.now(timezone.utc)
ENGINE = c.EngineIdentity(provider="local", model_id="m", revision="r1", prompt_version="tr-v1")
QE_PASS = c.QEResult(deterministic_pass=True, route="auto_accept")


def unit(**kw):
    base = dict(unit_id="b:u00001", book_id="b", block_ids=["b:1:0"], kind="prose",
                source_text="The mean is 3.", pages=[1], section_path=["Ch 1"])
    return c.TranslationUnit(**(base | kw))


def translation(**kw):
    base = dict(unit_id="b:u00001", target_text="المتوسط 3.", origin="engine", engine=ENGINE,
                attempts=1, issues=[], qe=QE_PASS, decision="accepted", decided_by="policy", decided_at=NOW)
    return c.UnitTranslation(**(base | kw))


def major(category="omission"):
    return c.QAIssue(check_id="x", category=category, severity="major", origin="deterministic")


def test_unit_needs_source_blocks_and_pages():
    unit()
    with pytest.raises(ValidationError):
        unit(block_ids=[])
    with pytest.raises(ValidationError):
        unit(pages=[])


def test_unit_source_must_not_be_blank():
    with pytest.raises(ValidationError):
        unit(source_text="   ")


def test_unknown_unit_kind_rejected():
    with pytest.raises(ValidationError):
        unit(kind="paragraph")


def test_accepted_cannot_carry_major_or_critical_issue():  # D-028 principle 7
    translation(issues=[c.QAIssue(check_id="x", category="fluency_style", severity="minor",
                                  origin="deterministic")])
    with pytest.raises(ValidationError):
        translation(issues=[major()])


def test_engine_output_needs_semantic_qe_before_policy_accepts():  # form checks alone never accept
    with pytest.raises(ValidationError):
        translation(qe=None)
    translation(qe=None, decision="needs_review")


def test_engine_origin_requires_engine_identity():
    with pytest.raises(ValidationError):
        translation(engine=None)


def test_dropped_units_have_no_target():
    translation(origin="rule", engine=None, qe=None, target_text="", decision="dropped")
    with pytest.raises(ValidationError):
        translation(origin="rule", engine=None, qe=None, target_text="x", decision="dropped")


def test_units_for_review_or_acceptance_need_target_text():
    with pytest.raises(ValidationError):
        translation(target_text="  ", decision="needs_review")
    with pytest.raises(ValidationError):
        translation(target_text="  ")
    # a rejection may have nothing usable at all (e.g. the engine returned nothing)
    translation(target_text="", decision="rejected", qe=None)


def test_human_acceptance_is_explicit():
    translation(origin="human", engine=None, qe=None, decided_by="human")
    with pytest.raises(ValidationError):  # a human edit is not "policy" accepted without QE
        translation(origin="human", engine=None, qe=None, decided_by="policy")


def test_decided_at_must_be_utc():
    with pytest.raises(ValidationError):
        translation(decided_at=datetime.now())
