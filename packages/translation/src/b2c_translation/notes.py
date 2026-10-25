"""Translator notes from translation results (D-040; learning-companion proposal §4). Pure, no I/O.

There is no human reviewer in the product. A unit whose final decision is ``needs_review`` or
``rejected`` is neither hidden nor blocking: it becomes a ``TranslatorNote`` in place, in the
course voice, with

* the best available translation among the final text and the earlier drafts, labelled approximate;
* the original text;
* a short reason the learner understands (no QA vocabulary).

Choosing the best translation: a candidate with a critical issue (leaked instructions, empty or
truncated output, engine failure) or no text is never offered. The others are ranked by the
number of blocking deterministic issues, then all blocking issues, then minor issues; a tie keeps
the final translation (the policy's own last word), then the latest draft.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from b2c_contracts import (BEST_TRANSLATION_LABEL_AR, TRANSLATOR_NOTE_LEAD_AR, ArtifactRef, QACategory, QAIssue,
                           TranslationUnit, TranslatorNote, TranslatorNoteReason, TranslatorNotesDocument,
                           UnitTranslation)

NOTES_BUILDER_VERSION = "translator-notes-2026-10-25.1"
PUBLISHABLE = frozenset({"accepted", "dropped"})
ANNOTATED = frozenset({"needs_review", "rejected"})
BLOCKING = frozenset({"major", "critical"})

REASON_PRIORITY: tuple[TranslatorNoteReason, ...] = (
    "no_translation", "meaning", "completeness", "untranslated", "numbers", "names", "terminology", "wording",
    "unverified",
)
CATEGORY_REASON: dict[QACategory, TranslatorNoteReason] = {
    "meaning_error": "meaning", "polarity_error": "meaning", "prompt_leakage": "meaning",
    "omission": "completeness", "addition": "completeness", "repetition": "completeness",
    "segmentation": "completeness",
    "untranslated_english": "untranslated", "foreign_script": "untranslated",
    "number_error": "numbers", "date_error": "numbers", "footnote_marker": "numbers",
    "named_entity": "names", "bibliographic": "names", "quotation": "names",
    "terminology": "terminology",
    "grammar": "wording", "fluency_style": "wording", "structure_formatting": "wording", "other": "wording",
}
# What the learner hears the note is about, by unit kind ("ترجمة <noun>").
KIND_NOUN: dict[str, str] = {
    "heading": "هذا العنوان", "list_item": "هذا البند", "caption": "هذا التعليق", "table_cell": "هذه الخانة",
    "footnote": "هذه الحاشية", "reference": "هذا المرجع", "toc": "هذا المدخل", "index": "هذا المدخل",
}
DEFAULT_NOUN = "هذه الفقرة"
REASON_AR: dict[TranslatorNoteReason, str] = {
    "no_translation": "تعذّرت ترجمة {x} ترجمةً موثوقة.",
    "meaning": "قد لا تنقل ترجمة {x} معنى الأصل بدقة.",
    "completeness": "قد تكون ترجمة {x} ناقصة أو زائدة عن الأصل.",
    "untranslated": "بقي في ترجمة {x} كلام غير مترجم.",
    "numbers": "قد يختلف رقم أو تاريخ أو إحالة في ترجمة {x} عن الأصل.",
    "names": "قد يكون اسم علم أو عنوان في ترجمة {x} مكتوبًا بغير صيغته المعتمدة.",
    "terminology": "قد لا يكون مصطلح في ترجمة {x} بمقابله المعتمد في هذا الكورس.",
    "wording": "قد تحتاج صياغة ترجمة {x} إلى تحسين.",
    "unverified": "تعذّر التحقق من دقة ترجمة {x}.",
}
WITH_TRANSLATION_AR = "وما يلي ترجمة تقريبية."
WITHOUT_TRANSLATION_AR = "والنص الأصلي محفوظ في النسخة المكتوبة من الكورس."

Candidate = tuple[str, Literal["final", "earlier_draft"], tuple[QAIssue, ...]]


def reasons_for(issues: Iterable[QAIssue]) -> list[TranslatorNoteReason]:
    """Learner reasons from the blocking issues, in priority order; none -> ``unverified``."""
    found = {CATEGORY_REASON.get(i.category, "wording") for i in issues if i.severity in BLOCKING}
    return [r for r in REASON_PRIORITY if r in found] or ["unverified"]


def _draft_issues(draft: Mapping[str, Any]) -> tuple[QAIssue, ...]:
    return tuple(QAIssue.model_validate(i) for i in draft.get("issues") or ())


def _rank(candidate: Candidate) -> tuple[int, int, int]:
    _, _, issues = candidate
    det = sum(1 for i in issues if i.severity in BLOCKING and i.origin == "deterministic")
    blocking = sum(1 for i in issues if i.severity in BLOCKING)
    return det, blocking, len(issues) - blocking


def best_translation(translation: UnitTranslation,
                     drafts: Sequence[Mapping[str, Any]] = ()) -> Candidate | None:
    """The translation worth offering as approximate, or None when no candidate is fit."""
    candidates: list[Candidate] = [(translation.target_text, "final", tuple(translation.issues))]
    candidates += [(str(d.get("text") or ""), "earlier_draft", _draft_issues(d)) for d in reversed(drafts)]
    usable = [c for c in candidates
              if c[0].strip() and not any(i.severity == "critical" for i in c[2])]
    if not usable:
        return None
    # min() keeps the first of equal ranks: the final translation, then the latest draft.
    best = min(usable, key=_rank)
    if best[1] == "earlier_draft" and best[0] == translation.target_text:
        best = (best[0], "final", tuple(translation.issues))
    return best


def note_for_unit(unit: TranslationUnit, translation: UnitTranslation,
                  drafts: Sequence[Mapping[str, Any]] = (), *, source_language: str = "en") -> TranslatorNote:
    if translation.unit_id != unit.unit_id:
        raise ValueError("the translation belongs to another unit")
    if translation.decision not in ANNOTATED:
        raise ValueError(f"a {translation.decision} unit takes no translator note")
    best = best_translation(translation, drafts)
    if best is None:
        reasons: list[TranslatorNoteReason] = ["no_translation"] + [
            r for r in reasons_for(translation.issues) if r != "unverified"]
    else:
        reasons = reasons_for(best[2])
    noun = KIND_NOUN.get(unit.kind, DEFAULT_NOUN)
    reason_ar = REASON_AR[reasons[0]].format(x=noun)
    tail = WITH_TRANSLATION_AR if best is not None else WITHOUT_TRANSLATION_AR
    return TranslatorNote(
        note_id=f"{unit.unit_id}:note", unit_id=unit.unit_id, book_id=unit.book_id, block_ids=list(unit.block_ids),
        pages=list(unit.pages), section_path=list(unit.section_path), unit_kind=unit.kind,
        unit_decision=translation.decision, reasons=reasons, reason_ar=reason_ar,
        source_language=source_language, source_text=unit.source_text,
        best_translation=None if best is None else best[0].strip(),
        best_translation_label_ar=None if best is None else BEST_TRANSLATION_LABEL_AR,
        best_translation_from=None if best is None else best[1],
        announcement_ar=f"{TRANSLATOR_NOTE_LEAD_AR} {reason_ar} {tail}")


def build_translator_notes(units: Sequence[TranslationUnit], translations: Mapping[str, UnitTranslation],
                           drafts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None, *,
                           run_id: str | None = None, units_ref: ArtifactRef | None = None,
                           results_digest: str | None = None, source_language: str = "en"
                           ) -> TranslatorNotesDocument:
    """Account for every unit of one book: accepted, dropped, annotated (a note), or pending
    (no final result yet, listed by id). Results of units outside ``units`` are ignored."""
    if not units:
        raise ValueError("no units: nothing to account for")
    book_ids = {u.book_id for u in units}
    if len(book_ids) != 1:
        raise ValueError("the units must belong to exactly one book")
    drafts = drafts or {}
    counts = {"accepted": 0, "dropped": 0}
    notes: list[TranslatorNote] = []
    pending: list[str] = []
    for unit in units:
        translation = translations.get(unit.unit_id)
        if translation is None:
            pending.append(unit.unit_id)
        elif translation.decision in PUBLISHABLE:
            counts[translation.decision] += 1
        else:
            notes.append(note_for_unit(unit, translation, drafts.get(unit.unit_id, ()),
                                       source_language=source_language))
    return TranslatorNotesDocument(
        book_id=book_ids.pop(), run_id=run_id, builder_version=NOTES_BUILDER_VERSION, units_ref=units_ref,
        results_digest=results_digest, total_units=len(units), accepted=counts["accepted"],
        dropped=counts["dropped"], annotated=len(notes), pending=len(pending), notes=notes,
        unannotated_unit_ids=pending)


__all__ = ["ANNOTATED", "CATEGORY_REASON", "NOTES_BUILDER_VERSION", "PUBLISHABLE", "REASON_AR", "REASON_PRIORITY",
           "best_translation", "build_translator_notes", "note_for_unit", "reasons_for"]
