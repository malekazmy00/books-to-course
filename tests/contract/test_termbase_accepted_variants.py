"""TermbaseEntry.accepted_variants_ar (SCHEMA 1.22.0, D-047): other spellings of an approved name
that the check accepts; never sent to the engine. Pre-1.22 payloads have none."""
import pytest
from pydantic import ValidationError

import b2c_contracts as c


def test_schema_version_is_1_22_0():
    assert c.SCHEMA_VERSION == "1.22.0"


def test_pre_1_22_payloads_have_no_accepted_variants():
    entry = c.TermbaseEntry.model_validate({"term_en": "Groningen", "term_ar": "خرونينغن",
                                            "definition_en": "Groningen", "approved_by": "r"})
    assert entry.accepted_variants_ar == []


def test_accepted_variants_are_arabic_and_distinct_from_target_and_forbidden():
    ok = c.TermbaseEntry(term_en="Groningen", term_ar="خرونينغن", definition_en="Groningen",
                         accepted_variants_ar=["جرونينجن", "غرونينغن"])
    assert ok.accepted_variants_ar == ["جرونينجن", "غرونينغن"]
    for bad in (["Groningen"], ["خرونينغن"], [""]):
        with pytest.raises(ValidationError):
            c.TermbaseEntry(term_en="Groningen", term_ar="خرونينغن", definition_en="Groningen",
                            accepted_variants_ar=bad)
    with pytest.raises(ValidationError):
        c.TermbaseEntry(term_en="Heuer", term_ar="هوير", definition_en="Heuer",
                        forbidden_variants_ar=["هيور"], accepted_variants_ar=["هيور"])
