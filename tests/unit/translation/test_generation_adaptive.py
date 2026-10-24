from __future__ import annotations

import json

import pytest
from b2c_translation.generation import (
    SourceSegment, TranslatedSegment, _number_tokens, _restore_numeric_tokens,
    _split_translation_source, _attach_page_context, _segment_failure_record,
    _RetryableTranslationValidationError, _is_bibliographic_fragment, translate_segments as _translate_segments,
)

def translate_segments(*args, **kwargs):
    # These provider fakes exercise retry/checkpoint mechanics and echo source
    # text; the language gate has its own explicit regression coverage.
    kwargs.setdefault("validate_target_language_script", False)
    return _translate_segments(*args, **kwargs)


@pytest.mark.parametrize("target", [
    "[[B2C_NUM_1]] 5 10.5 [[B2C_NUM_4]]",
    "5 [[B2C_NUM_2]] ١٠٫٥ ٢٠%",
    "5 5 [[B2C_NUM_3]] 20%",
])
def test_mixed_markers_and_exact_raw_values_preserve_full_order(target):
    assert _number_tokens(_restore_numeric_tokens(target, "5 5 10.5 20%")) == ["5", "5", "10.5", "20%"]


@pytest.mark.parametrize("target", [
    "[[B2C_NUM_2]] [[B2C_NUM_1]]",
    "[[B2C_NUM_1]] [[B2C_NUM_1]]",
    "[[B2C_NUM_1]] 6",
    "5 [[B2C_NUM_3]]",
    "5 [[B2C_NUM_X]]",
    "5 [[B2C_NUM_2]] 5",
    "5 [[B2C_NUM_02]]",
])
def test_mixed_missing_duplicate_or_wrong_marker_slots_fail(target):
    with pytest.raises(ValueError):
        _restore_numeric_tokens(target, "5 5")


def test_short_ocr_fragment_gets_same_page_context_without_changing_requested_ids():
    import json
    segments = [
        SourceSegment(segment_id="before", text="The preceding paragraph explains a political movement.", citations=["book:p17"]),
        SourceSegment(segment_id="fragment", text="af t er the spr i ng", citations=["book:p17"]),
        SourceSegment(segment_id="after", text="The following paragraph describes its later consequences.", citations=["book:p17"]),
        SourceSegment(segment_id="other-page", text="Do not leak unrelated page context.", citations=["book:p18"]),
    ]
    messages = [
        {"role": "system", "content": "translate requested segments only"},
        {"role": "user", "content": json.dumps({"segments": [{"segment_id": "fragment", "text": "af t er the spr i ng"}]})},
    ]
    enriched = _attach_page_context(messages, segments)
    payload = json.loads(enriched[1]["content"])
    row = payload["segments"][0]
    assert row["segment_id"] == "fragment"
    assert {item["segment_id"] for item in row["context_segments"]} == {"before", "after"}
    assert all(item["segment_id"] != "other-page" for item in row["context_segments"])


def test_split_keeps_source_characters_and_number_literals():
    source = ("Full sentence preserves measured value 10,500.25% exactly. " * 35)
    left, right = _split_translation_source(source)
    assert left + right == source
    assert _number_tokens(left) + _number_tokens(right) == _number_tokens(source)


class SizeLimitedGateway:
    model_id = "fake-size-limited"

    def __init__(self, always_omit=False):
        self.calls = []
        self.always_omit = always_omit

    def structured(self, messages, schema, *, prompt_version, **kwargs):
        items = json.loads(messages[1]["content"])["segments"]
        self.calls.append((prompt_version, [item["segment_id"] for item in items], [len(item["text"]) for item in items]))
        return schema(segments=[{
            "segment_id": item["segment_id"],
            "text": "omitted" if self.always_omit or len(item["text"]) > 800 else item["text"],
        } for item in items])


def long_source(sid="long"):
    return SourceSegment(segment_id=sid, text="Measured value 5 requires detailed complete explanation. " * 40, citations=["book:p5"])


def test_failed_long_unit_splits_reassembles_and_checkpoints_original_id():
    source = long_source()
    gateway = SizeLimitedGateway()
    events = []
    saved = TranslatedSegment(segment_id="saved", text="Value 9")
    pack = translate_segments([SourceSegment(segment_id="saved", text="Value 9"), source], gateway,
        source_language="en", target_language="ar", resume_segments=[saved], progress_callback=events.append)
    assert pack.status == "completed"
    assert pack.segments[0] == saved
    assert pack.segments[1].segment_id == "long"
    assert _number_tokens(pack.segments[1].text) == _number_tokens(source.text)
    assert all(ids == ["long"] for _, ids, _ in gateway.calls)
    assert len({version for version, _, _ in gateway.calls}) == len(gateway.calls)
    recovery = [e for e in events if e.get("recovery_method") == "adaptive_split"]
    assert len(recovery) == 1
    assert recovery[0]["batch_translations"][0]["segment_id"] == "long"
    assert recovery[0]["completed_segments"] == 2
    counts = [e["completed_segments"] for e in events if "completed_segments" in e]
    assert counts == sorted(counts)


