"""Deterministic translation checks (D-028). Synthetic text only."""
from __future__ import annotations

import pytest

from b2c_translation.qa import checks as C
from b2c_translation.qa.checks import DEFAULT_CHECKS, run_checks


def ids(issues):
    return {i.check_id for i in issues}


def blocking(issues):
    return [i for i in issues if i.severity in ("critical", "major")]


# ------------------------------------------------------------------ registry
def test_registry_is_ordered_with_stable_unique_ids():
    check_ids = [c.check_id for c in DEFAULT_CHECKS]
    assert len(check_ids) == len(set(check_ids))
    assert check_ids[0] == "empty_target" and check_ids[1] == "prompt_leakage"
    for expected in ("foreign_script", "latin_not_in_source", "untranslated_english", "numbers",
                     "footnote_markers", "length_ratio", "sentence_alignment", "repetition",
                     "terminology", "named_entity", "brackets", "quotes", "polarity"):
        assert expected in check_ids
    for c in DEFAULT_CHECKS:
        assert c.categories and callable(c.run)


def test_every_issue_uses_the_check_id_and_a_declared_category(make_ctx):
    ctx = make_ctx("He did not pay 25 dollars to Smith (twice).", "دفع 26 دولارًا (مرتين 生成")
    issues = run_checks(ctx)
    declared = {c.check_id: set(c.categories) for c in DEFAULT_CHECKS}
    assert issues
    for issue in issues:
        assert issue.origin == "deterministic"
        assert issue.category in declared[issue.check_id]


GOOD_SOURCE = ("The committee did not approve the budget in 2019. It met again on 12 March. "
               "Smith said the plan was sound.")
GOOD_TARGET = ("لم توافق اللجنة على الميزانية في عام 2019. واجتمعت مرة أخرى في 12 مارس. "
               "وقال سميث إن الخطة سليمة.")


def test_a_faithful_translation_has_no_blocking_issue(make_ctx):
    ctx = make_ctx(GOOD_SOURCE, GOOD_TARGET, terms={"budget": "الميزانية"}, entities={"Smith": "سميث"})
    assert blocking(run_checks(ctx)) == []


# ------------------------------------------------------------------ empty
@pytest.mark.parametrize("target", ["", "   \n"])
def test_empty_target_is_critical_omission(make_ctx, target):
    issues = C.empty_target(make_ctx(GOOD_SOURCE, target))
    assert [(i.category, i.severity) for i in issues] == [("omission", "critical")]


# ------------------------------------------------------------------ scripts
@pytest.mark.parametrize("target", [
    "ثلاث تقنيات ل生成 الفرضيات",          # CJK
    "تم تحديدها على ידי أحد الأعضاء",      # Hebrew
    "ساحة هейماركت",                         # Cyrillic
    "كتاب ہے جديد",                          # Urdu letters
    "السطر الأول\\nالسطر الثاني",            # literal escape sequence
    "مايكل بانيسter",                        # Latin glued after Arabic letters
    "يمكن للanalysts النظر",                 # lowercase English word glued to Arabic
])
def test_foreign_script_is_flagged(make_ctx, target):
    issues = C.foreign_script(make_ctx("three techniques for generating hypotheses", target))
    assert issues and all(i.category == "foreign_script" and i.severity == "major" for i in issues)


def test_proclitic_before_a_latin_name_is_allowed(make_ctx):
    source = "See Heuer and the CIA report."
    assert C.foreign_script(make_ctx(source, "انظر Heuer وHeuer ولـCIA والCIA.")) == []


def test_foreign_script_present_in_source_is_allowed(make_ctx):
    source = "The word 生成 means generation."
    assert C.foreign_script(make_ctx(source, "الكلمة 生成 تعني التوليد.")) == []


def test_latin_tokens_from_the_source_are_allowed(make_ctx):
    source = "See Heuer (1999) and shop.example.com for the Key Assumptions Check."
    target = "انظر Heuer (1999) وshop.example.com لفحص الافتراضات الأساسية (Key Assumptions Check)."
    assert C.latin_not_in_source(make_ctx(source, target)) == []


def test_latin_tokens_absent_from_the_source_are_flagged(make_ctx):
    issues = C.latin_not_in_source(make_ctx("revolutionizing praxis", "ريفولوت i على i ز i نغ"))
    assert issues and issues[0].category == "foreign_script"
    assert "i" in issues[0].evidence["tokens"]


