"""Named-entity check v2 (D-046). The pairs below are short excerpts of real drafts from the two
running books (book-main: run 66f03cab, book 2: run 8f288079), classified in
eval/reports/named-entity-check.md. Each false-alarm class has a test that it no longer alarms;
each real error found there has a test that it is still caught.
"""
from __future__ import annotations

import pytest

from b2c_contracts import TranslationUnit
from b2c_translation.book_pipeline import UnitTranslator
from b2c_translation.qa import CheckContext
from b2c_translation.qa import checks as C
from b2c_translation.qa.checks import contains_name, run_checks
from b2c_translation.references import render_reference


def _ctx(make_unit, source, target, entities, *, kind="prose", terms=None, hints=None):
    unit = make_unit(source, kind=kind)
    return CheckContext(unit=unit, target=target, entities=entities, terms=terms or {}, hints=hints or {})


def _flags(make_unit, source, target, entities, **kw):
    return [i.evidence["term"] for i in C.named_entity(_ctx(make_unit, source, target, entities, **kw))]


HEUER = {"Richards J. Heuer Jr.": "ريتشاردز جيه. هوير جونيور", "Heuer": "هوير"}
PHERSON = {"Randolph H. Pherson": "راندولف إتش. فيرسون", "Pherson": "فيرسون",
           "Pherson Associates": "فيرسون أسوشيتس"}


# ------------------------------------------------------------------ references: verbatim by rule
ARMSTRONG = ('1. J. Scott Armstrong, "Combining Forecasts," in Principles of Forecasting, ed. J. Scott '
             'Armstrong (New York: Springer, 2001), 418–439.')


def test_reference_unit_is_not_checked_for_names_or_terms(make_unit):
    # book-main:u00787 — the reference stays in its original language (D-028, ACRPS), so an Arabic
    # rendering of a name or a term can never be in it.
    ctx = _ctx(make_unit, ARMSTRONG, render_reference(ARMSTRONG),
               {"J. Scott Armstrong": "جيه. سكوت أرمسترونغ", "New York": "نيويورك"}, kind="reference",
               terms={"forecasting": "التنبؤ"}, hints={"forecasts": "التوقعات"})
    assert C.named_entity(ctx) == []
    assert C.terminology(ctx) == []
    assert C.terminology_hint(ctx) == []


def test_index_unit_is_not_checked_for_names(make_unit):
    ctx = _ctx(make_unit, "Heuer, Richards J., 12, 45", "Heuer, Richards J., 12, 45", HEUER, kind="index")
    assert C.named_entity(ctx) == []


def test_reference_unit_is_accepted_by_rule_with_an_entity_table(make_unit):
    class _NoEngine:
        identity = None
        capabilities = None

    unit = TranslationUnit(unit_id="bk:u00787", book_id="bk", block_ids=["b1"], kind="reference",
                           source_text=ARMSTRONG, footnote_markers=[], pages=[99], section_path=["Notes"])
    tr = UnitTranslator(_NoEngine(), entities={"J. Scott Armstrong": "جيه. سكوت أرمسترونغ"},
                        terms={"forecasting": "التنبؤ"})
    result = tr.translate(unit)
    assert result.translation.decision == "accepted"
    assert "J. Scott Armstrong" in result.translation.target_text


def test_a_translated_unit_is_still_checked(make_unit):
    # the same name in running text (prose) must be rendered with the approved Arabic form
    assert _flags(make_unit, "As J. Scott Armstrong showed,", "كما بيّن J. Scott Armstrong،",
                  {"J. Scott Armstrong": "جيه. سكوت أرمسترونغ"}) == ["J. Scott Armstrong"]


# ------------------------------------------------------------------ Arabic orthography
@pytest.mark.parametrize("expected,target", [
    ("أسامة", "اسامه"),          # hamza on alif, taa marbuta / haa
    ("موسى", "موسي"),            # alif maqsura / yaa
    ("رؤوف", "رءوف"),            # hamza seat (waw / line)
    ("فائق", "فايق"),            # hamza seat (yaa) / plain yaa
    ("فيرسون", "فِيرْسُون"),        # harakat
    ("فيرسون", "فيـرسون"),        # tatweel
    ("فيليبيني", "فيليپيني"),     # Persian letter for p (book 2:u00020, Filippini)
])
def test_orthographic_variants_match(expected, target):
    assert contains_name(f"قال {target} ذلك.", expected)


def test_proclitics_and_article_reuse_the_term_matcher():
    assert contains_name("ووفقًا لراندولف إتش. فيرسون", "راندولف إتش. فيرسون")
    assert contains_name("وبجامعة هارفارد", "جامعة هارفارد")