def test_ambiguous_provider_timeout_splits_without_repeating_full_source():
    from b2c_llm_gateway import LLMUnavailable

    class AmbiguousTimeoutGateway:
        model_id = "fake-timeout"
        def __init__(self):
            self.calls = []

        def structured(self, messages, schema, *, prompt_version, **kwargs):
            items = json.loads(messages[1]["content"])["segments"]
            self.calls.extend(len(item["text"]) for item in items)
            if len(items) == 1 and len(items[0]["text"]) > 1200 and len(self.calls) == 1:
                raise LLMUnavailable("gateway timeout", status_code=504, outcome_unknown=True)
            return schema(segments=[
                {"segment_id": item["segment_id"], "text": item["text"]} for item in items
            ])

    source = long_source()
    gateway = AmbiguousTimeoutGateway()
    pack = translate_segments([source], gateway, source_language="en", target_language="ar")
    assert pack.status == "completed"
    assert pack.segments[0].segment_id == source.segment_id
    assert gateway.calls[0] > 1200
    assert max(gateway.calls[1:]) < gateway.calls[0]


def test_english_output_is_rejected_by_default_for_english_to_arabic():
    source = SourceSegment(segment_id="english", text="A complete analytic explanation is required. " * 8)
    pack = _translate_segments([source], SizeLimitedGateway(), source_language="en", target_language="ar")
    assert pack.status == "partial"
    assert pack.failed_segment_ids == ["english"]


def test_real_omissions_in_split_pieces_remain_failed_with_reason():
    gateway = SizeLimitedGateway(always_omit=True)
    events = []
    pack = translate_segments([long_source()], gateway, source_language="en", target_language="ar", progress_callback=events.append)
    assert pack.status == "partial"
    assert pack.segments == []
    assert pack.failed_segment_ids == ["long"]
    failed = next(e for e in events if e["event"] == "batch_failed")
    assert failed["failed_units"][0]["error_reason"] == "numeric_token_mismatch"
    assert failed["failed_units"][0]["segment_id"] == "long"
    assert failed["failed_units"][0]["error_category"] == "validation"
    assert len(gateway.calls) < 20
    assert not any("batch_translations" in e for e in events)


def test_failure_record_keeps_compact_numeric_retry_evidence_without_source_excerpt():
    source = SourceSegment(segment_id="unit", text="The value is 5 at page 9.")
    error = _RetryableTranslationValidationError("numeric_token_mismatch", {
        "unit": {
            "version": 1,
            "reason_code": "numeric_token_mismatch",
            "evidence": {
                "expected_numbers": ["5", "9"],
                "draft_numbers": ["5"],
                "expected_markers": ["[[B2C_NUM_1]]", "[[B2C_NUM_2]]"],
                "draft_markers": ["[[B2C_NUM_1]]"],
                "marker_anchors": [{"source_excerpt": "private source quote"}],
            },
        }
    })
    record = _segment_failure_record(source, error, batch_no=2, attempts=2)
    assert record["retry_feedback_applied"] is True
    assert record["validation_evidence"] == {
        "expected_numbers": ["5", "9"],
        "draft_numbers": ["5"],
        "expected_markers": ["[[B2C_NUM_1]]", "[[B2C_NUM_2]]"],
        "draft_markers": ["[[B2C_NUM_1]]"],
    }
    assert "private source quote" not in repr(record)


def test_non_numeric_omissions_cannot_pass_adaptive_reassembly():
    source = SourceSegment(segment_id="long", text="Every source sentence must be retained in full detail. " * 40)
    gateway = SizeLimitedGateway(always_omit=True)
    events = []
    pack = translate_segments([source], gateway, source_language="en", target_language="ar", progress_callback=events.append)
    assert pack.status == "partial" and pack.segments == []
    assert next(e for e in events if e["event"] == "batch_failed")["last_error_reason"] == "severe_content_omission"


def test_very_long_single_unit_uses_bounded_parts_before_full_request():
    source = SourceSegment(segment_id="long", text="Measured value 5 requires detailed complete explanation. " * 100)
    gateway = SizeLimitedGateway()
    pack = translate_segments([source], gateway, source_language="en", target_language="ar")
    assert pack.status == "completed"
    assert all(":adaptive-" in version for version, _, _ in gateway.calls)
    assert max(size for _, _, sizes in gateway.calls for size in sizes) < 1400
    assert _number_tokens(pack.segments[0].text) == _number_tokens(source.text)


@pytest.mark.parametrize("text", ["و20 وب30 ثم40", "ب٢٠ و٣٠ ثم٤٠"])
def test_arabic_attached_numerals_keep_exact_order(text):
    assert _number_tokens(_restore_numeric_tokens(text, "20 30 40")) == ["20", "30", "40"]


def test_arabic_attached_changed_value_still_fails():
    with pytest.raises(ValueError, match="numeric_token_mismatch"):
        _restore_numeric_tokens("و20 ب31 ثم40", "20 30 40")