# ------------------------------------------------------------------ untranslated
def test_long_untranslated_run_is_flagged(make_ctx):
    source = "Gramsci as an original thinker of the modern state"
    issues = C.untranslated_english(make_ctx(source, "غرامشي as an original thinker of the modern state"))
    assert issues and issues[0].category == "untranslated_english" and issues[0].severity == "major"


def test_target_identical_to_english_source_is_untranslated(make_ctx):
    source = "Choosing the right technique"
    issues = C.untranslated_english(make_ctx(source, source, kind="heading"))
    assert issues and issues[0].severity == "major"


def test_short_names_and_citations_are_not_untranslated(make_ctx):
    source = "As Hall (1988) argues in Policing the Crisis, consent matters."
    target = "كما يرى هول (Hall, 1988) في كتاب Policing the Crisis، فإن الرضا مهم."
    assert C.untranslated_english(make_ctx(source, target)) == []


def test_parenthesised_original_language_gloss_is_not_untranslated(make_ctx):
    source = "See Selections from the Prison Notebooks of Antonio Gramsci for the argument."
    target = "انظر مختارات من دفاتر السجن (Selections from the Prison Notebooks of Antonio Gramsci) للاطلاع على الحجة."
    assert C.untranslated_english(make_ctx(source, target)) == []


def test_untranslated_check_skips_reference_units(make_ctx):
    source = "Hall, Stuart. Policing the Crisis. London: Macmillan, 1978."
    assert C.untranslated_english(make_ctx(source, source, kind="reference")) == []


# ------------------------------------------------------------------ numbers
@pytest.mark.parametrize("source,target", [
    ("between 1929 and 1935", "بين عامي ١٩٢٩ و١٩٣٥"),                    # Arabic-Indic digits
    ("between 1929 and 1935", "بين عامي ۱۹۲۹ و۱۹۳۵"),                    # Persian digits
    ("a rate of 3.5 percent", "بمعدل ٣٫٥ في المئة"),                      # Arabic decimal separator
    ("about 5,000 people", "نحو 5000 شخص"),                               # thousands separator
    ("about 5,000 people", "نحو ٥٬٠٠٠ شخص"),
    ("in the 1990s", "في التسعينيات"),                                     # decade as words
    ("in the 1990s", "في تسعينيات القرن العشرين (1990)"),
    ("the sixty-six techniques", "التقنيات الست والستون (66)"),           # number word -> digits ok
    ("pages 207-9", "الصفحات 207–209"),                                    # abbreviated range
    ("the 20th century", "القرن العشرين"),                                 # ordinal as words
    ("in 1999, 2001 and 1999", "في 1999 و2001 و1999"),
])
def test_number_allowances(make_ctx, source, target):
    assert C.numbers(make_ctx(source, target)) == []


@pytest.mark.parametrize("source,target,missing,extra", [
    ("between 1929 and 1935", "بين عامي 1929 و", ["1935"], []),
    ("after 1979", "بعد 1978", ["1979"], ["1978"]),
    ("in 1999, 2001 and 1999", "في 1999 و2001", ["1999"], []),        # multiset, not set
    ("the sixty-six techniques", "التقنيات التسع والستون (69)", [], ["69"]),
    ("in the 1970s", "ضمن 1971", [], ["1971"]),
])
def test_number_mismatches_are_major(make_ctx, source, target, missing, extra):
    issues = C.numbers(make_ctx(source, target))
    assert issues and all(i.category == "number_error" and i.severity == "major" for i in issues)
    ev = {k: v for i in issues for k, v in i.evidence.items()}
    assert ev.get("missing", []) == missing and ev.get("extra", []) == extra


@pytest.mark.parametrize("target", ["في 1970", "في منتصف ١٩٧٠"])
def test_decade_rendered_as_a_year_is_a_number_error(make_ctx, target):
    issues = C.numbers(make_ctx("in the 1970s", target))
    assert issues and issues[0].evidence["decade_as_year"] == ["1970"]


