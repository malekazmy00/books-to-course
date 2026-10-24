"""Per-unit translation flow (D-028 §3): routing, QA, QE, policy, repair/retranslate, TM."""
from datetime import datetime, timezone

import pytest

from b2c_contracts import EngineIdentity, QAIssue, QEResult, TranslationUnit, UnitTranslation
from b2c_translation.book_pipeline import (PipelineConfig, UnitTranslator, assemble_blocks, split_sentences,
                                           translate_units)
from b2c_translation.engines import EngineCapabilities, EngineResult
from b2c_translation.qa import QEOutcome

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
IDENTITY = EngineIdentity(provider="fake", model_id="fake-mt", revision="r1", prompt_version="tr-test")


def unit(text="The mean is 3.", *, kind="prose", markers=(), uid="b:u00001", blocks=("b:1:0",)):
    return TranslationUnit(unit_id=uid, book_id="b", block_ids=list(blocks), kind=kind, source_text=text,
                           footnote_markers=list(markers), pages=[1], section_path=["Ch 1"])


class FakeEngine:
    def __init__(self, outputs, *, repair=False):
        self.outputs = list(outputs)
        self.requests = []
        self._caps = EngineCapabilities(follows_instructions=repair, supports_terms=repair,
                                        supports_context=repair, supports_repair=repair, max_source_chars=2000)

    identity = property(lambda self: IDENTITY)
    capabilities = property(lambda self: self._caps)

    def translate(self, request):
        self.requests.append(request)
        out = self.outputs.pop(0)
        if callable(out):
            out = out(request)
        return EngineResult(text=out, identity=IDENTITY, finish_reason="stop")


class FakeQE:
    def __init__(self, passed=True):
        self.passed, self.calls = passed, 0

    name = "fake-qe"

    def estimate(self, unit, target):
        self.calls += 1
        issues = [] if self.passed else [QAIssue(check_id="qe", category="meaning_error", severity="major",
                                                 origin="qe")]
        return QEOutcome(passed=self.passed, result=QEResult(deterministic_pass=True, route="auto_accept",
                                                             cometkiwi=0.9 if self.passed else 0.2),
                         issues=issues)


class FakeMemory:
    def __init__(self, hit=None):
        self.hit, self.stored = hit, []

    def lookup(self, unit, source_language, target_language):
        return self.hit

    def store(self, unit, translation, **kw):
        self.stored.append(translation)


def translator(engine, **kw):
    kw.setdefault("estimator", FakeQE())
    return UnitTranslator(engine, config=PipelineConfig(max_attempts=3), clock=lambda: NOW, **kw)


def test_noise_is_dropped_without_engine_call():
    engine = FakeEngine([])
    out = translator(engine).translate(unit("Page 12", kind="noise")).translation
    assert out.decision == "dropped" and out.target_text == "" and out.origin == "rule"
    assert engine.requests == []


def test_reference_is_rendered_by_rule_not_by_engine():
    engine = FakeEngine([])
    t = translator(engine, reference_renderer=lambda u: "المرجع نفسه، ص 55.")
    out = t.translate(unit("Ibid., 55.", kind="reference")).translation
    assert out.origin == "rule" and out.decision == "accepted" and out.target_text == "المرجع نفسه، ص 55."
    assert engine.requests == []


def test_engine_receives_only_the_unit_and_footnotes_are_restored_by_code():
    engine = FakeEngine(["المتوسط هو 3."])
    memory = FakeMemory()
    out = translator(engine, memory=memory).translate(unit(markers=["12"])).translation
    assert engine.requests[0].unit.source_text == "The mean is 3."
    assert out.decision == "accepted" and out.target_text.endswith("12")
    assert memory.stored == [out]


def test_without_semantic_qe_engine_output_is_never_accepted():
    out = translator(FakeEngine(["المتوسط هو 3."]), estimator=None).translate(unit()).translation
    assert out.decision == "needs_review"


def test_leaked_instructions_trigger_retranslation_not_acceptance():
    leak = "نسخ كل علامة محمية من هذا القائمة الدقيقة مرة واحدة. القائمة: [\"3\", \"4\"]"
    engine = FakeEngine([leak, "المتوسط هو 3.", "والوسيط هو 4."])
    result = translator(engine).translate(unit("The mean is 3. The median is 4."))
    assert result.translation.decision == "accepted" and result.translation.attempts == 2
    assert "prompt_leakage" in {i.category for i in result.drafts[0].issues}


def test_identical_request_is_never_resent():
    # temperature-0 requests are cached: resending the same request cannot change the output
    engine = FakeEngine(["[نص المصدر] The mean is 3."])
    out = translator(engine).translate(unit()).translation
    assert len(engine.requests) == 1 and out.decision == "rejected"