def test_english_alphanumeric_identifiers_remain_excluded():
    assert _number_tokens("x20 gene30 model40") == []


def test_short_uppercase_heading_is_translated_separately_before_body():
    source = SourceSegment(segment_id="heading", text="AUDIENCE FOR THIS BOOK\n" + "Every substantive sentence describes the full target audience. " * 25)
    gateway = SizeLimitedGateway()
    pack = translate_segments([source], gateway, source_language="en", target_language="ar")
    assert pack.status == "completed"
    assert gateway.calls[0][2][0] == len("AUDIENCE FOR THIS BOOK\n")
    assert all(":adaptive-" in version for version, _, _ in gateway.calls)
    assert len(pack.segments[0].text) >= len(source.text) * .45


def test_existing_figure_suffix_policy_remains_compatible():
    assert _number_tokens("Figure 5.2b") == _number_tokens("الشكل 5.2ب") == ["5.2"]


def test_maximum_source_size_with_heading_remains_automatically_bounded():
    source = SourceSegment(segment_id="max", text="UPPERCASE HEADING\n" + "Full body sentence requires precise translation. " * 240)
    assert len(source.text) <= 12000
    gateway = SizeLimitedGateway()
    pack = translate_segments([source], gateway, source_language="en", target_language="ar")
    assert pack.status == "completed"
    assert len(pack.segments[0].text) >= len(source.text) * .45
    assert len(gateway.calls) < 100


class CompanionGateway:
    model_id = "fake-companion"
    def __init__(self, fail=True):
        self.fail = fail
        self.calls = []
    def structured(self, messages, schema, **kwargs):
        items = json.loads(messages[1]["content"])["segments"]
        self.calls.append([item["segment_id"] for item in items])
        return schema(segments=[{"segment_id":item["segment_id"],"text":"wrong 6" if self.fail and item["segment_id"].endswith("body") else item["text"]} for item in items])


def test_valid_title_saved_despite_bad_companion_and_retry_only_bad_original():
    sources = [SourceSegment(segment_id="p:title",text="Figure 2.3",citations=["book:p1"]),
               SourceSegment(segment_id="p:body",text="This body has the value 5 and complete context.",citations=["book:p1"])]
    gateway = CompanionGateway()
    events = []
    partial = translate_segments(sources,gateway,source_language="en",target_language="ar",progress_callback=events.append)
    assert partial.status == "partial"
    assert [x.segment_id for x in partial.segments] == ["p:title"]
    assert partial.failed_segment_ids == ["p:body"]
    assert gateway.calls == [["p:title","p:body"],["p:body"],["p:body"]]
    checkpoint = next(e["batch_translations"] for e in events if "batch_translations" in e)
    assert [x["segment_id"] for x in checkpoint] == ["p:title"]
    recovered = CompanionGateway(fail=False)
    full = translate_segments(sources,recovered,source_language="en",target_language="ar",resume_segments=[TranslatedSegment.model_validate(x) for x in checkpoint])
    assert full.status == "completed"
    assert recovered.calls == [["p:body"]]
    assert full.deterministic_checks["number_checks"] == 2


def test_adaptive_heading_fragment_is_never_checkpointed_as_full_original():
    source = SourceSegment(segment_id="p:body",text="Value Added\n"+"This full body requires value 5 to be preserved. "*12,citations=["book:p1"])
    events = []
    partial=translate_segments([source],CompanionGateway(),source_language="en",target_language="ar",progress_callback=events.append)
    assert partial.status == "partial" and partial.segments == []
    assert not any("batch_translations" in event for event in events)


@pytest.mark.parametrize("heading",["Origins of This Technique","Value Added","Relationship to Other Techniques","Back to Figure"])
def test_short_mixed_case_headings_split_before_requests(heading):
    source = SourceSegment(segment_id="small",text=heading+"\n"+"Every source fact requires a complete translation. "*5)
    gateway = SizeLimitedGateway()
    full=translate_segments([source],gateway,source_language="en",target_language="ar")
    assert full.status == "completed"
    assert gateway.calls[0][2][0] == len(heading)+1
    assert all(":adaptive-" in version for version,_,_ in gateway.calls)


def test_wrapped_ordinary_sentence_is_not_misclassified_as_heading():
    from b2c_translation.generation import _leading_heading_split
    assert _leading_heading_split("The analyst asks another question\n"+"while describing the exact steps to follow. "*5) is None
    assert _leading_heading_split("Every Word Starts With A Capital.\n"+"The body continues in long wrapped lines. "*5) is None


@pytest.mark.parametrize("source,target,expected",[
    ("Disinformation21", "التضليل 21", ["21"]),
    ("Figure 6.7a", "الشكل 6.7أ", ["6.7"]),
    ("Figure6.7a", "الشكل 6.7أ", ["6.7"]),
    ("Variable 1increase Event 1increase", "المتغير 1 زيادة الحدث 1 زيادة", ["1","1"]),
    ("early2002 modified2008", "في 2002 عُدّل في 2008", ["2002","2008"]),
])
def test_pdf_joined_prose_numbers_have_exact_full_sequence(source,target,expected):
    from b2c_translation.generation import _protect_numeric_tokens
    assert _number_tokens(source) == expected
    assert _number_tokens(_restore_numeric_tokens(target,source)) == expected
    assert _number_tokens(_protect_numeric_tokens(source)) == []


