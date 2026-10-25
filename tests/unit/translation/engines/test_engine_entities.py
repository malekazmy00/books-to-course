"""Approved names reach the engine (D-047).

Before D-047 only locked terms were sent; the approved names of people, places, organisations
and works were checked after translation but never shown to the model, so it guessed every
transliteration (most real ``named_entity`` alarms of book 1: «هيور» for the approved «هوير»).
Now the names occurring in the unit are sent in their own versioned block; accepted spelling
variants are never sent (they only widen the check).
"""
from datetime import datetime, timezone

from b2c_contracts import EngineIdentity, TranslationUnit
from b2c_translation.book_pipeline import PipelineConfig, UnitTranslator, names_in
from b2c_translation.engines.base import EngineCapabilities, EngineRequest, EngineResult
from b2c_translation.engines.chat import ChatEngine
from b2c_translation.engines.mt import PlainMTEngine
from b2c_translation.prompts import get_template

IDENTITY = EngineIdentity(provider="llama.cpp", model_id="qwen-test", revision="sha-abc", prompt_version="unset")
FULL = EngineCapabilities(follows_instructions=True, supports_terms=True, supports_context=True,
                          supports_repair=True, max_source_chars=2000)
BARE = EngineCapabilities(follows_instructions=True, supports_terms=False, supports_context=False,
                          supports_repair=False, max_source_chars=2000)
SOURCE = "Richards J. Heuer Jr. wrote the book at the CIA."
NAMES = {"Richards J. Heuer Jr.": "ريتشاردز جيه. هوير جونيور", "Randolph H. Pherson": "راندولف إتش. فيرسون"}


def request(unit, **kw):
    return EngineRequest(unit=unit, source_language="en", target_language="ar", **kw)


# ------------------------------------------------------------------ chat engine
def test_names_in_the_unit_are_sent_in_their_own_block(gateway, make_unit):
    result = ChatEngine(gateway, IDENTITY, FULL).translate(request(make_unit(SOURCE), entities=NAMES))
    call = gateway.calls[0]
    system = call["messages"][0]["content"]
    assert get_template("chat.entities").render(
        term_lines="- Richards J. Heuer Jr. => ريتشاردز جيه. هوير جونيور") in system
    assert "Pherson" not in system and "فيرسون" not in system
    assert "chat.entities@v1" in call["prompt_version"] == result.identity.prompt_version
    assert call["messages"][1]["content"] == SOURCE        # the user turn is still the bare source


def test_without_names_the_prompt_and_its_version_are_unchanged(gateway, make_unit):
    engine = ChatEngine(gateway, IDENTITY, FULL)
    engine.translate(request(make_unit(SOURCE)))
    engine.translate(request(make_unit(SOURCE), entities={"Randolph H. Pherson": "راندولف إتش. فيرسون"}))
    first, second = gateway.calls
    assert first["messages"] == second["messages"]
    assert first["prompt_version"] == second["prompt_version"]
    assert "chat.entities" not in first["prompt_version"]


def test_a_common_word_does_not_send_a_name(gateway, make_unit):
    # D-046: names match case-sensitively ("convention hall" is not Stuart Hall)
    ChatEngine(gateway, IDENTITY, FULL).translate(request(
        make_unit("Delegates met in the convention hall."), entities={"Hall": "هول"}))
    assert "هول" not in gateway.calls[0]["messages"][0]["content"]


def test_a_name_that_is_also_a_locked_term_is_sent_once(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, FULL).translate(request(
        make_unit("The CIA analysts."), terms={"CIA": "وكالة الاستخبارات المركزية"},
        entities={"CIA": "وكالة الاستخبارات المركزية"}))
    system = gateway.calls[0]["messages"][0]["content"]
    assert system.count("CIA =>") == 1


def test_names_are_not_sent_when_terms_are_not_supported(gateway, make_unit):
    ChatEngine(gateway, IDENTITY, BARE).translate(request(make_unit(SOURCE), entities=NAMES))
    call = gateway.calls[0]
    assert "هوير" not in call["messages"][0]["content"] and "chat.entities" not in call["prompt_version"]