def test_multi_sentence_unit_is_retranslated_sentence_by_sentence():
    src = "The mean is 3. The median is 4."
    engine = FakeEngine(["المتوسط", "المتوسط هو 3.", "والوسيط هو 4."])
    out = translator(engine).translate(unit(src)).translation
    assert [r.unit.source_text for r in engine.requests[1:]] == ["The mean is 3.", "The median is 4."]
    assert out.decision == "accepted" and out.target_text == "المتوسط هو 3. والوسيط هو 4."


def test_repairable_issue_uses_typed_repair_when_engine_supports_it():
    terms = {"Key Assumptions Check": "فحص الافتراضات الرئيسية"}
    src = "Run a Key Assumptions Check."
    engine = FakeEngine(["أجرِ فحصًا للافتراضات.", "أجرِ فحص الافتراضات الرئيسية."], repair=True)
    out = translator(engine, terms=terms).translate(unit(src)).translation
    assert engine.requests[1].repair is not None
    assert {i.category for i in engine.requests[1].repair.issues} == {"terminology"}
    assert engine.requests[1].terms == terms
    assert out.decision == "accepted"


def test_exhausted_attempts_with_critical_issue_are_rejected():
    leak = "[نص المصدر] The mean is 3."
    engine = FakeEngine([leak, leak, leak], repair=True)
    out = translator(engine).translate(unit()).translation
    assert out.decision == "rejected" and out.attempts == 3


def test_failed_semantic_qe_is_not_accepted():
    engine = FakeEngine(["المتوسط هو 3."] * 3, repair=True)
    out = translator(engine, estimator=FakeQE(passed=False)).translate(unit()).translation
    assert out.decision != "accepted"


def test_qe_is_skipped_when_deterministic_checks_already_block():
    qe = FakeQE()
    translator(FakeEngine(["", "", ""], repair=True), estimator=qe).translate(unit())
    assert qe.calls == 0


def test_human_translation_memory_wins_over_engine():
    human = UnitTranslation(unit_id="b:u00001", target_text="المتوسط يساوي 3.", origin="memory",
                            decision="accepted", decided_by="human", decided_at=NOW)
    engine = FakeEngine([])
    out = translator(engine, memory=FakeMemory(hit=human)).translate(unit()).translation
    assert out is human and engine.requests == []


def test_engine_memory_hit_is_rechecked_by_qa():
    cached = UnitTranslation(unit_id="b:u00001", target_text="[نص المصدر] المتوسط 3", origin="memory",
                             engine=IDENTITY, decision="needs_review", decided_by="policy", decided_at=NOW)
    engine = FakeEngine(["المتوسط هو 3."])
    out = translator(engine, memory=FakeMemory(hit=cached)).translate(unit()).translation
    assert out.target_text == "المتوسط هو 3." and len(engine.requests) == 1


def test_context_is_passed_only_when_engine_supports_it():
    units = [unit("First.", uid="b:u1"), unit("The mean is 3.", uid="b:u2")]
    plain = FakeEngine(["الأول.", "المتوسط هو 3."])
    translate_units(units, translator(plain))
    assert plain.requests[1].context_before is None
    chat = FakeEngine(["الأول.", "المتوسط هو 3."], repair=True)
    translate_units(units, translator(chat))
    assert chat.requests[1].context_before == "First."


def test_assemble_blocks_keeps_only_accepted_text_and_reports_the_rest():
    u1, u2 = unit("A.", uid="b:u1"), unit("B.", uid="b:u2")
    u3 = unit("C.", uid="b:u3", blocks=("b:1:1",))
    acc = lambda uid, t: UnitTranslation(unit_id=uid, target_text=t, origin="human", decision="accepted",
                                         decided_by="human", decided_at=NOW)
    pending = UnitTranslation(unit_id="b:u3", target_text="ج.", origin="engine", engine=IDENTITY,
                              decision="needs_review", decided_by="policy", decided_at=NOW)
    blocks = assemble_blocks([u1, u2, u3], {"b:u1": acc("b:u1", "أ."), "b:u2": acc("b:u2", "ب."), "b:u3": pending})
    assert blocks["b:1:0"].text == "أ. ب." and blocks["b:1:0"].complete
    assert blocks["b:1:1"].text is None and blocks["b:1:1"].pending_unit_ids == ["b:u3"]


@pytest.mark.parametrize("text,expected", [
    ("Dr. Smith arrived. He left at 5 p.m. today.", 2),
    ("One. Two? Three!", 3),
    ("No terminal punctuation", 1),
])
def test_split_sentences(text, expected):
    assert len(split_sentences(text)) == expected


def test_references_use_the_rule_renderer_by_default():
    engine = FakeEngine([])
    out = translator(engine).translate(unit("Ibid., 55.", kind="reference")).translation
    assert "المرجع نفسه" in out.target_text and "55" in out.target_text
    assert out.origin == "rule" and engine.requests == []


def test_index_entries_are_kept_verbatim_by_rule():
    engine = FakeEngine([])
    out = translator(engine).translate(unit("hegemony, 12, 45-47", kind="index")).translation
    assert out.origin == "rule" and out.target_text == "hegemony, 12, 45-47" and engine.requests == []