# ------------------------------------------------------------------ initials
@pytest.mark.parametrize("source,target,entities", [
    ("Randolph H. Pherson", "راندولف هـ. فيرسون", PHERSON),                        # book-main:u00003
    ("by Richards J. Heuer Jr. and", "ريتشاردز ج. هوير جونيور", HEUER),               # book-main:u00312
    ("—Dr. Patrick F. Walsh, Associate Professor", "—د. باتريك ف. والش، أستاذ مشارك",
     {"Patrick F. Walsh": "باتريك إف. والش"}),                                      # book-main:u00013
    ("A. H. Van de Ven", "أ. هـ. فان دي فين", {"A. H. Van de Ven": "إيه. إتش. فان دي فين"}),
])
def test_an_initial_may_be_the_single_arabic_letter(make_unit, source, target, entities):
    assert _flags(make_unit, source, target, entities) == []


def test_a_wrong_initial_is_still_caught(make_unit):
    assert _flags(make_unit, "Randolph H. Pherson", "راندولف جيه. فيرسون", PHERSON) == ["Randolph H. Pherson"]


# ------------------------------------------------------------------ long vowels written or not
@pytest.mark.parametrize("source,target,entities", [
    ("Herbert Simon", "هيربرت سايمون", {"Herbert Simon": "هربرت سايمون"}),             # book-main:u01306
    ("Daniel Kahneman", "دانيال كانيمان", {"Daniel Kahneman": "دانيال كانمان"}),         # book-main:u01305
    ("Amos Tversky", "أموس تفرسكي", {"Amos Tversky": "آموس تفيرسكي"}),                # book-main:u00571
    ("Randolph Pherson", "راندولف فييرسون", {"Randolph Pherson": "راندولف فيرسون"}),    # book-main:u00274
    ("Albert S. Elstein", "ألبرت إس. إلستين", {"Albert S. Elstein": "ألبرت إس. إلستاين"}),  # u02058
    ("Mercyhurst College", "كلية ميرسيهيرست", {"Mercyhurst College": "كلية ميرسيهرست"}),  # u02101
    ("Laclau", "لاكلو", {"Laclau": "لاكلاو"}),                                         # book 2:u00122
    ("Michael Gilsenan", "مايكل جيلسينان", {"Michael Gilsenan": "مايكل جيلسنان"}),       # book 2:u00023
    ("Heuer", "هيوير", HEUER),                                                         # book-main:u00011
])
def test_a_long_vowel_written_or_omitted_matches(make_unit, source, target, entities):
    assert _flags(make_unit, source, target, entities) == []


@pytest.mark.parametrize("source,target,entities", [
    ("Craig", "كريج", {"Craig": "كريغ"}),                                             # book-main:u01717
    ("J. Scott Armstrong", "ج. سكوت أرمسترونج", {"J. Scott Armstrong": "جيه. سكوت أرمسترونغ"}),  # u00329
])
def test_hard_g_written_with_jeem_matches(make_unit, source, target, entities):
    assert _flags(make_unit, source, target, entities) == []


def test_only_one_long_vowel_may_differ(make_unit):
    # two vowel letters missing is a different reading, not a spelling variant
    assert _flags(make_unit, "Pherson", "فرسن", PHERSON) == ["Pherson"]


def test_jeem_for_a_j_is_not_relaxed_to_ghain(make_unit):
    assert _flags(make_unit, "George", "غورغ", {"George": "جورج"}) == ["George"]


# ------------------------------------------------------------------ real errors stay caught
@pytest.mark.parametrize("source,target,entities", [
    # vowel letters swapped: reads "Hayyur", not the approved "Hoyer" (book-main:u00004 and 40 others)
    ("Richards J. Heuer Jr.", "ريتشاردز ج. هيور جونيور", HEUER),
    # final letter dropped: Richards -> Richard (book-main:u00004, draft 3)
    ("RICHARDS J. HEUER JR.", "ريتشارد جيه. هوير جونيور", HEUER),
    # letter added to the name: Bardawil -> Bardawili (book 2:u00022)
    ("criticisms by Fadi Bardawil, Vincent", "انتقادات فادي بردويلي، وفينسنت", {"Fadi Bardawil": "فادي بردويل"}),
    # consonant dropped: Madhok -> Madok (book 2:u00022)
    ("Sumi Madhok", "وسومي مادوك", {"Sumi Madhok": "سومي مادهوك"}),
    # wrong reading of an Italian name (book 2:u00020)
    ("Patrizia Manduchi", "وباتريسيا ماندوتشي", {"Patrizia Manduchi": "باتريتسيا ماندوكي"}),
    ("Michele Filippini", "وميشيل فيليپيني", {"Michele Filippini": "ميكيلي فيليبيني"}),
    # name missing from the translation (book-main:u00003, draft 2)
    ("Richards J. Heuer Jr.\n\nRandolph H. Pherson", "ريتشاردز جيه. هوير جونيور", {**HEUER, **PHERSON}),
    # locked rendering cut short (book-main:u01902)
    ("selecting the first answer (Satisficing), and", "واختيار أول إجابة (الاكتفاء)، و",
     {"Satisficing": "الاكتفاء بالحل المرضي"}),
    # idafa instead of the locked adjective form (book-main:u00602)
    ("such as confirmation bias, Groupthink, and", "مثل التحيز التأكيدي، وتفكير الجماعة، و",
     {"Groupthink": "التفكير الجماعي"}),
])
def test_real_name_errors_are_still_caught(make_unit, source, target, entities):
    assert _flags(make_unit, source, target, entities)