def test_identifier_and_url_digits_are_excluded():
    assert _number_tokens("Qwen2 Qwen2.5 GPT4 ISO9001 x20 gene30 https://example.com/early2002 www.example.com/model20") == []


def test_joined_prose_number_omission_and_changes_still_rejected():
    with pytest.raises(ValueError,match="numeric_token_mismatch"):
        _restore_numeric_tokens("في 2002", "early2002 modified2008")
    with pytest.raises(ValueError,match="numeric_token_mismatch"):
        _restore_numeric_tokens("الشكل 6.8أ", "Figure 6.7a")


def test_mixed_batch_headed_bodies_are_isolated_before_any_combined_request():
    sources=[SourceSegment(segment_id="p:title",text="Value Added",citations=["book:p1"]),
             SourceSegment(segment_id="p:body",text="Value Added\n"+"Every substantive source sentence is required. "*8,citations=["book:p1"])]
    gateway=CompanionGateway(fail=False)
    pack=translate_segments(sources,gateway,source_language="en",target_language="ar")
    assert pack.status == "completed"
    assert gateway.calls[:2] == [["p:body"],["p:body"]]
    assert ["p:title","p:body"] not in gateway.calls



def test_per_item_checkpoint_storage_failure_propagates_instead_of_false_partial():
    from b2c_translation.generation import _TranslationCheckpointError
    sources=[SourceSegment(segment_id="p:title",text="Figure 2.3",citations=["book:p1"]),
             SourceSegment(segment_id="p:body",text="Body value 5 requires complete translation.",citations=["book:p1"])]
    def failing_storage(event):
        if "batch_translations" in event:
            raise OSError("simulated storage failure")
    with pytest.raises(_TranslationCheckpointError,match="translation_checkpoint_write_failed"):
        translate_segments(sources,CompanionGateway(),source_language="en",target_language="ar",progress_callback=failing_storage)


@pytest.mark.parametrize("source,target,expected",[
    ("techniques12", "التقنيات 12", ["12"]),
    ("to100", "إلى 100", ["100"]),
    ("and2009", "وفي 2009", ["2009"]),
    ("each2-×-2", "كل 2×2", ["2","2"]),
    ("Actual subtraction -3", "طرح -3", ["-3"]),
])
def test_additional_reviewed_joined_tokens_and_matrix_dimensions(source,target,expected):
    assert _number_tokens(source) == expected
    assert _number_tokens(_restore_numeric_tokens(target,source)) == expected


def test_long_model_identifiers_remain_excluded():
    assert _number_tokens("EmbeddingGemma2 embeddinggemma2 DeepSeekV4 qwen2 GPT4 bertbase12 ISO9001") == []


def test_introduced_html_and_provenance_are_rejected():
    from b2c_translation.generation import validate_manual_translation
    with pytest.raises(ValueError,match="introduced_markup"):
        validate_manual_translation("Content in 2009", "نص في 2009 </p><p class=citation>[book-main:p565]</p>")
    with pytest.raises(ValueError,match="introduced_provenance"):
        validate_manual_translation("Content in 2009", "نص في 2009 [book-main:p565]")


def quarter_table():
    return "2008 2009\nQuarter 1 Quarter 2 Quarter 3 Quarter 4\nOrganizational capabilities Low Negligible Negligible Negligible Negligible Negligible\nEconomic strength Low Low Moderate Substantial Low Low\nMilitary strength Low Low Moderate Low Low Moderate\n"


def test_quarter_table_restores_every_cell_in_order_and_rejects_duplication():
    from b2c_translation.generation import _protect_translation_tokens,_restore_table_tokens,_table_cells
    source = quarter_table()
    protected = _protect_translation_tokens(source)
    restored = _restore_table_tokens(_restore_numeric_tokens(protected,source),source)
    assert len(_table_cells(source)) == 18
    with pytest.raises(ValueError,match="table_cell_sequence_mismatch"):
        _restore_table_tokens(restored+" ضئيل",source)
    with pytest.raises(ValueError,match="table_cell_sequence_mismatch"):
        _restore_table_tokens(restored.replace("ضئيل", "", 1),source)
    with pytest.raises(ValueError,match="table_cell_placeholder_mismatch"):
        _restore_table_tokens(protected.replace("[[B2C_CELL_2]]", "[[B2C_CELL_1]]"),source)


def test_compound_cells_longest_match_and_glued_cells_have_exact_counts():
    from b2c_translation.generation import _table_cells
    assert _table_cells("Strongpositiveandstrongnegative Mediumpositiveandmediumnegative Weakpositiveandweaknegative Nil") == ["strong positive and strong negative","medium positive and medium negative","weak positive and weak negative","nil"]
    assert _table_cells("LowLowNegligibleNegligible") == ["low","low","negligible","negligible"]
    assert _table_cells("allow strongpoint") == []