def test_the_entities_template_is_versioned_and_tells_the_model_to_use_the_form():
    t = get_template("chat.entities")
    assert t.version_id == "chat.entities@v1" and t.placeholders == {"term_lines"}
    assert "exactly" in t.text and "Arabic script" in t.text


# ------------------------------------------------------------------ plain MT engine
def test_plain_mt_gets_names_as_reference_translations(gateway, make_unit):
    caps = EngineCapabilities(False, True, False, False, 2000)
    PlainMTEngine(gateway, IDENTITY, caps, "hy-mt2").translate(request(
        make_unit(SOURCE), terms={"CIA": "وكالة الاستخبارات المركزية"}, entities=NAMES))
    content = gateway.calls[0]["messages"][0]["content"]
    assert "CIA translates to وكالة الاستخبارات المركزية" in content
    assert "Richards J. Heuer Jr. translates to ريتشاردز جيه. هوير جونيور" in content
    assert "Pherson" not in content


# ------------------------------------------------------------------ pipeline
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


class RecordingEngine:
    def __init__(self, text="ترجمة"):
        self.requests, self.text = [], text

    identity = property(lambda self: IDENTITY)
    capabilities = property(lambda self: FULL)

    def translate(self, req):
        self.requests.append(req)
        return EngineResult(text=self.text, identity=IDENTITY, finish_reason="stop")


def _unit(text, kind="prose"):
    return TranslationUnit(unit_id="b:u00001", book_id="b", block_ids=["b:1:0"], kind=kind, source_text=text,
                           footnote_markers=[], pages=[1], section_path=["Ch 1"])


def test_the_translator_sends_the_approved_names_of_the_unit():
    engine = RecordingEngine("كتب ريتشاردز جيه. هوير جونيور الكتاب في الوكالة.")
    UnitTranslator(engine, entities=NAMES, entity_variants={"Richards J. Heuer Jr.": ("ريتشاردز ج. هيور",)},
                   config=PipelineConfig(max_attempts=1), clock=lambda: NOW).translate(_unit(SOURCE))
    [req] = engine.requests
    assert dict(req.entities) == {"Richards J. Heuer Jr.": "ريتشاردز جيه. هوير جونيور"}
    # an accepted variant widens the check only; it is never put in the prompt
    assert "هيور" not in str(req)


def test_names_in_matches_like_the_check():
    assert names_in("Stuart Hall and the convention hall", {"Hall": "هول"}) == {"Hall": "هول"}
    assert names_in("the convention hall", {"Hall": "هول"}) == {}
    assert names_in("RICHARDS J. HEUER JR.", NAMES) == {"Richards J. Heuer Jr.": "ريتشاردز جيه. هوير جونيور"}


def test_a_sentence_retry_sends_the_names_of_its_own_sentence():
    calls = []

    class Splitter(RecordingEngine):
        def translate(self, req):
            calls.append(req)
            return EngineResult(text="نص قصير جدا." if len(calls) == 1 else "جملة.", identity=IDENTITY,
                                finish_reason="stop")

    from b2c_translation.qa import QEOutcome
    from b2c_contracts import QEResult

    class FailingQE:
        name = "qe"

        def estimate(self, unit, target):
            from b2c_contracts import QAIssue
            return QEOutcome(passed=False, result=QEResult(deterministic_pass=True, route="retranslate"),
                             issues=[QAIssue(check_id="qe", category="meaning_error", severity="major", origin="qe")])

    text = "Richards J. Heuer Jr. wrote it. Randolph H. Pherson revised it."
    UnitTranslator(Splitter(), entities=NAMES, estimator=FailingQE(), config=PipelineConfig(max_attempts=2),
                   clock=lambda: NOW).translate(_unit(text))
    sentence_requests = [r for r in calls if "#s" in r.unit.unit_id]
    assert [dict(r.entities) for r in sentence_requests] == [
        {"Richards J. Heuer Jr.": "ريتشاردز جيه. هوير جونيور"}, {"Randolph H. Pherson": "راندولف إتش. فيرسون"}]