@pytest.mark.parametrize("kind", ["prose", "heading", "list_item", "toc"])
def test_a_name_left_in_latin_is_caught_where_it_is_translated(make_unit, kind):
    # book-main:u01356 (prose), u00112 (toc): the engine kept the trademark in Latin
    assert _flags(make_unit, "6.4 Circleboarding™", "6.4 ™Circleboarding",
                  {"Circleboarding™": "اللوحة الدائرية"}, kind=kind) == ["Circleboarding™"]


# ------------------------------------------------------------------ source spelling in citations
@pytest.mark.parametrize("source,target", [
    ("Source: Pherson Associates, LLC, 2019.", "المصدر: Pherson Associates, LLC, 2019."),   # caption u00521
    ("4. For a more detailed discussion, see Randolph H. Pherson, “Five Habits of the Master Thinker,”",
     "4. لمناقشة أكثر تفصيلاً لهذا الموضوع، انظر Randolph H. Pherson، \"العادات الخمس للمفكر البارع\"،"),  # u00790
])
@pytest.mark.parametrize("kind", ["caption", "footnote"])
def test_a_citation_may_keep_the_source_spelling(make_unit, source, target, kind):
    assert _flags(make_unit, source, target, PHERSON, kind=kind) == []


def test_a_citation_with_a_misspelled_arabic_name_is_still_caught(make_unit):
    assert _flags(make_unit, "Source: Pherson Associates, LLC, 2019.", "المصدر: فرسن أسوشيتس، 2019.",
                  PHERSON, kind="caption") == ["Pherson Associates"]


# ------------------------------------------------------------------ the source must name the entity
def test_a_common_noun_is_not_the_entity(make_unit):
    # book 2:u00110: "convention hall" is not Stuart Hall
    assert _flags(make_unit, "connected to the convention hall, as in the case of Bernie Sanders",
                  "ربط الشوارع بقاعة المؤتمرات، كما هو الحال مع بيرني ساندرز", {"Hall": "هول"}) == []
    # book 2:u00082: Alaa Abd El-Fattah's own 'prison notebooks' are not Gramsci's Prison Notebooks
    assert _flags(make_unit, "in his own 'prison notebooks':", "في \"مذكرات سجنه\" الخاصة به:",
                  {"Prison Notebooks": "دفاتر السجن"}) == []


def test_capitalised_and_all_caps_sources_are_still_checked(make_unit):
    assert _flags(make_unit, "Stuart Hall argued", "جادل ستيوارت هو", {"Hall": "هول"}) == ["Hall"]
    assert _flags(make_unit, "RICHARDS J. HEUER JR.", "ريتشاردز جيه. هيور جونيور", HEUER)


def test_a_name_inside_a_longer_checked_name_is_reported_once(make_unit):
    # book-main:u00003: "Pherson" was reported next to "Randolph H. Pherson" for the same words
    assert _flags(make_unit, "Randolph H. Pherson", "راندولف إتش. فرسن", PHERSON) == ["Randolph H. Pherson"]
    # a separate mention of the short form is still checked on its own
    assert sorted(_flags(make_unit, "Randolph H. Pherson wrote it; Pherson also said",
                         "كتبه راندولف إتش. فرسن؛ وقال فرسن أيضًا", PHERSON)) == ["Pherson", "Randolph H. Pherson"]


# ------------------------------------------------------------------ inflection (D-039 matcher)
def test_a_nisba_adjective_from_the_name_matches(make_unit):
    # book 2:u00058: "a Gramsci revival" -> "نهضة غرامشية"
    assert _flags(make_unit, "There also has been a Gramsci revival", "كما حدثت نهضة غرامشية",
                  {"Gramsci": "غرامشي"}) == []


def test_one_letter_pronouns_are_not_stripped_as_suffixes():
    # the D-039 stemmer lists only multi-letter pronoun suffixes; a polarity-check set of the same
    # name used to shadow it, so "بردويلي" was read as "بردويل" + the pronoun "ي".
    assert not C.contains_term_inflected("فادي بردويلي", "فادي بردويل")
    assert C.contains_term_inflected("جماعتهم الاجتماعية", "جماعة اجتماعية")


def test_run_checks_keeps_the_named_entity_issue_major(make_unit):
    issues = run_checks(_ctx(make_unit, "Richards J. Heuer Jr.", "ريتشارد جيه. هوير جونيور", HEUER))
    found = [i for i in issues if i.check_id == "named_entity"]
    assert found and all((i.category, i.severity) == ("named_entity", "major") for i in found)