@pytest.mark.parametrize("source",[
    "A B C D E F G H I\nF Strongnegative Mediumnegative Mediumnegative Mediumnegative Strongnegative\nStrongpositiveandstrongnegative",
    "J Lack of openness to change among senior analysts\n3Mediumnegative\nMediumnegative\nMediumnegative Mpamn",
    "G Change in client preferences\nMediumpositive Weakpositive Mediumpositiveandmediumnegative\nH Capabilities\nStrongpositiveandstrongnegative Nil Mediumnegative Mpamn",
])
def test_short_rating_fragments_complete_without_losing_cells_or_unknown_tokens(source):
    from b2c_translation.generation import _recognized_rating_table,_target_table_cells,_table_cells
    assert _recognized_rating_table(source)
    gateway=CompanionGateway(fail=False)
    events=[]
    pack=translate_segments([SourceSegment(segment_id="table",text=source)],gateway,source_language="en",target_language="ar",progress_callback=events.append)
    assert pack.status == "completed"
    assert _target_table_cells(pack.segments[0].text) == _table_cells(source)
    assert "Mpamn" in pack.segments[0].text if "Mpamn" in source else True
    if "Mpamn" in source:
        assert pack.deterministic_checks["unclear_table_tokens"] == 1
        assert events[0]["source_warnings"][0]["reason"] == "unclear_table_token_preserved_verbatim"
    assert all(len(ids)==1 for ids in gateway.calls)


def test_rating_table_rows_never_commit_before_complete_original():
    class OmitRowGateway(CompanionGateway):
        def structured(self,messages,schema,**kwargs):
            items=json.loads(messages[1]["content"])["segments"]
            return schema(segments=[{"segment_id":item["segment_id"],"text":"omitted"} for item in items])
    events=[]
    pack=translate_segments([SourceSegment(segment_id="table",text=quarter_table())],OmitRowGateway(),source_language="en",target_language="ar",progress_callback=events.append)
    assert pack.status == "partial" and pack.segments == []
    assert not any("batch_translations" in event for event in events)


def test_saved_duplicate_table_cell_is_retried_and_not_accepted():
    from b2c_translation.generation import _protect_translation_tokens,_restore_table_tokens
    source=quarter_table()
    valid=_restore_table_tokens(_restore_numeric_tokens(_protect_translation_tokens(source),source),source)
    gateway=CompanionGateway(fail=False)
    pack=translate_segments([SourceSegment(segment_id="table",text=source)],gateway,source_language="en",target_language="ar",resume_segments=[TranslatedSegment(segment_id="table",text=valid+" ضئيل")])
    assert gateway.calls
    assert pack.status == "completed"
    assert not pack.segments[0].text.endswith("ضئيل")



def test_partial_checkpoint_event_does_not_claim_completed_batch():
    sources=[SourceSegment(segment_id="p:title",text="Figure 2.3",citations=["book:p1"]),
             SourceSegment(segment_id="p:body",text="Body value 5 requires full context.",citations=["book:p1"])]
    events=[]
    pack=translate_segments(sources,CompanionGateway(),source_language="en",target_language="ar",progress_callback=events.append)
    assert pack.status == "partial"
    checkpoints=[event for event in events if "batch_translations" in event]
    assert checkpoints and all(event["event"] == "batch_partial_completed" for event in checkpoints)


def test_public_completeness_helper_flags_short_fragment_and_existing_bad_table():
    from b2c_translation.generation import translation_completeness_issue,_protect_translation_tokens,_restore_table_tokens
    fragment="A B C D E F G H I\nFStrongnegativeMediumnegativeMediumnegativeMediumnegativeStrongnegative\nStrongpositiveandstrongnegative"
    assert translation_completeness_issue(fragment,"عنوان قصير")["reason"] == "table_cell_sequence_mismatch"
    source=quarter_table()
    valid=_restore_table_tokens(_restore_numeric_tokens(_protect_translation_tokens(source),source),source)
    assert translation_completeness_issue(source,valid) is None
    assert translation_completeness_issue(source,valid+" ضئيل")["reason"] == "table_cell_sequence_mismatch"
    assert translation_completeness_issue("source","source </p>")["reason"] == "introduced_markup"


def test_joined_quarter_headers_and_label_to_cell_spacing_are_recognized():
    from b2c_translation.generation import _recognized_rating_table,_table_cells
    source=quarter_table().replace("Quarter 1 Quarter 2 Quarter 3 Quarter 4","1stquarter 2ndquarter 3rdquarter 4thquarter").replace("capabilities Low", "capabilitiesLow")
    assert _recognized_rating_table(source)
    assert len(_table_cells(source)) == 18



def test_real_camel_case_quarter_header_recognizes_rating_table():
    from b2c_translation.generation import _recognized_rating_table
    source=quarter_table().replace("Quarter 1 Quarter 2 Quarter 3 Quarter 4","FirstQuarterSecondQuarterThirdQuarterFourthQuarterFirstQuarterSecondQuarter")
    assert _recognized_rating_table(source)


