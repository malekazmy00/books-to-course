"""PlainMTEngine: translation-only models get their documented template and nothing else."""
import pytest
from b2c_contracts import EngineIdentity
from b2c_translation.engines.base import EngineCapabilities, EngineRequest, RepairRequest
from b2c_translation.engines.mt import PlainMTEngine

IDENTITY = EngineIdentity(provider="hy-translation", model_id="tencent/Hy-MT2-1.8B",
                          revision="9a341cd1b679d3efd23b46e847b01745a71ed792", prompt_version="unset")
PLAIN = EngineCapabilities(follows_instructions=False, supports_terms=False, supports_context=False,
                           supports_repair=False, max_source_chars=400)
TERMS = EngineCapabilities(False, True, False, False, 400)
CONTEXT = EngineCapabilities(False, False, True, False, 400)
BOTH = EngineCapabilities(False, True, True, False, 400)
SOURCE = "Intelligence analysis began in 1947."
DEFAULT = ("Translate the following text into Arabic. Note that you should only output the "
           "translated result without any additional explanation:\n\n" + SOURCE)


def request(unit, **kw):
    return EngineRequest(unit=unit, source_language="en", target_language="ar", **kw)


def test_default_template_is_the_only_message(gateway, make_unit):
    result = PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2").translate(request(make_unit(SOURCE)))
    call = gateway.calls[0]
    assert call["messages"] == [{"role": "user", "content": DEFAULT}]
    assert call["prompt_version"] == "hy-mt2.default@v1" == result.identity.prompt_version
    assert call["temperature"] == 0.0
    assert result.identity.model_id == "tencent/Hy-MT2-1.8B"
    assert result.identity.revision == "9a341cd1b679d3efd23b46e847b01745a71ed792"
    assert result.identity.provider == "hy-translation"


def test_terms_and_context_are_ignored_without_capability(gateway, make_unit):
    PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2").translate(request(
        make_unit(SOURCE), terms={"Intelligence analysis": "تحليل الاستخبارات"},
        context_before="Before.", context_after="After."))
    assert gateway.calls[0]["messages"] == [{"role": "user", "content": DEFAULT}]


def test_documented_terminology_template_when_supported(gateway, make_unit):
    result = PlainMTEngine(gateway, IDENTITY, TERMS, "hy-mt2").translate(request(
        make_unit(SOURCE), terms={"Intelligence analysis": "تحليل الاستخبارات", "covert action": "العمل السري"}))
    assert gateway.calls[0]["messages"] == [{"role": "user", "content": (
        "Reference the following translations:\nIntelligence analysis translates to تحليل الاستخبارات\n\n"
        "Translate the following text into Arabic. Note that you must ONLY output the translated result "
        "without any additional explanation:\n\n" + SOURCE)}]
    assert result.identity.prompt_version == "hy-mt2.terminology@v1+hy-mt2.terminology_line@v1"


def test_terms_absent_from_unit_fall_back_to_default(gateway, make_unit):
    PlainMTEngine(gateway, IDENTITY, TERMS, "hy-mt2").translate(request(
        make_unit(SOURCE), terms={"covert action": "العمل السري"}))
    assert gateway.calls[0]["messages"][0]["content"] == DEFAULT


def test_documented_context_template_when_supported(gateway, make_unit):
    result = PlainMTEngine(gateway, IDENTITY, CONTEXT, "hy-mt2").translate(request(
        make_unit(SOURCE), context_before="Before.", context_after="After."))
    assert gateway.calls[0]["messages"] == [{"role": "user", "content": (
        "[Background Information]\nBefore.\n\nAfter.\n\nPlease translate the following text into Arabic, "
        "taking the provided background information into consideration.\n\n[Source Text]\n" + SOURCE)}]
    assert result.identity.prompt_version == "hy-mt2.context@v1"


def test_terms_take_precedence_over_context_no_undocumented_combination(gateway, make_unit):
    PlainMTEngine(gateway, IDENTITY, BOTH, "hy-mt2").translate(request(
        make_unit(SOURCE), terms={"Intelligence analysis": "تحليل الاستخبارات"}, context_before="Before."))
    content = gateway.calls[0]["messages"][0]["content"]
    assert content.startswith("Reference the following translations:") and "Before." not in content


def test_repair_is_not_supported_by_plain_mt(gateway, make_unit):
    with pytest.raises(ValueError):
        PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2").translate(request(
            make_unit(SOURCE), repair=RepairRequest(previous_target="x", issues=[])))
    assert gateway.calls == []


def test_unknown_template_family_and_oversized_units_are_rejected(gateway, make_unit):
    with pytest.raises(ValueError):
        PlainMTEngine(gateway, IDENTITY, PLAIN, "made-up-mt")
    with pytest.raises(ValueError):
        PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2").translate(request(make_unit("x" * 401)))


def test_source_with_braces_is_inserted_verbatim_and_output_is_plain_text(gateway, make_unit):
    gateway.replies.append(("  النص {x}  \n", "stop"))
    result = PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2").translate(request(make_unit("text {x} {}")))
    assert gateway.calls[0]["messages"][0]["content"].endswith("\n\ntext {x} {}")
    assert result.text == "النص {x}"


def test_identity_property(gateway):
    engine = PlainMTEngine(gateway, IDENTITY, PLAIN, "hy-mt2")
    assert engine.identity.prompt_version == "hy-mt2.default@v1"
    assert engine.capabilities is PLAIN
