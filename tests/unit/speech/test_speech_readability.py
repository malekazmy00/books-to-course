"""Reading quality of the speech text (docs/proposals/2026-10-25-speech-readability.md).

Owner: "the text can be perfect but the reading weak". These tests pin the practice chosen in the
proposal: Arabic punctuation normalization, TTS-friendly phrase splitting bounded by the engine's
real input limit, a prosodic boundary/pause plan (SSML 1.1 break strengths), voicing of
parenthesised original terms only from a reviewed pronunciation (or a spelled acronym), pausal
forms (waqf) for diacritized text, an SSML rendering, and an offline reading-quality gate.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from b2c_contracts import ArtifactRef, EngineIdentity, QEResult, TranslationUnit, UnitTranslation
from b2c_speech.text import (BREAK_STRENGTH, ENGINE_PROFILES, PAUSE_MS, READING_QUALITY_GATE, PronunciationLexicon,
                             apply_waqf, build_speech_text, evaluate_reading_quality, normalize_text, pausal_form,
                             render_ssml, split_phrases)

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/",
                     sha256="s" * 64, created_at=NOW)
ENGINE = EngineIdentity(provider="fake", model_id="m", prompt_version="p@1")
LEX = PronunciationLexicon.from_mappings(terms={"hegemony": "الهيمنة"}, entities={"Gramsci": "غرامشي"})
SPEECH_LEX = PronunciationLexicon.from_mappings(terms={"hegemony": "الهيمنة"},
                                                overrides={"hegemony": "هيجموني"})
FASIH = ENGINE_PROFILES["fasih-xtts"]
AR_ALLOWED = re.compile("^[\u0621-\u064a\u064b-\u0652\u0671-\u06d3\\s.،؛:!؟]+$")


def spoken(text: str, lexicon: PronunciationLexicon = LEX, structure: str = "paragraph") -> str:
    return normalize_text(text, lexicon=lexicon, structure=structure, terminal=True).text


def unit(seq, kind="prose"):
    return TranslationUnit(unit_id=f"bk:u{seq:05d}", book_id="bk", block_ids=[f"bk:b{seq:05d}"], kind=kind,
                           source_text="x", footnote_markers=[], pages=[1], section_path=["Ch"])


def accepted(seq, target):
    return UnitTranslation(unit_id=f"bk:u{seq:05d}", target_text=target, origin="engine", engine=ENGINE,
                           qe=QEResult(deterministic_pass=True, route="auto_accept"), decision="accepted",
                           decided_by="policy", decided_at=NOW)


def build(items, lexicon=LEX, **kw):
    units = [unit(i + 1, kind) for i, (kind, _) in enumerate(items)]
    translations = {f"bk:u{i + 1:05d}": accepted(i + 1, text) for i, (_, text) in enumerate(items)}
    return build_speech_text(units, translations, book_id="bk", source=SOURCE, lexicon=lexicon, **kw)


# ------------------------------------------------------------------ punctuation
@pytest.mark.parametrize("text,expected", [
    ("الدولة, المجتمع والسوق", "الدولة، المجتمع والسوق."),
    ("ما الهيمنة? إنها القيادة", "ما الهيمنة؟ إنها القيادة."),
    ("أولا; ثانيا", "أولا؛ ثانيا."),
    ("هذا مهم!! جدا", "هذا مهم! جدا."),
    ("نص بلا نقطة في آخره", "نص بلا نقطة في آخره."),
    ("ينتهي بنقطتين:", "ينتهي بنقطتين:"),
    ("مساحة 3×4 أمتار", "مساحة ثلاثة في أربعة أمتار."),
])
def test_arabic_punctuation_is_normalized(text, expected):
    assert spoken(text) == expected


def test_heading_gets_a_final_stop_for_falling_intonation():
    assert spoken("رؤيتنا للمستقبل", structure="heading") == "رؤيتنا للمستقبل."


def test_nested_parentheses_become_breaks_not_symbols():
    out = spoken("قال الكاتب (وهو مؤرخ (من إيطاليا) معروف) ذلك")
    assert "(" not in out and ")" not in out
    assert out == "قال الكاتب، وهو مؤرخ، من إيطاليا، معروف، ذلك."


def test_long_quotation_is_set_off_by_breaks_short_quote_is_not():
    assert spoken("قال «إن الهيمنة هي قيادة فكرية وأخلاقية» ثم مضى") == \
        "قال، إن الهيمنة هي قيادة فكرية وأخلاقية، ثم مضى."
    assert spoken("مفهوم «الهيمنة» الثقافية") == "مفهوم الهيمنة الثقافية."


# ------------------------------------------------------------------ parenthesised original terms
def test_gloss_without_reviewed_pronunciation_is_not_guessed():
    out = normalize_text("الهيمنة (hegemony) مفهوم مركزي", lexicon=LEX, terminal=True)
    assert out.text == "الهيمنة مفهوم مركزي."
    assert out.unvoiced_glosses == ["hegemony"] and "latin_gloss_unvoiced" in out.rules


def test_gloss_with_reviewed_pronunciation_is_voiced_after_a_short_break():
    out = normalize_text("الهيمنة (hegemony) مفهوم مركزي", lexicon=SPEECH_LEX, terminal=True)
    assert out.text == "الهيمنة، هيجموني، مفهوم مركزي."
    assert "latin_gloss_voiced" in out.rules and out.unvoiced_glosses == []


def test_acronym_gloss_is_spelled_once_per_document():
    seen: set[str] = set()
    first = normalize_text("وكالة الاستخبارات المركزية (CIA) جهاز كبير", lexicon=LEX, seen_glosses=seen, terminal=True)
    again = normalize_text("ثم عادت وكالة الاستخبارات المركزية (CIA) فقالت", lexicon=LEX, seen_glosses=seen, terminal=True)
    assert first.text == "وكالة الاستخبارات المركزية، سي آي إيه، جهاز كبير."
    assert again.text == "ثم عادت وكالة الاستخبارات المركزية فقالت."
    assert "latin_gloss_repeat_removed" in again.rules


def test_latin_citation_is_still_removed():
    out = normalize_text("كما يرى (Gramsci, 1971: 23) فإن", lexicon=LEX, terminal=True)
    assert out.text == "كما يرى فإن." and "latin_citation_removed" in out.rules


# ------------------------------------------------------------------ phrase splitting
LONG = ("وقد أدت هذه التحولات العميقة في بنية الدولة الحديثة إلى ظهور أشكال جديدة من السيطرة الاجتماعية "
        "التي لم تعد تعتمد على القوة وحدها بل على الرضا الذي تنتجه مؤسسات المجتمع المدني، "
        "مثل المدرسة والكنيسة والصحافة والنقابات، حيث تتشكل القناعات اليومية للناس.")


def test_long_sentence_is_split_into_bounded_phrases_at_clause_boundaries():
    phrases = split_phrases(LONG, max_chars=FASIH.max_phrase_chars, max_words=FASIH.max_phrase_words,
                            min_chars=FASIH.min_phrase_chars)
    assert len(phrases) > 1
    assert all(len(p) <= FASIH.max_phrase_chars and len(p.split()) <= FASIH.max_phrase_words for p in phrases)
    assert " ".join(phrases) == LONG
    # prefers punctuation / clause words over arbitrary word cuts
    assert any(p.endswith("،") for p in phrases)
    assert any(p.split()[0] in {"التي", "بل", "حيث", "مثل"} for p in phrases[1:])


def test_split_is_balanced_no_tiny_tail():
    text = "كلمة " * 40
    phrases = split_phrases(text.strip(), max_chars=85, max_words=16, min_chars=12)
    assert max(len(p) for p in phrases) - min(len(p) for p in phrases) <= 15


def test_phrase_never_ends_on_a_preposition_or_inside_a_number():
    text = ("أتم السيد فيرسون مسيرة مهنية استمرت ثمانية وعشرين عاماً في مجتمع الاستخبارات "
            "وكان يشغل منصبا رفيعا في وكالة الاستخبارات المركزية لسنوات طويلة جدا")
    phrases = split_phrases(text, max_chars=60, max_words=16, min_chars=12)
    assert len(phrases) > 1
    for left, right in zip(phrases, phrases[1:]):
        assert left.split()[-1] not in {"في", "من", "إلى", "على", "ثمانية"}
        assert not (left.split()[-1] == "ثمانية" and right.split()[0] == "وعشرين")


def test_unbroken_token_still_terminates():
    phrases = split_phrases("أ" * 400, max_chars=85, max_words=16, min_chars=12)
    assert "".join(phrases) == "أ" * 400 and all(len(p) <= 85 for p in phrases)


# ------------------------------------------------------------------ pause plan
def test_pause_and_break_scale_is_monotonic():
    order = ["phrase", "sentence", "list_item", "paragraph", "heading", "section"]
    assert [PAUSE_MS[b] for b in order] == sorted(PAUSE_MS[b] for b in order)
    assert BREAK_STRENGTH["phrase"] == "weak" and BREAK_STRENGTH["sentence"] == "medium"
    assert BREAK_STRENGTH["paragraph"] == "strong" and BREAK_STRENGTH["section"] == "x-strong"
    assert max(PAUSE_MS.values()) <= 1200  # leaves room under the 1.5 s internal-silence limit


def test_document_carries_a_boundary_plan():
    doc = build([("heading", "الفصل الأول"), ("prose", "جملة أولى قصيرة هنا. " + LONG),
                 ("list_item", "البند الأول من القائمة"), ("list_item", "البند الثاني من القائمة"),
                 ("prose", "فقرة أخيرة قبل العنوان."), ("heading", "خاتمة"), ("prose", "نص الخاتمة.")])
    b = [(s.structure, s.boundary_after) for s in doc.segments]
    assert b[0] == ("heading", "heading")
    para = [x for s, x in b if s == "paragraph"]
    assert "phrase" in para and "sentence" in para
    lists = [s for s in doc.segments if s.structure == "list_item"]
    assert [s.boundary_after for s in lists] == ["list_item", "paragraph"]
    before_heading = doc.segments[[s.spoken_text for s in doc.segments].index("خاتمة.") - 1]
    assert before_heading.boundary_after == "section"
    assert doc.segments[-1].boundary_after == "paragraph"
    for s in doc.segments:
        assert s.pause_after_ms == PAUSE_MS[s.boundary_after]
        assert s.break_after == BREAK_STRENGTH[s.boundary_after]
        if s.boundary_after != "phrase":
            assert s.spoken_text[-1] in ".!؟:"
        assert len(s.spoken_text) <= FASIH.max_phrase_chars


def test_unvoiced_glosses_are_traced_on_segments():
    doc = build([("prose", "الهيمنة (hegemony) مفهوم مركزي.")])
    assert doc.segments[0].unvoiced_glosses == ["hegemony"]


def test_ssml_rendering_uses_standard_elements():
    doc = build([("heading", "الفصل الأول"), ("prose", "جملة أولى. جملة ثانية.")],
                profile="ssml-generic")
    ssml = render_ssml(doc)
    assert ssml.startswith('<speak version="1.1"') and 'xml:lang="ar"' in ssml
    assert "<p>" in ssml and "<s>" in ssml
    assert '<break strength="strong" time="1000ms"/>' in ssml
    assert ssml.count("<p>") == ssml.count("</p>") and ssml.count("<s>") == ssml.count("</s>")


def test_ssml_escapes_text():
    doc = build([("prose", "نص.")], profile="ssml-generic")
    odd = doc.model_copy(update={"segments": [doc.segments[0].model_copy(update={"spoken_text": "أ < ب & ج."})]})
    assert "أ &lt; ب &amp; ج." in render_ssml(odd)


# ------------------------------------------------------------------ waqf (pausal form)
@pytest.mark.parametrize("word,expected", [
    ("الكِتَابُ", "الكِتَابْ"),
    ("كِتَابٌ", "كِتَابْ"),
    ("كِتَابًا", "كِتَابَا"),
    ("مَدْرَسَةٌ", "مَدْرَسَهْ"),
    ("المَدْرَسَةِ", "المَدْرَسَهْ"),
    ("الحَقُّ", "الحَقّْ"),
    ("فِي", "فِي"),
    ("كتاب", "كتاب"),
])
def test_pausal_form(word, expected):
    assert pausal_form(word) == expected


def test_apply_waqf_only_at_pause_positions():
    text = "ذَهَبَ الطَّالِبُ إِلَى المَدْرَسَةِ، ثُمَّ عَادَ إِلَى البَيْتِ."
    assert apply_waqf(text) == "ذَهَبَ الطَّالِبُ إِلَى المَدْرَسَهْ، ثُمَّ عَادَ إِلَى البَيْتْ."


# ------------------------------------------------------------------ reading-quality gate
def test_reading_quality_gate_passes_clean_plan():
    doc = build([("heading", "مقدمة"), ("prose", LONG), ("prose", "فقرة ثانية قصيرة.")])
    passed, metrics = evaluate_reading_quality(doc, FASIH)
    assert passed, metrics
    assert metrics["phrases_over_limit"] == 0 and metrics["unspeakable_chars"] == {}
    assert metrics["engine_rechunk_estimate"] == 0 and metrics["pause_plan_errors"] == []
    assert set(READING_QUALITY_GATE) <= set(metrics["thresholds"])


def test_reading_quality_gate_fails_on_bad_plan_and_unspeakable_text():
    doc = build([("prose", "فقرة أولى."), ("prose", "فقرة ثانية.")])
    seg = doc.segments[0]
    broken = doc.model_copy(update={"segments": [
        seg.model_copy(update={"spoken_text": "نص فيه رمز # و" + "كلمة " * 40, "pause_after_ms": 5000}),
        doc.segments[1]]})
    passed, metrics = evaluate_reading_quality(broken, FASIH)
    assert not passed
    assert metrics["phrases_over_limit"] == 1
    assert metrics["unspeakable_chars"] == {"#": 1}
    assert metrics["pause_plan_errors"]


def test_real_style_paragraph_is_fully_speakable():
    text = ("عمل سابقاً في وكالة الاستخبارات المركزية (CIA)، حيث أدار إنتاج التحليل الاستخباري حول مواضيع "
            "تتراوح من عدم الاستقرار العالمي إلى أمريكا اللاتينية, وعمل في طاقم المفتش العام")
    doc = build([("prose", text)])
    assert all(AR_ALLOWED.match(s.spoken_text) for s in doc.segments)
    assert "سي آي إيه" in " ".join(s.spoken_text for s in doc.segments)


def test_unknown_engine_profile_is_refused():
    from b2c_speech.text import engine_profile
    with pytest.raises(ValueError):
        engine_profile("no-such-engine")
    with pytest.raises(ValueError):
        build([("prose", "نص.")], profile="no-such-engine")