def test_clipped_compound_cell_is_preserved_as_unknown_not_partially_translated():
    from b2c_translation.generation import _table_cells,_protect_translation_tokens,_restore_table_tokens
    source="A B C D\nF Mediumpositiveandmediumnegativ Weakpositiveandweaknegativ Weaknegativ\nNil Strongpositive Mediumpositive"
    assert _table_cells(source) == ["nil","strong positive","medium positive"]
    protected=_protect_translation_tokens(source)
    assert protected.count("B2C_LITERAL_") == 3
    text=_restore_table_tokens(_restore_numeric_tokens(protected,source),source)
    assert "Mediumpositiveandmediumnegativ" in text and "Weakpositiveandweaknegativ" in text and "Weaknegativ" in text
    assert _table_cells(text) == []


def test_legacy_negligible_aliases_preserve_exact_cell_identity_and_count():
    from b2c_translation.generation import _target_table_cells
    assert _target_table_cells("منخفض مهمل مهملة ضئيل لا يُذكر منخفضة") == ["low","negligible","negligible","negligible","negligible","low"]



def test_joined_arabic_legacy_cells_and_gender_forms_preserve_counts():
    from b2c_translation.generation import _target_table_cells
    assert _target_table_cells("منخفضمهملمهملةمنخفضةمتوسطكبيرة") == ["low","negligible","negligible","low","moderate","substantial"]



def test_joined_is2030_and_actual_unclosed_truncation_are_rejected():
    from b2c_translation.generation import translation_completeness_issue
    source="The forecast is2030 and the final paragraph is complete. "*8
    assert _number_tokens(source) == ["2030"]*8
    with pytest.raises(ValueError,match="numeric_token_mismatch"):
        _restore_numeric_tokens("The forecast is omitted (",source)
    assert translation_completeness_issue("Balanced source (complete) words", "نص عربي ناقص (")["reason"] == "unclosed_target_bracket"
    assert translation_completeness_issue("Source continues (across a PDF boundary", "النص يستمر (في الصفحة التالية") is None



def test_public_quality_helper_flags_numeric_mismatch_for_status_readers():
    from b2c_translation.generation import translation_completeness_issue
    assert translation_completeness_issue("The value is 5", "القيمة 6")["reason"] == "numeric_token_mismatch"
    assert translation_completeness_issue("Forecast is2030", "التوقع 2030") is None
    assert translation_completeness_issue("Forecast is2030", "التوقع")["reason"] == "numeric_token_mismatch"


def test_joined_figure_footnote_after_short_titlecase_word_is_preserved():
    from b2c_translation.generation import _number_tokens, _protect_numeric_tokens
    source = "Figure 8.2.5 High Impact/Low Probability Scenario: Conflict in the Arctic20\nSource: This example was developed by Pherson Associates, LLC."
    assert _number_tokens(source) == ["8.2.5", "20"]
    protected = _protect_numeric_tokens(source)
    assert "Arctic[[B2C_NUM_2]]" in protected


def test_note_number_on_line_after_url_is_protected():
    from b2c_translation.generation import _protect_numeric_tokens
    source = "15. Reference https://example.org/item/3568.html\n16. Next note\n17. Final note"
    assert _number_tokens(source) == ["15", "16", "17"]
    protected = _protect_numeric_tokens(source)
    assert "[[B2C_NUM_2]]. Next note" in protected
    assert _restore_numeric_tokens(protected, source) == source


def test_wrapped_url_and_note_marker_have_stable_final_slots():
    source = "1. Reference\nhttps://example.org/document\n2. Another note 2001."
    target = "[[B2C_NUM_1]]. المرجع https://example.org/document\n[[B2C_NUM_2]]. مرجع آخر [[B2C_NUM_3]]."
    assert _number_tokens(_restore_numeric_tokens(target, source)) == ["1", "2", "2001"]


def test_note_number_after_url_cannot_be_omitted():
    source = "1. https://example.org/item/3568.html\n2. Next note 2001"
    with pytest.raises(ValueError):
        _restore_numeric_tokens("[[B2C_NUM_1]]. https://example.org/item/3568.html مرجع [[B2C_NUM_3]]", source)


def test_url_wrap_reflow_does_not_change_numeric_validation():
    source = "20. Note 2019 https://example.org/five-myths-\nabout/2019/01/17/0ef1b840-\nf9f77a3_story\n21. Next note 2001"
    target = "[[B2C_NUM_1]]. ملاحظة [[B2C_NUM_2]] https://example.org/five-myths-about/2019/01/17/0ef1b840-f9f77a3_story\n[[B2C_NUM_3]]. التالي [[B2C_NUM_4]]"
    assert _number_tokens(source) == ["20", "2019", "21", "2001"]
    assert _number_tokens(_restore_numeric_tokens(target, source)) == ["20", "2019", "21", "2001"]


def test_wrapped_url_numeric_continuation_is_excluded_but_next_note_is_not():
    source = "4. https://example.org/wiki/Flowchart#cite_note-\n16\n5. Note 1921"
    assert _number_tokens(source) == ["4", "5", "1921"]


