"""Original term at its first mention (D-032 principle 3; learning-companion §2.3).

Deterministic, post-translation: in an episode's Arabic text (book text and scripts, in reading
order) the first mention of each approved locked term first mentioned in the lesson becomes
"المقابل العربي (original)". Idempotent; the QA checks are told explicitly that the inserted Latin
is a sanctioned gloss (whitelist), so it never becomes a foreign-script / untranslated finding."""
from b2c_contracts import TranslationUnit
from b2c_translation.first_mention import (FirstMentionTerm, find_term_span, insert_first_mentions,
                                           strip_original_glosses)
from b2c_translation.qa import CheckContext
from b2c_translation.qa.checks import run_checks

HEG = FirstMentionTerm(original="hegemony", target="الهيمنة")
WOP = FirstMentionTerm(original="war of position", target="حرب المواقع")


def test_first_mention_per_episode_gets_the_original_once():
    texts = ["هذه الحلقة عن السلطة.", "وبالهيمنة يحكم الطرف الأقوى، والهيمنة تقوم على الرضا.",
             "حرب المواقع طويلة. الهيمنة مرة أخرى."]
    out = insert_first_mentions(texts, [HEG, WOP])
    assert out.texts[0] == texts[0]
    assert out.texts[1] == "وبالهيمنة (hegemony) يحكم الطرف الأقوى، والهيمنة تقوم على الرضا."
    assert out.texts[2] == "حرب المواقع (war of position) طويلة. الهيمنة مرة أخرى."
    assert out.inserted == [[], ["hegemony"], ["war of position"]] and out.missing == []


def test_idempotent_and_respects_an_existing_gloss():
    once = insert_first_mentions(["الهيمنة تحكم. الهيمنة."], [HEG]).texts
    twice = insert_first_mentions(once, [HEG])
    assert twice.texts == once and twice.inserted == [[]]
    engine_gloss = insert_first_mentions(["الهيمنة (Hegemony) تحكم."], [HEG])
    assert engine_gloss.texts == ["الهيمنة (Hegemony) تحكم."] and engine_gloss.inserted == [[]]


def test_terms_not_in_the_text_are_reported_and_latin_targets_skipped():
    out = insert_first_mentions(["نص بلا مصطلحات."], [HEG, FirstMentionTerm(original="ACH", target="ACH")])
    assert out.texts == ["نص بلا مصطلحات."] and out.missing == ["hegemony"]


def test_span_allows_clitics_and_article():
    assert find_term_span("وللهيمنة دور", "الهيمنة") == (0, 8)
    assert find_term_span("في حرب المواقع الطويلة", "حرب المواقع") == (3, 14)
    assert find_term_span("هيمنات", "الهيمنة") is None


def unit(text):
    return TranslationUnit(unit_id="b:u00001", book_id="b", block_ids=["b:1:0"], kind="prose", source_text=text,
                           pages=[1], section_path=["episode-script"])


def test_qa_whitelist_for_inserted_originals():
    # the original is NOT in this unit's source (the term reached it through the episode's first mention)
    src = unit("The ruling group keeps its power through consent rather than force alone.")
    target = "تحافظ الجماعة الحاكمة على سلطتها بالهيمنة (hegemony) والرضا لا بالقوة وحدها."
    flagged = run_checks(CheckContext(unit=src, target=target))
    assert any(i.check_id == "latin_not_in_source" for i in flagged)
    clean = run_checks(CheckContext(unit=src, target=target, glossed_originals=frozenset({"hegemony"})))
    assert not any(i.category in ("foreign_script", "untranslated_english") for i in clean)
    # only the whitelisted gloss is exempt: other stray Latin is still flagged
    other = run_checks(CheckContext(unit=src, target=target + " domination", glossed_originals=frozenset({"hegemony"})))
    assert any(i.check_id == "latin_not_in_source" for i in other)


def test_strip_original_glosses():
    assert strip_original_glosses("الهيمنة (hegemony) و(X)", {"hegemony"}) == "الهيمنة و(X)"


def test_a_later_gloss_is_not_the_first_mention():
    out = insert_first_mentions(["الهيمنة في النص.", "الهيمنة (hegemony): تعريف."], [HEG])
    assert out.texts == ["الهيمنة (hegemony) في النص.", "الهيمنة (hegemony): تعريف."]


def test_nested_terms_longest_first_and_idempotent():
    from b2c_translation.first_mention import first_mention_status
    sat = FirstMentionTerm(original="Structured Analytic Technique", target="تقنية التحليل المنظم")
    sa = FirstMentionTerm(original="structured analysis", target="التحليل المنظم")
    texts = ["تقوم تقنية التحليل المنظم على خطوات.", "ويعني التحليل المنظم إظهار التفكير."]
    out = insert_first_mentions(texts, [sa, sat])
    assert out.texts == ["تقوم تقنية التحليل المنظم (Structured Analytic Technique) على خطوات.",
                         "ويعني التحليل المنظم (structured analysis) إظهار التفكير."]
    assert insert_first_mentions(out.texts, [sa, sat]).texts == out.texts
    assert first_mention_status(out.texts, [sa, sat]) == {"Structured Analytic Technique": "glossed",
                                                          "structured analysis": "glossed"}
    assert first_mention_status(texts, [sa, sat])["structured analysis"] == "not_glossed"
