from __future__ import annotations

import json

import pytest
from b2c_translation.generation import (
    SourceSegment,
    TranslatedSegment,
    translate_segments,
)


class EchoStructuringGateway:
    model_id = "test-model"

    def __init__(self):
        self.calls = 0

    def structured(self, messages, schema, *, prompt_version, temperature=0.0, max_tokens=None):
        self.calls += 1
        payload = json.loads(messages[1]["content"])
        return schema(segments=[
            {"segment_id": item["segment_id"],
             "text": "valeur [[B2C_NUM_1]] avec traduction complète et contexte supplémentaire"}
            for item in payload["segments"]
        ])


class MustNotCallGateway(EchoStructuringGateway):
    def structured(self, *args, **kwargs):
        raise AssertionError("completed checkpoint batches must not call the model")


def test_translation_resumes_from_completed_batches_without_model_calls():
    segments = [
        SourceSegment(segment_id=f"s{i}", text="The value is 5.",
                      citations=[f"book:p{i + 1}"])
        for i in range(9)
    ]
    events = []
    first_gateway = EchoStructuringGateway()
    first = translate_segments(
        segments, first_gateway, source_language="en", target_language="ar",
        progress_callback=events.append,
    )
    saved_batches = [
        event["batch_translations"]
        for event in events
        if event["event"] == "batch_completed"
    ]
    assert first_gateway.calls == 9
    assert len(saved_batches) == 9

    resumed = [
        TranslatedSegment.model_validate(segment)
        for batch in saved_batches
        for segment in batch
    ]
    resumed_events = []
    second = translate_segments(
        segments, MustNotCallGateway(), source_language="en", target_language="ar",
        progress_callback=resumed_events.append, resume_segments=resumed,
    )
    assert [item.model_dump() for item in second.segments] == [
        item.model_dump() for item in first.segments
    ]
    assert len([event for event in resumed_events if event["event"] == "batch_resumed"]) == 9
    assert all(event["completed_segments"] == 9 for event in resumed_events if "completed_segments" in event)


def test_translation_retries_only_segments_missing_from_partial_checkpoint():
    segments = [
        SourceSegment(segment_id=f"s{i}", text="The value is 5.")
        for i in range(9)
    ]
    gateway = EchoStructuringGateway()
    pack = translate_segments(
        segments, gateway, source_language="en", target_language="ar",
        resume_segments=[TranslatedSegment(segment_id="s0", text="valeur 5")],
    )
    assert len(pack.segments) == len(segments)
    assert pack.segments[0].text == "valeur 5"
    assert gateway.calls == 8


def test_noncontiguous_saved_tail_counts_before_first_missing_batch():
    segments=[SourceSegment(segment_id=f"s{i}", text="The value is 5.", citations=[f"book:p{i+1}"]) for i in range(3)]
    gateway=EchoStructuringGateway()
    events=[]
    translate_segments(segments,gateway,source_language="en",target_language="ar",
        resume_segments=[TranslatedSegment(segment_id="s2",text="valeur 5")],progress_callback=events.append)
    started=[event for event in events if event["event"]=="batch_started"]
    assert started[0]["completed_segments"] == 1
    assert started[0]["completed_segment_ids"] == ["s2"]
    assert gateway.calls == 2
    counts=[event["completed_segments"] for event in events if "completed_segments" in event]
    assert counts == sorted(counts)


class SelectiveGateway:
    model_id = "test-model"
    def __init__(self, failing):
        self.failing = set(failing)
        self.ids = []
    def structured(self, messages, schema, **kwargs):
        entries = json.loads(messages[1]["content"])["segments"]
        self.ids.extend(item["segment_id"] for item in entries)
        if any(item["segment_id"] in self.failing for item in entries):
            return schema(segments=[{"segment_id": item["segment_id"], "text": "wrong 6"} for item in entries])
        return schema(segments=[{"segment_id": item["segment_id"], "text": "correct 5"} for item in entries])


def test_exhausted_unit_returns_partial_after_later_units_and_resumes_only_gap():
    sources = [SourceSegment(segment_id=f"s{i}", text="The value is 5.", citations=[f"book:p{i+1}"]) for i in range(3)]
    failing = SelectiveGateway({"s0"})
    events = []
    partial = translate_segments(sources, failing, source_language="en", target_language="ar", progress_callback=events.append)
    assert partial.status == "partial"
    assert partial.total_segments == 3
    assert [x.segment_id for x in partial.segments] == ["s1", "s2"]
    assert partial.missing_segment_ids == partial.failed_segment_ids == ["s0"]
    assert partial.deterministic_checks["all_segments_covered"] is False
    assert failing.ids == ["s0", "s0", "s1", "s2", "s0"]
    assert events[-1]["event"] == "translation_partial"
    success = SelectiveGateway(set())
    complete = translate_segments(sources, success, source_language="en", target_language="ar", resume_segments=partial.segments)
    assert success.ids == ["s0"]
    assert complete.status == "completed" and complete.missing_segment_ids == []
    assert complete.deterministic_checks["all_segments_covered"] is True


def test_all_failed_units_have_explicit_empty_partial_not_false_success():
    partial = translate_segments([SourceSegment(segment_id="s0", text="5")], SelectiveGateway({"s0"}),
                                 source_language="en", target_language="ar")
    assert partial.status == "partial" and partial.segments == []
    assert partial.missing_segment_ids == ["s0"]


@pytest.mark.parametrize("source,target", [
    ("5 5 10.5 20%", "value 5 5 10.5 20%"),
    ("5 5 10.5 20%", "value ٥ ٥ ١٠٫٥ ٢٠%"),
    ("No numbers", "translated text"),
])
def test_exact_raw_numerals_are_valid_without_placeholders(source, target):
    from b2c_translation.generation import _restore_numeric_tokens
    assert _restore_numeric_tokens(target, source).startswith("value" if "5" in source else "translated")


@pytest.mark.parametrize("text", ["5", "5 6", "6 5", "5 5 7", "[[B2C_NUM_X]] 5 5"])
def test_missing_extra_reordered_or_malformed_numeric_values_are_rejected(text):
    from b2c_translation.generation import _restore_numeric_tokens, validate_manual_translation
    with pytest.raises(ValueError):
        validate_manual_translation("5 5", _restore_numeric_tokens(text, "5 5"))
