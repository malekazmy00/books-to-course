"""Regressions for source/adapter boundary defects from the strict runtime audit."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import pytest
from b2c_translation.generation import (
    SourceSegment, _attach_page_context, _make_retry_feedback, _number_tokens,
    _protect_translation_tokens, _repair_numeric_marker_sequence, _restore_numeric_tokens, _translation_structure_issue,
    translate_segments,
)
SPEC = importlib.util.spec_from_file_location("strict_hy_server", Path(__file__).resolve().parents[3]/"docker"/"hy-translation"/"server.py")
SERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER)


def test_hy_receives_same_page_context_and_keeps_source_last():
    sources = [SourceSegment(segment_id="a",text="The context describes the spring of a political movement.",citations=["b:p3"]),
               SourceSegment(segment_id="b",text="af t er the spr i ng",citations=["b:p3"])]
    messages = _attach_page_context([{"role":"system","content":"translate"},
        {"role":"user","content":json.dumps({"segments":[sources[1].model_dump()]})}],sources)
    segment=json.loads(messages[1]["content"])["segments"][0]
    prompt=SERVER.build_segment_prompt("ar",segment,{})
    assert sources[0].text in prompt
    assert "Never translate or reproduce the background text itself" in prompt
    assert prompt.endswith("[Source Text]\n"+sources[1].text)
    assert "B2C_NUM" not in prompt and "B2C_CELL" not in prompt and "B2C_LITERAL" not in prompt


def test_hy_marker_inventory_contains_only_markers_in_current_source():
    source="A note [[B2C_NUM_1]] follows a claim."
    prompt=SERVER.build_segment_prompt("ar",{"segment_id":"s","text":source},{})
    assert "B2C_NUM_n" not in prompt and "B2C_CELL" not in prompt and "B2C_LITERAL" not in prompt
    assert "footnote" in prompt and "Keep number words as words" in prompt


def test_retry_feedback_is_before_authoritative_source_and_delimited():
    source=SourceSegment(segment_id="s",text="The first claim 12. The next claim 13.")
    feedback=_make_retry_feedback(source,"ادعاء [[B2C_NUM_2]]","missing_numeric_placeholder",{})
    prompt=SERVER.build_segment_prompt("ar",{"segment_id":"s","text":_protect_translation_tokens(source.text),"retry_feedback":feedback},{})
    assert prompt.index("previous_draft") < prompt.rindex("[Source Text]")
    assert prompt.endswith(_protect_translation_tokens(source.text))
    assert "Restore missing numeric markers" in prompt
    assert feedback["evidence"]["absent_markers"] == ["[[B2C_NUM_1]]"]
    assert feedback["evidence"]["marker_anchors"][0]["value"] == "12"
    assert "The first claim 12" in feedback["evidence"]["marker_anchors"][0]["source_excerpt"]


def test_missing_footnote_is_reported_as_omission_not_order_error():
    with pytest.raises(ValueError,match="missing_numeric_placeholder"):
        _restore_numeric_tokens("[[B2C_NUM_1]] ثم [[B2C_NUM_3]]", "The years 1960 and note 13 precede 1980.")
    with pytest.raises(ValueError,match="numeric_placeholder_order_mismatch"):
        _restore_numeric_tokens("[[B2C_NUM_2]] ثم [[B2C_NUM_1]]", "5 6")


@pytest.mark.parametrize("source,values",[
    ("Events followed uprisings-1848-1849-and their aftermath.",["1848","1849"]),
    ("Revolution-1917 changed the region.",["1917"]),
    ("Actual change was -1848.",["-1848"]),
    ("GPT-2030 ISO-2001 model-1999",[]),
])
def test_hyphen_attached_prose_years_are_not_lost_or_signed(source,values):
    assert _number_tokens(source)==values
    assert _number_tokens(_restore_numeric_tokens(_protect_translation_tokens(source),source))==values


def test_protected_numeric_digit_glyphs_normalize_without_guessing_index():
    assert _restore_numeric_tokens("القيمة [[B2C_NUM_١]]", "Value 5")=="القيمة 5"
    with pytest.raises(ValueError,match="malformed_numeric_placeholder"):
        _restore_numeric_tokens("القيمة [[B2C_NUM_٠١]]", "Value 5")


def test_table_feedback_reports_protected_source_inventory_including_forced_row():
    source=SourceSegment(segment_id="row",text="Ratings Low Moderate")
    feedback=_make_retry_feedback(source,"[[B2C_CELL_2]]","table_cell_placeholder_mismatch",{},table_policy=True)
    assert feedback["evidence"]["source_markers"]==["[[B2C_CELL_1]]","[[B2C_CELL_2]]"]
    prompt=SERVER.build_segment_prompt("ar",{"segment_id":"row","text":_protect_translation_tokens(source.text,table_policy=True),"retry_feedback":feedback},{})
    assert '"source_markers": ["[[B2C_CELL_1]]", "[[B2C_CELL_2]]"]' in prompt


def test_bracket_feedback_counts_actual_bracket_kind_without_transport_markers():
    source=SourceSegment(segment_id="s",text="A (complete) clause with note 5.")
    feedback=_make_retry_feedback(source,"نص (ناقص [[B2C_NUM_1]]","unclosed_target_bracket",{})
    assert feedback["evidence"]["draft_open_brackets"]==0
    assert feedback["evidence"]["bracket_pairs"]==[{"opening":"(","closing":")","source_open":1,"source_close":1,"draft_open":1,"draft_close":0}]
    prompt=SERVER.build_segment_prompt("ar",{"segment_id":"s","text":_protect_translation_tokens(source.text),"retry_feedback":feedback},{})
    assert "square-bracket boundary" not in prompt
    assert '"opening": "("' in prompt


@pytest.mark.parametrize("source,target,reason",[
    ("A source continues [across a page", "نص (ناقص", "unclosed_target_bracket"),
    ("A complete source.", "نص صحيح }", "unexpected_target_closing_bracket"),
    ("A (complete) source.", "نص )معكوس(", "misordered_target_bracket"),
    ("An open source (across a page", "نص يستمر (عبر الصفحة", None),
])
def test_bracket_types_are_independent_and_extra_closes_do_not_pass(source,target,reason):
    assert _translation_structure_issue(source,target)==reason


def test_missing_marker_retry_reaches_hy_with_anchor_and_saves_only_complete_result():
    class Gateway:
        model_id="strict-gate-fake"
        def __init__(self):self.prompts=[]
        def structured(self,messages,schema,**kwargs):
            item=json.loads(messages[1]["content"])["segments"][0]
            self.prompts.append(SERVER.build_segment_prompt("ar",item,{}))
            output="القيمة [[B2C_NUM_2]]" if len(self.prompts)==1 else "القيمة [[B2C_NUM_1]] و[[B2C_NUM_2]]"
            return schema(segments=[{"segment_id":item["segment_id"],"text":output}])
    gateway=Gateway(); events=[]
    pack=translate_segments([SourceSegment(segment_id="s",text="Values 5 and 6.")],gateway,source_language="en",target_language="ar",progress_callback=events.append)
    assert pack.status=="completed" and pack.segments[0].text=="القيمة 5 و6"
    assert len(gateway.prompts)==2
    assert "missing_numeric_placeholder" in gateway.prompts[1]
    assert len([event for event in events if "batch_translations" in event])==1


def test_external_route_system_prompt_does_not_advertise_absent_placeholder_examples():
    class Gateway:
        model_id="external-prompt-test"
        def structured(self,messages,schema,**kwargs):
            assert "B2C_NUM_n" not in messages[0]["content"]
            assert "B2C_CELL_n" not in messages[0]["content"]
            assert "Never invent marker tokens" in messages[0]["content"]
            item=json.loads(messages[1]["content"])["segments"][0]
            return schema(segments=[{"segment_id":item["segment_id"],"text":"قراءات الكتاب"}])
    result=translate_segments([SourceSegment(segment_id="s",text="Book readings")],Gateway(),source_language="en",target_language="ar")
    assert result.status=="completed"


def test_duplicate_numeric_marker_labels_are_repaired_by_source_order_only_when_count_matches():
    source = "The findings list values 12, 13, and 14."
    duplicate_labels = "القيم [[B2C_NUM_1]] و[[B2C_NUM_1]] ثم [[B2C_NUM_3]]"
    repaired = _repair_numeric_marker_sequence(duplicate_labels, source)
    assert repaired == "القيم [[B2C_NUM_1]] و[[B2C_NUM_2]] ثم [[B2C_NUM_3]]"
    assert _restore_numeric_tokens(repaired, source) == "القيم 12 و13 ثم 14"

    extra_duplicate = "القيم [[B2C_NUM_1]] ثم [[B2C_NUM_1]] و[[B2C_NUM_2]]"
    repaired = _repair_numeric_marker_sequence(extra_duplicate, "Values 5 and 8")
    assert _restore_numeric_tokens(repaired, "Values 5 and 8") == "القيم 5 ثم  و8"


def test_missing_numeric_marker_is_not_invented_by_marker_repair():
    source = "The values are 5, 8, and 13."
    incomplete = "القيم [[B2C_NUM_1]] ثم [[B2C_NUM_3]]"
    unchanged = _repair_numeric_marker_sequence(incomplete, source)
    assert unchanged == incomplete
    with pytest.raises(ValueError, match="missing_numeric_placeholder"):
        _restore_numeric_tokens(unchanged, source)


def test_translation_path_repairs_duplicate_placeholder_ids_without_weakening_value_gate():
    source = SourceSegment(segment_id="s", text="The values are 5 and 8.")
    class MarkerGateway:
        model_id = "test-model"
        def structured(self, messages, schema, **kwargs):
            return schema(segments=[{"segment_id": "s", "text": "القيم [[B2C_NUM_1]] ثم [[B2C_NUM_1]]"}])

    result = translate_segments([source], MarkerGateway(), source_language="en", target_language="ar")
    assert result.status == "completed"
    assert result.segments[0].text == "القيم 5 ثم 8"
    assert _number_tokens(result.segments[0].text) == ["5", "8"]
