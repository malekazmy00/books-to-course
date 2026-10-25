"""TermbaseEntry.tier (SCHEMA 1.14.0, D-039): locked / hint; pre-1.14 payloads stay locked."""
import pytest
from pydantic import ValidationError

import b2c_contracts as c


def test_schema_version_has_the_tier_field():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 14, 0)


def test_tier_defaults_to_locked_for_pre_1_14_payloads():
    entry = c.TermbaseEntry.model_validate({"term_en": "hegemony", "term_ar": "الهيمنة",
                                            "definition_en": "hegemony", "approved_by": "r"})
    assert entry.tier == "locked"


def test_hint_tier_is_accepted_and_dropped_is_not():
    hint = c.TermbaseEntry(term_en="analyst", term_ar="المحلل", definition_en="analyst", tier="hint")
    assert hint.tier == "hint"
    with pytest.raises(ValidationError):
        c.TermbaseEntry(term_en="July", term_ar="يوليو", definition_en="July", tier="dropped")


def test_hint_entries_take_no_forbidden_variants():
    with pytest.raises(ValidationError):
        c.TermbaseEntry(term_en="analyst", term_ar="المحلل", definition_en="analyst", tier="hint",
                        forbidden_variants_ar=["المحللة"])
