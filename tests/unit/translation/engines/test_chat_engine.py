"""ChatEngine: instructions in the system message, the user message is the bare source text."""
import json

import pytest
from b2c_contracts import EngineIdentity, QAIssue
from b2c_translation.engines.base import EngineCapabilities, EngineRequest, RepairRequest
from b2c_translation.engines.chat import ChatEngine, clean_output
from b2c_translation.prompts import get_template

IDENTITY = EngineIdentity(provider="llama.cpp", model_id="qwen-test", revision="sha-abc",
                          prompt_version="unset")
FULL = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=2000)
BARE = EngineCapabilities(follows_instructions=True, supports_terms=False, supports_context=False,
                          supports_repair=False, max_source_chars=2000)
SOURCE = "Intelligence analysis began in 1947 with the CIA."


def request(unit, **kw):
    return EngineRequest(unit=unit, source_language="en", target_language="ar", **kw)


def test_user_message_is_exactly_the_source_text(gateway, make_unit):
    engine = ChatEngine(gateway, IDENTITY, FULL)
    engine.translate(request(make_unit(SOURCE), terms={"intelligence analysis": "تحليل الاستخبارات"},
                             context_before="Previous paragraph.", context_after="Next one."))
    messages = gateway.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == SOURCE


def test_system_message_carries_instructions_register_and_language(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE)))
    system = gateway.calls[0]["messages"][0]["content"]
    assert "from English into Arabic" in system
    assert "Modern Standard Arabic" in system
    assert "do not add, omit" in system
    assert "Keep every number" in system
    assert "no notes" in system
    assert SOURCE not in system


def test_only_locked_terms_occurring_in_the_unit_are_sent(gateway, make_unit):
    terms = {"Intelligence analysis": "تحليل الاستخبارات", "covert action": "العمل السري"}
    ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE), terms=terms))
    system = gateway.calls[0]["messages"][0]["content"]
    assert "- Intelligence analysis => تحليل الاستخبارات" in system
    assert "covert action" not in system


def test_terms_and_context_are_omitted_when_not_supported(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, BARE).translate(request(
        make_unit(SOURCE), terms={"Intelligence analysis": "تحليل الاستخبارات"},
        context_before="Before text.", context_after="After text."))
    call = gateway.calls[0]
    system = call["messages"][0]["content"]
    assert "تحليل الاستخبارات" not in system and "Before text." not in system
    assert "chat.terms" not in call["prompt_version"] and "chat.context" not in call["prompt_version"]


def test_context_is_marked_not_to_translate_and_never_in_user_turn(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, FULL).translate(request(
        make_unit(SOURCE), context_before="Before text.", context_after="After text."))
    system, user = (m["content"] for m in gateway.calls[0]["messages"])
    assert "[Preceding text - do not translate]\nBefore text." in system
    assert "[Following text - do not translate]\nAfter text." in system
    assert "NOT part of the text to translate" in system
    assert user == SOURCE


def test_repair_conversation_and_typed_issues(gateway, make_unit):
    issue = QAIssue(check_id="numbers.multiset", category="number_error", severity="major",
                    origin="deterministic", evidence={"missing": ["1947"]})
    engine = ChatEngine(gateway, IDENTITY, FULL)
    result = engine.translate(request(make_unit(SOURCE), repair=RepairRequest(
        previous_target="بدأ تحليل الاستخبارات مع وكالة المخابرات المركزية.", issues=[issue])))
    messages = gateway.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == SOURCE
    assert messages[2]["content"] == "بدأ تحليل الاستخبارات مع وكالة المخابرات المركزية."
    assert messages[3]["content"] == get_template("chat.repair.request").text
    system = messages[0]["content"]
    assert "number_error (major)" in system
    assert get_template("chat.repair.fix.number_error").text in system
    assert json.dumps({"missing": ["1947"]}, ensure_ascii=False) in system
    assert "chat.repair.system@v1" in result.identity.prompt_version
    assert "chat.repair.request@v1" in result.identity.prompt_version


def test_repair_rejected_when_engine_cannot_repair(gateway, make_unit):
    with pytest.raises(ValueError):
        ChatEngine(gateway, IDENTITY, BARE).translate(request(
            make_unit(SOURCE), repair=RepairRequest(previous_target="x", issues=[])))
    assert gateway.calls == []


def test_result_has_full_identity_and_gateway_metadata(gateway, make_unit):
    gateway.replies.append(("بدأ تحليل الاستخبارات عام 1947.", "stop"))
    result = ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE)))
    call = gateway.calls[0]
    assert result.text == "بدأ تحليل الاستخبارات عام 1947."
    assert result.identity.provider == "llama.cpp" and result.identity.model_id == "qwen-test"
    assert result.identity.revision == "sha-abc"
    assert result.identity.prompt_version == call["prompt_version"]
    assert result.identity.prompt_version.startswith("chat.translate.system@v1+chat.register.ar@v1")
    assert result.finish_reason == "stop" and result.usage["output_tokens"] == 5
    assert call["temperature"] == 0.0 and 0 < call["max_tokens"] <= 4096


def test_identity_property_reports_engine_prompt_set(gateway):
    engine = ChatEngine(gateway, IDENTITY, FULL)
    assert engine.identity.prompt_version.startswith("chat.translate.system@v1")
    assert engine.capabilities is FULL


def test_units_longer_than_capability_are_rejected(gateway, make_unit):
    small = EngineCapabilities(True, True, True, True, max_source_chars=10)
    with pytest.raises(ValueError):
        ChatEngine(gateway, IDENTITY, small).translate(request(make_unit(SOURCE)))


def test_plain_text_output_no_json_parsing(gateway, make_unit):
    gateway.replies.append(('{"translation": "x"}', "stop"))
    result = ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE)))
    assert result.text == '{"translation": "x"}'  # returned as text; QA decides


@pytest.mark.parametrize("raw,source,expected", [
    ('"مرحبا بالعالم"', "Hello world", "مرحبا بالعالم"),
    ("«مرحبا بالعالم»", "Hello world", "مرحبا بالعالم"),
    ('"مرحبا بالعالم"', '"Hello world"', '"مرحبا بالعالم"'),
    ("Translation: مرحبا", "Hello", "مرحبا"),
    ("الترجمة: مرحبا", "Hello", "مرحبا"),
    ("Translation: مرحبا", "Translation: hello", "Translation: مرحبا"),
    ("```\nمرحبا\n```", "Hello", "مرحبا"),
    ("  مرحبا  \n", "Hello", "مرحبا"),
    ('"أ" و "ب"', "a and b", '"أ" و "ب"'),
])
def test_clean_output_strips_wrappers_only_when_absent_from_source(raw, source, expected):
    assert clean_output(raw, source) == expected
