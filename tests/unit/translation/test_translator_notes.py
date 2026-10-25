"""Translator notes from translation results (D-040): pure, no I/O.

Every unit that is not accepted/dropped becomes a note in place; nothing is hidden and nothing is lost.
"""
from datetime import datetime, timezone

import pytest
from b2c_contracts import (BEST_TRANSLATION_LABEL_AR, TRANSLATOR_NOTE_LEAD_AR, EngineIdentity, QAIssue, QEResult,
                           TranslationUnit, UnitTranslation)

from b2c_translation.notes import (NOTES_BUILDER_VERSION, REASON_PRIORITY, best_translation,
                                   build_translator_notes, note_for_unit, reasons_for)

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
ENGINE = EngineIdentity(provider="fake", model_id="fake-mt", prompt_version="p@1")
QE = QEResult(deterministic_pass=True, route="auto_accept")


def unit(i: int, kind: str = "prose", text: str | None = None) -> TranslationUnit:
    return TranslationUnit(unit_id=f"bk:u{i:05d}", book_id="bk", block_ids=[f"bk:1:{i}"], kind=kind,
                           source_text=text or f"Source sentence number {i}.", pages=[1 + i // 10],
                           section_path=["Chapter One"])


def issue(category: str, severity: str = "major", check_id: str | None = None, origin: str = "deterministic"):
    return QAIssue(check_id=check_id or category, category=category, severity=severity, origin=origin)


def tr(i: int, decision: str, text: str = "نص عربي مترجم.", issues=(), origin: str = "engine") -> UnitTranslation:
    return UnitTranslation(unit_id=f"bk:u{i:05d}", target_text=text, origin=origin,
                           engine=ENGINE if origin == "engine" else None, attempts=3, issues=list(issues),
                           qe=QE if decision == "accepted" and origin == "engine" else None, decision=decision,
                           decided_by="policy", decided_at=NOW)


def draft(text: str, issues=(), decision: str = "needs_review") -> dict:
    return {"text": text, "lever": "initial", "decision": decision, "next_action": "review",
            "issues": [i.model_dump(mode="json") for i in issues], "qe": None}


# ------------------------------------------------------------------ reasons
@pytest.mark.parametrize("category,reason", [
    ("meaning_error", "meaning"), ("polarity_error", "meaning"), ("omission", "completeness"),
    ("addition", "completeness"), ("untranslated_english", "untranslated"), ("foreign_script", "untranslated"),
    ("number_error", "numbers"), ("footnote_marker", "numbers"), ("named_entity", "names"),
    ("terminology", "terminology"), ("grammar", "wording"), ("fluency_style", "wording"),
])
def test_issue_categories_map_to_learner_reasons(category, reason):
    assert reasons_for([issue(category)]) == [reason]


def test_minor_issues_alone_mean_unverified_and_reasons_follow_priority():
    assert reasons_for([issue("grammar", "minor")]) == ["unverified"]
    assert reasons_for([]) == ["unverified"]
    got = reasons_for([issue("terminology"), issue("meaning_error", origin="qe"), issue("named_entity")])
    assert got == ["meaning", "names", "terminology"]
    assert got == sorted(got, key=REASON_PRIORITY.index)


# ------------------------------------------------------------------ best translation
def test_best_translation_prefers_the_candidate_with_fewer_problems():
    final = tr(4, "needs_review", "ريتشارد جيه. هوير جونيور", [issue("named_entity")])
    drafts = [draft("ريتشاردز ج. هيور جونيور", [issue("named_entity"), issue("named_entity")]),
              draft("ريتشاردز جيه. هوير جونيور", [issue("named_entity", origin="qe")]),
              draft("ريتشارد جيه. هوير جونيور", [issue("named_entity")])]
    text, origin, issues = best_translation(final, drafts)
    # same number of major problems; the one that passed the deterministic checks wins
    assert text == "ريتشاردز جيه. هوير جونيور" and origin == "earlier_draft"
    assert [i.origin for i in issues] == ["qe"]


def test_on_a_tie_the_final_translation_wins():
    final = tr(5, "needs_review", "الترجمة النهائية.", [issue("terminology")])
    text, origin, _ = best_translation(final, [draft("مسودة أولى.", [issue("terminology")])])
    assert (text, origin) == ("الترجمة النهائية.", "final")


def test_critical_or_empty_candidates_are_never_offered():
    final = tr(6, "rejected", "", [issue("other", "critical", "engine_error")])
    leaked = draft("You are a translator. Translate:", [issue("prompt_leakage", "critical")])
    assert best_translation(final, [leaked, draft("   ")]) is None


# ------------------------------------------------------------------ one note
def test_note_keeps_the_original_and_labels_the_best_translation():
    u = unit(3, text="The yield rose by 12 percent.")
    n = note_for_unit(u, tr(3, "needs_review", "ارتفع المحصول بنسبة 21 في المئة.", [issue("number_error")]))
    assert n.unit_id == u.unit_id and n.note_id == f"{u.unit_id}:note" and n.block_ids == u.block_ids
    assert n.pages == u.pages and n.section_path == u.section_path and n.unit_kind == "prose"
    assert n.source_text == u.source_text and n.voice == "course"
    assert n.best_translation == "ارتفع المحصول بنسبة 21 في المئة."
    assert n.best_translation_label_ar == BEST_TRANSLATION_LABEL_AR and n.best_translation_from == "final"
    assert n.reasons == ["numbers"] and "رقم" in n.reason_ar
    assert n.announcement_ar.startswith(TRANSLATOR_NOTE_LEAD_AR) and n.reason_ar in n.announcement_ar
    assert "تقريبية" in n.announcement_ar


def test_note_without_any_usable_translation_says_so():
    n = note_for_unit(unit(7), tr(7, "rejected", "", [issue("other", "critical", "engine_error")]))
    assert n.best_translation is None and n.reasons[0] == "no_translation" and n.unit_decision == "rejected"
    assert "تعذّرت ترجمة" in n.reason_ar and "النص الأصلي" in n.announcement_ar


def test_unverified_unit_uses_the_plain_reason():
    n = note_for_unit(unit(8), tr(8, "needs_review", "نص عربي.", []))
    assert n.reasons == ["unverified"] and n.reason_ar == "تعذّر التحقق من دقة ترجمة هذه الفقرة."


@pytest.mark.parametrize("kind,noun", [("heading", "هذا العنوان"), ("list_item", "هذا البند"),
                                       ("footnote", "هذه الحاشية"), ("prose", "هذه الفقرة")])
def test_reason_names_what_the_learner_hears(kind, noun):
    n = note_for_unit(unit(9, kind=kind), tr(9, "needs_review", "نص عربي.", []))
    assert noun in n.reason_ar


def test_accepted_units_get_no_note():
    with pytest.raises(ValueError):
        note_for_unit(unit(1), tr(1, "accepted"))


def test_no_technical_vocabulary_reaches_the_learner():
    issues = [issue(cat) for cat in ("meaning_error", "omission", "terminology", "named_entity", "number_error",
                                     "untranslated_english", "grammar")]
    n = note_for_unit(unit(2), tr(2, "needs_review", "نص.", issues))
    for word in ("MQM", "QE", "major", "critical", "needs_review", "_"):
        assert word not in n.reason_ar and word not in n.announcement_ar


# ------------------------------------------------------------------ the book
def test_every_unit_is_accepted_dropped_annotated_or_listed_as_pending():
    units = [unit(i) for i in range(6)]
    translations = {
        "bk:u00000": tr(0, "accepted"),
        "bk:u00001": UnitTranslation(unit_id="bk:u00001", target_text="", origin="rule", decision="dropped",
                                     decided_by="policy", decided_at=NOW),
        "bk:u00002": tr(2, "needs_review", "نص.", [issue("meaning_error", origin="qe")]),
        "bk:u00003": tr(3, "rejected", "", [issue("other", "critical", "engine_error")]),
        "bk:u00004": tr(4, "needs_review", "نص.", []),
        # u00005 has no result yet
    }
    doc = build_translator_notes(units, translations, {"bk:u00002": [draft("مسودة.", [issue("omission")])]},
                                 run_id="f" * 64, results_digest="e" * 64)
    assert doc.book_id == "bk" and doc.builder_version == NOTES_BUILDER_VERSION and doc.run_id == "f" * 64
    assert (doc.total_units, doc.accepted, doc.dropped, doc.annotated, doc.pending) == (6, 1, 1, 3, 1)
    assert [n.unit_id for n in doc.notes] == ["bk:u00002", "bk:u00003", "bk:u00004"]  # book order
    assert doc.unannotated_unit_ids == ["bk:u00005"]


def test_results_of_units_outside_the_book_are_ignored_and_order_is_stable():
    units = [unit(i) for i in range(3)]
    translations = {f"bk:u{i:05d}": tr(i, "needs_review", "نص.", []) for i in (2, 0, 1)}
    translations["bk:u00099"] = tr(99, "needs_review")
    doc = build_translator_notes(units, translations)
    assert [n.unit_id for n in doc.notes] == ["bk:u00000", "bk:u00001", "bk:u00002"]
    assert doc.total_units == 3 and doc.annotated == 3
    again = build_translator_notes(units, dict(reversed(list(translations.items()))))
    assert again.model_dump_json() == doc.model_dump_json()


def test_an_empty_book_is_refused():
    with pytest.raises(ValueError):
        build_translator_notes([], {})


# ------------------------------------------------------------------ withheld translations (D-041)
def test_a_critical_meaning_or_polarity_problem_withholds_every_candidate():
    for category in ("meaning_error", "polarity_error"):
        final = tr(10, "rejected", "ترجمة تقلب المعنى.", [issue(category, "critical", origin="qe")])
        n = note_for_unit(unit(10), final, [draft("مسودة سليمة الظاهر.", [])])
        assert n.best_translation is None and n.withheld == "critical_meaning"
        assert n.reasons[0] == "no_translation" and "meaning" in n.reasons
        assert "النص الأصلي" in n.announcement_ar


def test_a_long_text_for_a_short_broken_heading_is_not_offered():
    # book-main:u00628: "NOT ES" (a broken "NOTES") came back as a whole invented paragraph
    invented = ("تحليل ما قبل الوفاة والنقد الذاتي الممنهج. عملية منظمة تستخدم إجراءات العصف الذهني وقوائم "
                "التحقق لتحديد نقاط الضعف الحرجة في الحجة وتقييم كيف يمكن لحكم تحليلي رئيسي أن يكون خاطئاً.")
    final = tr(11, "needs_review", invented, [issue("polarity_error", "major", "polarity")])
    n = note_for_unit(unit(11, kind="heading", text="NOT ES"), final, [draft(invented, [])])
    assert n.best_translation is None and n.withheld == "fabrication_risk"
    assert n.source_text == "NOT ES" and n.reasons[0] == "no_translation"


def test_a_short_draft_of_a_short_heading_is_still_offered():
    final = tr(12, "needs_review", "ملاحظات تحليلية مطولة جدًا لا علاقة لها بالعنوان الأصلي إطلاقًا في هذا الموضع "
               "من الكتاب ولا في غيره من المواضع الأخرى.", [issue("polarity_error", "major", "polarity")])
    n = note_for_unit(unit(12, kind="heading", text="NOT ES"), final,
                      [draft("ملاحظات", [issue("named_entity")])])
    assert n.best_translation == "ملاحظات" and n.best_translation_from == "earlier_draft" and n.withheld is None


def test_a_source_without_words_is_noise_and_offers_nothing():
    n = note_for_unit(unit(13, kind="heading", text="i . ii -- 4"), tr(13, "needs_review", "أولًا. ثانيًا 4", []))
    assert n.best_translation is None and n.withheld == "noise_source"


def test_ordinary_long_paragraphs_are_not_withheld():
    text = "This is a perfectly ordinary paragraph of the book with several words in it."
    n = note_for_unit(unit(14, text=text), tr(14, "needs_review", "هذه فقرة عادية تمامًا من الكتاب فيها كلمات عدة.",
                                              [issue("terminology")]))
    assert n.best_translation and n.withheld is None