def test_extra_occurrence_of_a_source_number_is_only_minor(make_ctx):
    issues = C.numbers(make_ctx("Global Trends 2025.", "Global Trends 2025 [الاتجاهات العالمية 2025]."))
    assert [(i.severity, i.evidence) for i in issues] == [("minor", {"surplus": ["2025"]})]


def test_small_counts_like_scores_of_1s_are_not_decades(make_ctx):
    assert C.numbers(make_ctx("Give it 1s and 2s.", "أعطها 1 و2.")) == []


def test_numbers_ignore_restored_footnote_markers(make_ctx):
    ctx = make_ctx("It rose in 2010.", "ارتفع في عام 2010. 14", markers=["14"])
    assert C.numbers(ctx) == []


# ------------------------------------------------------------------ footnotes
def test_footnote_markers_restored_at_the_end_pass(make_ctx):
    ctx = make_ctx("It rose in 2010.", "ارتفع في عام 2010. 14 15", markers=["14", "15"])
    assert C.footnote_markers(ctx) == []


@pytest.mark.parametrize("target", [
    "ارتفع في عام 2010.",          # missing
    "ارتفع 14 في عام 2010.",       # displaced
    "ارتفع في عام 2010. 15 14",    # wrong order
    "ارتفع في عام 2010. 114",      # glued to another number
])
def test_footnote_marker_defects_are_major(make_ctx, target):
    issues = C.footnote_markers(make_ctx("It rose in 2010.", target, markers=["14"] if "15" not in target
                                         else ["14", "15"]))
    assert issues and issues[0].category == "footnote_marker" and issues[0].severity == "major"


# ------------------------------------------------------------------ length
def test_length_ratio_bounds_exist_per_kind():
    for kind in ("prose", "heading", "list_item", "caption", "table_cell", "footnote"):
        low, high = C.LENGTH_RATIO_BOUNDS[kind]
        assert 0 < low < 1 < high


def test_much_shorter_target_is_omission(make_ctx):
    issues = C.length_ratio(make_ctx(GOOD_SOURCE, "لم توافق اللجنة."))
    assert issues and issues[0].category == "omission"


def test_much_longer_target_is_addition(make_ctx):
    issues = C.length_ratio(make_ctx("The plan was sound.", "كانت الخطة سليمة " * 6))
    assert issues and issues[0].category == "addition"


def test_length_ratio_skips_very_short_sources(make_ctx):
    assert C.length_ratio(make_ctx("Index", "الفهرس الكامل للمفاهيم والأعلام", kind="heading")) == []


# ------------------------------------------------------------------ sentences
def test_dropped_sentences_are_flagged(make_ctx):
    source = ("The first claim is weak. The second claim is strong. The third claim is new. "
              "The fourth claim is old.")
    issues = C.sentence_alignment(make_ctx(source, "الادعاء الأول ضعيف."))
    assert issues and issues[0].category == "omission" and issues[0].severity in ("major", "critical")


def test_abbreviations_do_not_count_as_sentences(make_ctx):
    source = "See e.g. the U.S. report by J. Smith, pp. 4-5, and Dr. Brown's note."
    assert C.sentence_alignment(make_ctx(source, "انظر مثلًا التقرير الأمريكي لسميث، ص 4-5، وملاحظة براون.")) == []


# ------------------------------------------------------------------ repetition
def test_repeated_sentence_is_flagged(make_ctx):
    target = "وصفت التقنية بأنها مفيدة جدا للمحللين. وصفت التقنية بأنها مفيدة جدا للمحللين."
    issues = C.repetition(make_ctx("The technique was described as very useful for analysts.", target))
    assert issues and issues[0].category == "repetition"


def test_adjacent_duplicate_phrase_is_minor(make_ctx):
    issues = C.repetition(make_ctx("unlikely or unanticipated developments", "تطورات غير متوقعة أو غير متوقعة"))
    assert issues and issues[0].severity == "minor"


def test_repetition_mirrored_in_source_is_allowed(make_ctx):
    source = "Step by step, the plan was revised. Step by step, the plan was revised."
    target = "خطوة بخطوة، تمت مراجعة الخطة. خطوة بخطوة، تمت مراجعة الخطة."
    assert C.repetition(make_ctx(source, target)) == []