def test_slash_url_does_not_swallow_next_numbered_note():
    assert _number_tokens("1. https://example.org/\n2. Next 2001") == ["1", "2", "2001"]


def test_adjacent_numeric_markers_do_not_merge_separate_footnote_values():
    assert _restore_numeric_tokens("النظام [[B2C_NUM_1]].[[B2C_NUM_2]]", "System 1 Thinking.2") == "النظام 1. 2"
    assert _number_tokens(_restore_numeric_tokens("[[B2C_NUM_1]],[[B2C_NUM_2]]", "11, 2001")) == ["11", "2001"]


def test_article_a_and_repeated_low_in_prose_are_not_a_rating_table():
    from b2c_translation.generation import _recognized_rating_table, _protect_translation_tokens
    source = "A High Impact/Low Probability study is useful.\nAnalysts compare low probability events.\nAnother low probability scenario remains possible."
    assert not _recognized_rating_table(source)
    assert "B2C_CELL" not in _protect_translation_tokens(source)


@pytest.mark.parametrize("source,target,values", [
    ("Chapters 3-6 cover four phases.", "تتناول الفصول 3 إلى 6 أربع مراحل.", ["3", "6"]),
    ("What changed among post-1968 youth?", "ما الذي تغير لدى الشباب بعد 1968؟", ["1968"]),
    ("The measured change was -6 and +3.", "كان التغير المقاس -6 و +3.", ["-6", "+3"]),
])
def test_numeric_ranges_and_temporal_hyphens_preserve_values(source, target, values):
    assert _number_tokens(source) == values
    assert _number_tokens(target) == values
    assert _restore_numeric_tokens(target, source) == target


def test_short_omitted_list_recovers_in_complete_checkpoint():
    class ShortListGateway:
        model_id = "short-list-test"
        def __init__(self):
            self.calls = []
        def structured(self, messages, schema, **kwargs):
            items = json.loads(messages[1]["content"])["segments"]
            self.calls.extend(x["text"] for x in items)
            return schema(segments=[{"segment_id": x["segment_id"],
                "text": "omitted" if len(x["text"]) > 130 else x["text"]} for x in items])
    text = "Forms of death, ossification, rigidification, mummification, morbid phenomena, becoming an anachronism, disaggregation into constituent parts, severing of connections and bonds, dissipation, and decay."
    gateway = ShortListGateway()
    events = []
    pack = translate_segments([SourceSegment(segment_id="list",text=text)], gateway,
        source_language="en",target_language="ar",progress_callback=events.append)
    assert pack.status == "completed"
    assert " ".join(pack.segments[0].text.split()) == " ".join(text.split())
    assert any(len(x) <= 130 for x in gateway.calls)
    checkpoints = [e for e in events if "batch_translations" in e]
    assert all(" ".join(e["batch_translations"][0]["text"].split()) == " ".join(text.split()) for e in checkpoints)

@pytest.mark.parametrize("event_name", ["batch_partial_completed", "batch_completed"])
def test_safe_pause_escapes_per_item_and_outer_retry_without_another_request(event_name):
    from temporalio.exceptions import ApplicationError
    class CompleteGateway:
        model_id = "pause-test"
        def __init__(self): self.calls = 0
        def structured(self, messages, schema, **kwargs):
            self.calls += 1
            payload = json.loads(messages[1]["content"])
            return schema(segments=[{"segment_id": s["segment_id"], "text": "نص عربي محفوظ"} for s in payload["segments"]])
    gateway = CompleteGateway()
    def checkpoint(event):
        if event.get("event") == event_name:
            raise ApplicationError("paused after durable checkpoint", type="StageCheckpointPaused", non_retryable=True)
    sources = [SourceSegment(segment_id="m1.l1:lesson-qa:1:question", text="An original source."), SourceSegment(segment_id="m1.l1:lesson-qa:1:answer", text="Another original source.")]
    with pytest.raises(ApplicationError) as caught:
        translate_segments(sources, gateway, source_language="en", target_language="ar", progress_callback=checkpoint)
    assert caught.value.type == "StageCheckpointPaused"
    assert gateway.calls == 1


class TargetedFeedbackGateway:
    model_id = "fake-targeted-feedback"

    def __init__(self, *, always_fail=False):
        self.calls = []
        self.always_fail = always_fail

    def structured(self, messages, schema, **kwargs):
        payload = json.loads(messages[1]["content"])
        items = payload["segments"]
        self.calls.append(payload)
        rendered = []
        for item in items:
            feedback = item.get("retry_feedback")
            if item["segment_id"] == "p:body" and (len(self.calls) == 1 or self.always_fail):
                rendered.append({"segment_id": item["segment_id"], "text": "القيمة [[B2C_NUM_2]] ناقصة"})
            else:
                if item["segment_id"] == "p:body" and feedback:
                    assert feedback["reason_code"] in {"numeric_placeholder_order_mismatch", "malformed_numeric_placeholder", "unexpected_numeric_placeholder"}
                    assert feedback["previous_draft"]
                rendered.append({"segment_id": item["segment_id"],
                                 "text": "القيمة [[B2C_NUM_1]] مكتملة" if item["segment_id"] == "p:body" else "عنوان صحيح"})
        return schema(segments=rendered)


