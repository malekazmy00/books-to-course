"""translate_segments catches only recoverable failures and never repeats an adaptive split."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from b2c_llm_gateway import CloudProviderNotAllowed, InvalidLLMOutput
from b2c_translation.generation import SourceSegment, translate_segments


def _long(sid="long"):
    return SourceSegment(segment_id=sid, text="Measured value 5 requires detailed complete explanation. " * 60,
                         citations=["book:p5"])


class AlwaysInvalidGateway:
    model_id = "fake-invalid"

    def __init__(self):
        self.versions = []

    def structured(self, messages, schema, *, prompt_version, **kwargs):
        self.versions.append(prompt_version)
        raise InvalidLLMOutput("bad", attempts=1, last_output="", errors=[])


def test_long_unit_adaptive_split_is_attempted_once_then_deferred():
    gateway = AlwaysInvalidGateway()
    pack = translate_segments([_long()], gateway, source_language="en", target_language="ar",
                              validate_target_language_script=False)
    assert pack.status == "partial"
    assert pack.failed_segment_ids == ["long"]
    repeated = {version: n for version, n in Counter(gateway.versions).items() if n > 1}
    assert repeated == {}


@pytest.mark.parametrize("error", [CloudProviderNotAllowed("remote endpoint"), TypeError("bug"), KeyError("bug")])
def test_non_recoverable_errors_propagate_instead_of_being_deferred(error):
    class Gateway:
        model_id = "fake"

        def structured(self, *args, **kwargs):
            raise error

    with pytest.raises(type(error)):
        translate_segments([SourceSegment(segment_id="s", text="Short text 1.")], Gateway(),
                           source_language="en", target_language="ar")


def test_validation_failure_is_still_deferred_and_reported():
    class EmptyGateway:
        model_id = "fake"

        def structured(self, messages, schema, *, prompt_version, **kwargs):
            items = json.loads(messages[1]["content"])["segments"]
            return schema(segments=[{"segment_id": item["segment_id"], "text": "x"} for item in items])

    pack = translate_segments([SourceSegment(segment_id="s", text="Value 7 matters.")], EmptyGateway(),
                              source_language="en", target_language="ar")
    assert pack.status == "partial"
    assert pack.failed_segment_ids == ["s"]
    assert "number_checks" in pack.deterministic_checks
