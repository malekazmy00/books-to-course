"""Hint-tier termbase entries (D-039) reach the chat prompt as preferred renderings, separate
from locked terms; without hints the prompt (and its version) is unchanged."""
from b2c_contracts import EngineIdentity
from b2c_translation.book_pipeline import UnitTranslator
from b2c_translation.engines.base import EngineCapabilities, EngineRequest, EngineResult
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.prompts import get_template

IDENTITY = EngineIdentity(provider="llama.cpp", model_id="qwen-test", revision="sha-abc", prompt_version="unset")
FULL = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=2000)
BARE = EngineCapabilities(follows_instructions=True, supports_terms=False, supports_context=False,
                          supports_repair=False, max_source_chars=2000)
SOURCE = "The analyst used Signal Mapping."


def request(unit, **kw):
    return EngineRequest(unit=unit, source_language="en", target_language="ar", **kw)


def test_hints_are_rendered_as_preferred_not_locked(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, FULL).translate(request(
        make_unit(SOURCE), terms={"Signal Mapping": "خريطة الإشارات"}, hints={"analyst": "المحلل", "nation": "الأمة"}))
    call = gateway.calls[0]
    system = call["messages"][0]["content"]
    hints_t = get_template("chat.term_hints")
    assert hints_t.render(term_lines="- analyst => المحلل") in system
    assert "- nation =>" not in system and "الأمة" not in system   # only hints occurring in the unit
    assert "- Signal Mapping => خريطة الإشارات" in system
    assert "chat.term_hints@v1" in call["prompt_version"] and "chat.terms@v1" in call["prompt_version"]
    assert "inflect" in hints_t.text and "exactly" not in hints_t.text


def test_no_hint_means_an_unchanged_prompt_version(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE), terms={"Signal Mapping": "خريطة الإشارات"}))
    ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE), terms={"Signal Mapping": "خريطة الإشارات"},
                                                          hints={"nation": "الأمة"}))
    first, second = gateway.calls
    assert first["prompt_version"] == second["prompt_version"]
    assert "chat.term_hints" not in first["prompt_version"]
    assert first["messages"] == second["messages"]


def test_hints_are_omitted_when_terms_are_not_supported(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, BARE).translate(request(make_unit(SOURCE), hints={"analyst": "المحلل"}))
    assert "المحلل" not in gateway.calls[0]["messages"][0]["content"]


class _Engine:
    def __init__(self):
        self.requests = []

    identity = property(lambda self: IDENTITY)
    capabilities = property(lambda self: FULL)

    def translate(self, req):
        self.requests.append(req)
        return EngineResult(text="استخدم الباحث خريطة الإشارات.", identity=IDENTITY, finish_reason="stop")


def test_unit_translator_sends_hints_and_checks_them_as_minor(make_unit):
    engine = _Engine()
    translator = UnitTranslator(engine, terms={"Signal Mapping": "خريطة الإشارات"},
                                hints={"analyst": "المحلل", "nation": "الأمة"})
    result = translator.translate(make_unit(SOURCE))
    assert engine.requests[0].hints == {"analyst": "المحلل"}
    hint_issues = [i for i in result.translation.issues if i.check_id == "terminology_hint"]
    assert [(i.severity, i.evidence["term"]) for i in hint_issues] == [("minor", "analyst")]
    assert not [i for i in result.translation.issues if i.check_id == "terminology"]