# ------------------------------------------------------------------ terms / entities
@pytest.mark.parametrize("target", [
    "يتشكل الحس المشترك عبر الزمن.",
    "وبالحس المشترك يتشكل الوعي.",     # و + ب + ال clitics
    "وللحس المشترك دور.",              # و + ل + ال -> لل
    "فالحسّ المشترك راسخ.",            # diacritics are ignored
])
def test_locked_term_present_with_clitics(make_ctx, target):
    ctx = make_ctx("Common sense is shaped over time.", target, terms={"common sense": "الحس المشترك"})
    assert C.terminology(ctx) == []


def test_locked_term_violation_is_major(make_ctx):
    ctx = make_ctx("Common sense is shaped over time.", "يتشكل الحس السليم عبر الزمن.",
                   terms={"common sense": "الحس المشترك"})
    issues = C.terminology(ctx)
    assert issues and (issues[0].category, issues[0].severity) == ("terminology", "major")
    assert issues[0].evidence["term"] == "common sense"


def test_terms_absent_from_source_are_not_required(make_ctx):
    ctx = make_ctx("The state is strong.", "الدولة قوية.", terms={"common sense": "الحس المشترك"})
    assert C.terminology(ctx) == []


def test_term_matching_handles_plural_and_case(make_ctx):
    # an English plural still triggers the requirement
    ctx = make_ctx("Subalterns cannot speak.", "المهمشون لا يستطيعون الكلام.", terms={"subaltern": "التابع"})
    assert C.terminology(ctx)
    ctx = make_ctx("The SUBALTERN cannot speak.", "التابع لا يستطيع الكلام.", terms={"subaltern": "التابع"})
    assert C.terminology(ctx) == []


def test_named_entity_table_is_enforced(make_ctx):
    ctx = make_ctx("If Tahrir Square had not existed", "لو لم يكن ميدان تهرير", entities={"Tahrir": "التحرير"})
    issues = C.named_entity(ctx)
    assert issues and (issues[0].category, issues[0].severity) == ("named_entity", "major")
    ok = make_ctx("If Tahrir Square had not existed", "لو لم يكن ميدان التحرير", entities={"Tahrir": "التحرير"})
    assert C.named_entity(ok) == []


# ------------------------------------------------------------------ brackets / quotes
def test_unbalanced_brackets_are_flagged(make_ctx):
    issues = C.brackets(make_ctx("the subaltern [who] cannot speak (here)", "التابع [الذي لا يستطيع الكلام (هنا))"))
    assert issues and issues[0].category == "structure_formatting"


def test_balanced_brackets_pass(make_ctx):
    assert C.brackets(make_ctx("the subaltern [who] cannot speak", "التابع [الذي] لا يستطيع الكلام")) == []


def test_unbalanced_quotes_are_minor(make_ctx):
    issues = C.quotes(make_ctx('He said "no" twice.', "قال «لا مرتين."))
    assert issues and (issues[0].category, issues[0].severity) == ("quotation", "minor")
    assert C.quotes(make_ctx('He said "no" twice.', "قال «لا» مرتين.")) == []


# ------------------------------------------------------------------ polarity
def test_dropped_negation_is_flagged(make_ctx):
    source = "Reference to these trademarks in no way indicates any relationship."
    issues = C.polarity(make_ctx(source, "تشير الإشارة إلى هذه العلامات بأي شكل إلى أي علاقة."))
    assert issues and (issues[0].category, issues[0].severity) == ("polarity_error", "major")


@pytest.mark.parametrize("target", [
    "ولا تعني الإشارة إلى هذه العلامات أي علاقة.",
    "لم تعنِ الإشارة أي علاقة.",
    "الإشارة ليست دليلًا على علاقة.",
    "إشارة غير دالة على علاقة.",
    "إشارة دون علاقة.",
    "عدم وجود علاقة.",
    "وبدونها تنقطع العلاقة.",
])
def test_arabic_negation_cues_satisfy_polarity(make_ctx, target):
    assert C.polarity(make_ctx("This does not indicate any relationship.", target)) == []


def test_negation_added_without_source_cue_is_flagged(make_ctx):
    issues = C.polarity(make_ctx("Many analysts believe it is time well spent.",
                                 "لا يعتقد كثير من المحللين أنه وقت لم يُحسن استثماره ولن يفيد."))
    assert issues and issues[0].category == "polarity_error"