def test_retry_feedback_is_segment_scoped_and_preserves_successful_companion():
    sources = [
        SourceSegment(segment_id="p:title", text="A short title", citations=["book:p1"]),
        SourceSegment(segment_id="p:body", text="The body contains the exact value 5.", citations=["book:p1"]),
    ]
    gateway = TargetedFeedbackGateway()
    pack = translate_segments(sources, gateway, source_language="en", target_language="ar")
    assert pack.status == "completed"
    assert gateway.calls[0]["segments"][0]["segment_id"] == "p:body" or gateway.calls[0]["segments"][1]["segment_id"] == "p:body"
    retry = gateway.calls[1]["segments"]
    assert [segment["segment_id"] for segment in retry] == ["p:body"]
    feedback = retry[0]["retry_feedback"]
    assert feedback["reason_code"] in {"numeric_placeholder_order_mismatch", "malformed_numeric_placeholder", "unexpected_numeric_placeholder"}
    assert feedback["evidence"]["expected_numbers"] == ["5"]
    assert feedback["evidence"]["expected_markers"] == ["[[B2C_NUM_1]]"]
    assert feedback["previous_draft"] == "القيمة [[B2C_NUM_2]] ناقصة"
    assert [item.segment_id for item in pack.segments] == ["p:title", "p:body"]


def test_deferred_retry_carries_the_observed_validation_reason():
    source = SourceSegment(segment_id="p:body", text="The body contains the exact value 5.", citations=["book:p1"])
    gateway = TargetedFeedbackGateway(always_fail=True)
    pack = translate_segments([source], gateway, source_language="en", target_language="ar")
    assert pack.status == "partial"
    assert len(gateway.calls) == 3
    deferred_feedback = gateway.calls[-1]["segments"][0]["retry_feedback"]
    assert deferred_feedback["reason_code"] in {"numeric_placeholder_order_mismatch", "malformed_numeric_placeholder", "unexpected_numeric_placeholder"}
    assert deferred_feedback["previous_draft"] == "القيمة [[B2C_NUM_2]] ناقصة"


def test_retry_feedback_keeps_reason_specific_evidence_bounded_and_typed():
    from b2c_translation.generation import _make_retry_feedback
    source = SourceSegment(segment_id="term", text="Intelligence is assessed as a discipline.")
    feedback = _make_retry_feedback(source, "الحكمة تُقيّم كحقل.", "terminology_mismatch",
                                   {"Intelligence": "استخبارات"})
    assert feedback["reason_code"] == "terminology_mismatch"
    assert feedback["evidence"]["required_terms"] == [{"source": "Intelligence", "target": "استخبارات"}]
    assert len(feedback["previous_draft"]) <= 6000


@pytest.mark.parametrize("text", [
    "Ibid.",
    "Gill, 'Postmodern Prince'.",
    "Gurr, Why Men Rebel.",
    "Cited in Philip Roberts, Political Change.",
])
def test_bibliographic_fragments_are_recognized_and_kept_out_of_page_context(text):
    assert _is_bibliographic_fragment(text)
    segments = [
        SourceSegment(segment_id="ref", text=text, citations=["book:p347"]),
        SourceSegment(segment_id="neighbor", text="A long nearby paragraph should not leak into a citation.", citations=["book:p347"]),
    ]
    messages = [
        {"role": "system", "content": "translate"},
        {"role": "user", "content": json.dumps({"segments": [{"segment_id": "ref", "text": text}]})},
    ]
    enriched = _attach_page_context(messages, segments)
    assert "context_segments" not in json.loads(enriched[1]["content"])["segments"][0]


def test_ordinary_short_phrase_is_not_classified_as_bibliography():
    assert not _is_bibliographic_fragment("The political system changed rapidly.")


def test_exhausted_bibliography_retry_preserves_exact_source_and_checkpoints_it():
    class BadReferenceGateway:
        model_id = "fake-reference-gateway"

        def structured(self, messages, schema, *, prompt_version, **kwargs):
            requested = json.loads(messages[1]["content"])["segments"]
            return schema(segments=[{"segment_id": item["segment_id"], "text": "999"} for item in requested])

    source = SourceSegment(segment_id="ref", text="Gurr, Why Men Rebel 3.", citations=["book:p349"])
    events = []
    pack = translate_segments([source], BadReferenceGateway(), source_language="en", target_language="ar",
                              progress_callback=events.append)
    assert pack.status == "completed"
    assert pack.segments == [TranslatedSegment(segment_id="ref", text=source.text)]
    assert pack.deterministic_checks["source_preserved_references"] == 1
    saved = [event for event in events if event.get("recovery_method") == "source_preserved_reference"]
    assert len(saved) == 1
    assert saved[0]["batch_translations"][0]["text"] == source.text
