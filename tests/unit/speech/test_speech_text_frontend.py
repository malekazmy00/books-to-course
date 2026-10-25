"""Speech text front end for Arabic academic text (R7, audit 07 C2/C3, §5.2 S1).

Golden cases: deterministic input -> expected spoken text. Every case comes from a pattern the
audit measured in real tracks (§3.4): footnote markers, page references, section numbers,
years, decades, decimals, percentages, ordinals, Latin acronyms and names.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from b2c_contracts import ArtifactRef, EngineIdentity, QEResult, TranslationUnit, UnitTranslation
from b2c_speech.text import (FRONTEND_VERSION, NotSpeechReady, PronunciationLexicon, build_speech_text,
                             cardinal, evaluate_speech_text, normalize_text, ordinal, speech_text_violations,
                             split_for_tts)

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/",
                     sha256="s" * 64, created_at=NOW)
ENGINE = EngineIdentity(provider="fake", model_id="m", prompt_version="p@1")
LEX = PronunciationLexicon.from_mappings(
    terms={"hegemony": "الهيمنة", "CIA": "وكالة المخابرات المركزية"},
    entities={"Antonio Gramsci": "أنطونيو غرامشي", "Gramsci": "غرامشي"})


def spoken(text: str, lexicon: PronunciationLexicon = LEX) -> str:
    return normalize_text(text, lexicon=lexicon).text


# ------------------------------------------------------------------ numbers
@pytest.mark.parametrize("n,case,expected", [
    (0, "nom", "صفر"), (1, "nom", "واحد"), (2, "nom", "اثنان"), (2, "gen", "اثنين"),
    (11, "nom", "أحد عشر"), (12, "gen", "اثني عشر"), (15, "nom", "خمسة عشر"),
    (20, "nom", "عشرون"), (20, "gen", "عشرين"), (21, "nom", "واحد وعشرون"),
    (100, "nom", "مئة"), (200, "gen", "مئتين"), (305, "nom", "ثلاثمئة وخمسة"),
    (1000, "nom", "ألف"), (2000, "nom", "ألفان"), (2011, "gen", "ألفين وأحد عشر"),
    (1980, "gen", "ألف وتسعمئة وثمانين"), (3000, "nom", "ثلاثة آلاف"), (11000, "nom", "أحد عشر ألفًا"),
    (100000, "nom", "مئة ألف"), (2500000, "nom", "مليونان وخمسمئة ألف"),
])
def test_cardinal(n, case, expected):
    assert cardinal(n, case=case) == expected


@pytest.mark.parametrize("n,gender,expected", [
    (1, "m", "الأول"), (1, "f", "الأولى"), (2, "m", "الثاني"), (10, "f", "العاشرة"),
    (11, "m", "الحادي عشر"), (12, "f", "الثانية عشرة"), (19, "m", "التاسع عشر"),
    (20, "m", "العشرون"), (21, "m", "الحادي والعشرون"),
])
def test_ordinal(n, gender, expected):
    assert ordinal(n, gender=gender) == expected


# ------------------------------------------------------------------ golden normalization
GOLDEN = [
    # footnote marker glued after punctuation (audit: «جماعي.واحد»)
    ("وهو فعل جماعي.1 القوى الاجتماعية", "وهو فعل جماعي. القوى الاجتماعية"),
    ("كما قال¹ في كتابه", "كما قال في كتابه"),
    # bracketed index/page references are not spoken (audit: «رؤيتنا [ثلاثة]»)
    ("رؤيتنا [3] للمستقبل", "رؤيتنا للمستقبل"),
    ("انظر الدراسات [12، 14] حول ذلك", "انظر الدراسات حول ذلك"),
    # year after a noun -> genitive, spoken together with the conjunction
    ("في عام 2011 بدأت الأحداث", "في عام ألفين وأحد عشر بدأت الأحداث"),
    ("منذ 1980 تغير كل شيء", "منذ ألف وتسعمئة وثمانين تغير كل شيء"),
    # decades (audit: «ألف و تسعمائة و ثمانونات»)
    ("في 1980ات ظهرت الحركة", "في ثمانينيات القرن العشرين ظهرت الحركة"),
    ("خلال الـ1960ات", "خلال ستينيات القرن العشرين"),
    # decimals and percentages (audit: «ثلاثة.خمسة%»)
    ("ارتفع بنسبة 3.5% فقط", "ارتفع بنسبة ثلاثة فاصلة خمسة بالمئة فقط"),
    ("بلغت ٪25 تقريبا", "بلغت خمسة وعشرين بالمئة تقريبا"),
    ("نحو ٢٥٪ من السكان", "نحو خمسة وعشرين بالمئة من السكان"),
    # ordinals after ordinal nouns (audit: «الفصل واحد»)
    ("الفصل 1 مقدمة", "الفصل الأول مقدمة"),
    ("في الطبعة 2 من الكتاب", "في الطبعة الثانية من الكتاب"),
    ("في القرن 19 ظهرت", "في القرن التاسع عشر ظهرت"),
    ("القرن XX كان قرن الحروب", "القرن العشرون كان قرن الحروب"),
    # ranges
    ("بين عامي 1914-1918 وقعت الحرب", "بين عامي ألف وتسعمئة وأربعة عشر وألف وتسعمئة وثمانية عشر وقعت الحرب"),
    ("في الفترة 1914–1918 وقعت", "في الفترة من ألف وتسعمئة وأربعة عشر إلى ألف وتسعمئة وثمانية عشر وقعت"),
    ("من 1919 إلى 1921", "من ألف وتسعمئة وتسعة عشر إلى ألف وتسعمئة وواحد وعشرين"),
    # numbers in parentheses: a reference number is dropped, years are spoken
    ("كما ذكرنا (3) سابقا", "كما ذكرنا سابقا"),
    ("الحرب (1939-1945) كانت", "الحرب، من ألف وتسعمئة وتسعة وثلاثين إلى ألف وتسعمئة وخمسة وأربعين، كانت"),
    # found on the real accepted units of book-main (eval/reports/speech-v2-2026-10-25.md)
    ("انظر الشكل 1.6 أدناه", "انظر الشكل واحد نقطة ستة أدناه"),
    ("3.6 جعل استخدام التقنيات عادة", "جعل استخدام التقنيات عادة"),
    ("المعرف LCCN 2019033477 هنا", "المعرف إل سي سي إن اثنان صفر واحد تسعة صفر ثلاثة ثلاثة أربعة سبعة سبعة هنا"),
    # an acronym gloss is spelled at its first mention (speech readability proposal 2026-10-25; it was dropped)
    ("وتحليل الفرضيات المتنافسة [ACH]، ورسم الخرائط", "وتحليل الفرضيات المتنافسة، إيه سي إتش، ورسم الخرائط"),
    ("مستكشف التناقضات™ وحقوق © محفوظة", "مستكشف التناقضات وحقوق محفوظة"),
    ("في 11 سبتمبر 2001 وقعت", "في الحادي عشر من سبتمبر ألفين وواحد وقعت"),
    ("العصف الذهني العنقودي (الفصل 6).", "العصف الذهني العنقودي، الفصل السادس."),
    ("النظام A والنظام B", "النظام إيه والنظام بي"),
    ("محفوظة لـ سي كيو برس", "محفوظة لسي كيو برس"),
    # thousands separators
    ("قتل 1,500 شخص", "قتل ألف وخمسمئة شخص"),
    # page references and URLs are not spoken
    ("كما ورد (انظر ص 23) سابقا", "كما ورد سابقا"),
    ("المصدر https://example.org/a?b=1 متاح", "المصدر متاح"),
    # Latin citation/gloss in parentheses is dropped; the Arabic already says it
    ("الهيمنة (hegemony) مفهوم مركزي", "الهيمنة مفهوم مركزي"),
    ("كما يرى (Gramsci, 1971: 23) فإن", "كما يرى فإن"),
    # lexicon: entity table / locked terms; longest match first
    ("كتب Antonio Gramsci دفاتره", "كتب أنطونيو غرامشي دفاتره"),
    ("قال Gramsci ذلك", "قال غرامشي ذلك"),
    ("تقرير CIA السري", "تقرير وكالة المخابرات المركزية السري"),
    # acronym not in the lexicon: spelled letter by letter
    ("مكتب FBI في واشنطن", "مكتب إف بي آي في واشنطن"),
    # Arabic abbreviations
    ("قال د. أحمد", "قال دكتور أحمد"),
    ("توفي سنة 632م", "توفي سنة ستمئة واثنين وثلاثين ميلادية"),
    ("الكتب والمقالات إلخ", "الكتب والمقالات إلى آخره"),
    # symbols
    ("الفصل 1 • مقدمة", "الفصل الأول، مقدمة"),
    ("الدولة/المجتمع المدني", "الدولة أو المجتمع المدني"),
    ("«الهيمنة» الثقافية", "الهيمنة الثقافية"),
]


@pytest.mark.parametrize("text,expected", GOLDEN, ids=[g[0][:20] for g in GOLDEN])
def test_golden_normalization(text, expected):
    assert spoken(text) == expected


def test_heading_section_number_is_dropped():
    assert normalize_text("1.1 رؤيتنا", lexicon=LEX, structure="heading").text == "رؤيتنا"
    assert normalize_text("2.3.1 دور الدولة", lexicon=LEX, structure="heading").text == "دور الدولة"


def test_rules_are_recorded():
    result = normalize_text("في عام 2011 قال Gramsci [3] ذلك.1", lexicon=LEX)
    assert {"year", "lexicon", "bracket_reference_removed", "footnote_marker_removed"} <= set(result.rules)
    assert result.lexicon_hits == ["Gramsci"]


def test_unknown_latin_word_falls_back_and_is_counted():
    result = normalize_text("قال Bourdieu ذلك", lexicon=LEX)
    assert result.lexicon_misses == ["Bourdieu"]
    assert not any("a" <= ch.lower() <= "z" for ch in result.text)
    assert "latin_fallback" in result.rules


@pytest.mark.parametrize("text", [GOLDEN[i][0] for i in range(len(GOLDEN))])
def test_spoken_text_has_no_raw_digits_brackets_or_latin(text):
    out = spoken(text)
    assert speech_text_violations(out) == []


def test_violations_detected():
    assert set(speech_text_violations("رقم 3 و[4] وabc و http://x.y")) >= {
        "raw_digit", "bracket_reference", "latin_letters", "url"}


def test_normalization_is_deterministic():
    text = "في عام 2011 ارتفع بنسبة 3.5% وقال Gramsci ذلك.1"
    assert spoken(text) == spoken(text)


# ------------------------------------------------------------------ segmentation
def test_split_respects_sentences_and_bounds():
    text = "جملة أولى قصيرة. " * 40
    chunks = split_for_tts(text, target_chars=120, max_chars=200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(c.replace(" ", "") for c in chunks) == text.replace(" ", "")
    assert all(c.endswith(".") for c in chunks)


def test_split_long_sentence_without_punctuation_terminates():
    text = "كلمة " * 500
    chunks = split_for_tts(text, target_chars=100, max_chars=150)
    assert chunks and all(len(c) <= 150 for c in chunks)
    unbroken = "أ" * 1000  # no space at all: hard cut, still terminates
    chunks = split_for_tts(unbroken, target_chars=100, max_chars=150)
    assert all(len(c) <= 150 for c in chunks) and "".join(chunks) == unbroken


# ------------------------------------------------------------------ document builder
def unit(seq, text, kind="prose", markers=()):
    return TranslationUnit(unit_id=f"bk:u{seq:05d}", book_id="bk", block_ids=[f"bk:b{seq:05d}"], kind=kind,
                           source_text=text, footnote_markers=list(markers), pages=[1], section_path=["Ch"])


def accepted(seq, target, decided_by="policy", decision="accepted"):
    return UnitTranslation(unit_id=f"bk:u{seq:05d}", target_text=target if decision != "dropped" else "",
                           origin="engine", engine=ENGINE, qe=QEResult(deterministic_pass=True, route="auto_accept"),
                           decision=decision, decided_by=decided_by, decided_at=NOW)


def test_builder_traces_segments_to_units_and_strips_restored_markers():
    units = [unit(1, "Chapter 1", kind="heading"), unit(2, "Text 1.", markers=["7"]),
             unit(3, "Note.", kind="footnote"), unit(4, "Dropped", kind="prose")]
    translations = {"bk:u00001": accepted(1, "الفصل 1"), "bk:u00002": accepted(2, "في عام 2011 قال ذلك. 7"),
                    "bk:u00003": accepted(3, "حاشية."), "bk:u00004": accepted(4, "", decision="dropped")}
    doc = build_speech_text(units, translations, book_id="bk", source=SOURCE, lexicon=LEX)
    assert doc.frontend_version == f"{FRONTEND_VERSION}-fasih-xtts" and doc.lexicon_digest == LEX.digest
    assert [s.unit_ids for s in doc.segments] == [["bk:u00001"], ["bk:u00002"]]
    assert doc.segments[0].structure == "heading" and doc.segments[0].spoken_text == "الفصل الأول."
    assert doc.segments[0].pause_after_ms > doc.segments[1].pause_after_ms > 0
    assert doc.segments[1].spoken_text == "في عام ألفين وأحد عشر قال ذلك."
    assert "footnote_marker_removed" in doc.segments[1].rules_applied
    reasons = {e.unit_id: e.reason for e in doc.excluded}
    assert reasons == {"bk:u00003": "not_spoken_kind:footnote", "bk:u00004": "dropped"}


@pytest.mark.parametrize("decision", ["needs_review", "rejected"])
def test_builder_refuses_units_that_are_not_accepted(decision):
    units = [unit(1, "Text.")]
    bad = UnitTranslation(unit_id="bk:u00001", target_text="نص" if decision != "rejected" else "", origin="engine",
                          engine=ENGINE, decision=decision, decided_by="policy", decided_at=NOW)
    with pytest.raises(NotSpeechReady):
        build_speech_text(units, {"bk:u00001": bad}, book_id="bk", source=SOURCE, lexicon=LEX)


def test_builder_refuses_missing_results():
    with pytest.raises(NotSpeechReady):
        build_speech_text([unit(1, "Text.")], {}, book_id="bk", source=SOURCE, lexicon=LEX)


def test_builder_accepts_human_reviewed_units():
    t = UnitTranslation(unit_id="bk:u00001", target_text="نص مراجع.", origin="human", decision="accepted",
                        decided_by="human", decided_at=NOW)
    doc = build_speech_text([unit(1, "Text.")], {"bk:u00001": t}, book_id="bk", source=SOURCE, lexicon=LEX)
    assert doc.segments[0].spoken_text == "نص مراجع."


def test_builder_excludes_wrong_language_unit():
    units = [unit(1, "Text."), unit(2, "More.")]
    translations = {"bk:u00001": accepted(1, "This text was never translated into Arabic at all."),
                    "bk:u00002": accepted(2, "نص عربي.")}
    doc = build_speech_text(units, translations, book_id="bk", source=SOURCE, lexicon=LEX)
    assert [e.reason for e in doc.excluded] == ["wrong_language"]
    assert [s.unit_ids for s in doc.segments] == [["bk:u00002"]]


def test_long_unit_is_split_into_bounded_segments_with_same_provenance():
    long = "هذه جملة عربية طويلة نسبيا تتكرر. " * 30
    doc = build_speech_text([unit(1, "Long.")], {"bk:u00001": accepted(1, long)}, book_id="bk",
                            source=SOURCE, lexicon=LEX, max_chars=200, target_chars=150)
    assert len(doc.segments) > 1
    assert all(s.unit_ids == ["bk:u00001"] and len(s.spoken_text) <= 200 for s in doc.segments)
    assert [s.seq for s in doc.segments] == list(range(len(doc.segments)))
    assert doc.segments[-1].pause_after_ms > doc.segments[0].pause_after_ms


def test_lexicon_digest_is_stable_and_content_addressed():
    again = PronunciationLexicon.from_mappings(entities={"Gramsci": "غرامشي", "Antonio Gramsci": "أنطونيو غرامشي"},
                                               terms={"CIA": "وكالة المخابرات المركزية", "hegemony": "الهيمنة"})
    assert again.digest == LEX.digest
    assert PronunciationLexicon.from_mappings(terms={"x": "س"}).digest != LEX.digest


# ------------------------------------------------------------------ gate
def test_gate_passes_clean_document():
    doc = build_speech_text([unit(1, "T.")], {"bk:u00001": accepted(1, "قال Gramsci في عام 2011 ذلك.")},
                            book_id="bk", source=SOURCE, lexicon=LEX)
    passed, metrics = evaluate_speech_text(doc)
    assert passed and metrics["violations"] == {} and metrics["lexicon_misses"] == 0
    assert metrics["segments"] == 1 and metrics["units_spoken"] == 1 and metrics["lexicon_hits"] == 1


def test_gate_fails_on_lexicon_misses_and_wrong_language():
    units = [unit(1, "T."), unit(2, "U.")]
    translations = {"bk:u00001": accepted(1, "قال Bourdieu ذلك."),
                    "bk:u00002": accepted(2, "This sentence stayed in English after translation, sadly.")}
    doc = build_speech_text(units, translations, book_id="bk", source=SOURCE, lexicon=LEX)
    passed, metrics = evaluate_speech_text(doc)
    assert not passed
    assert metrics["lexicon_misses"] == 1 and metrics["missed_words"] == ["Bourdieu"]
    assert metrics["excluded"] == {"wrong_language": 1}


def test_gate_counts_violations_in_spoken_text():
    doc = build_speech_text([unit(1, "T.")], {"bk:u00001": accepted(1, "نص سليم.")}, book_id="bk",
                            source=SOURCE, lexicon=LEX)
    broken = doc.model_copy(update={"segments": [doc.segments[0].model_copy(update={"spoken_text": "نص 3 [4]"})]})
    passed, metrics = evaluate_speech_text(broken)
    assert not passed and metrics["violations"] == {"raw_digit": 1, "bracket_reference": 1}
