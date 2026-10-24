"""Deterministic book routing; no IO, model requests or worker changes.

Language confidence describes bounded script/word evidence, not calibrated
language-model probability. Verified artifacts must match source/config/language
before their stage names are supplied as available_stages.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field
from b2c_contracts import ParsedBook

Stage = Literal["ocr", "outline", "rag", "course", "questions", "translation", "audio"]
ORDER = ("ocr", "outline", "rag", "course", "questions", "translation", "audio")


class PageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    page_no: int = Field(ge=1)
    text: str = ""
    has_images: bool | None = None
    native_text_usable: bool | None = None
    ocr_processed: bool = False
    verified_blank: bool = False


class DocumentProfile(BaseModel):
    classification: Literal["digital", "scanned", "mixed", "unknown"]
    page_count: int
    digital_pages: list[int]
    scanned_pages: list[int]
    blank_pages: list[int]
    unknown_pages: list[int]
    ocr_required_pages: list[int]
    ocr_completed_pages: list[int]
    inspected_page_fraction: float


class StageDecision(BaseModel):
    stage: Stage
    action: Literal["run", "reuse", "skip", "await_user"]
    reason: str


class ProcessingPlan(BaseModel):
    source_language: str
    confidence: float
    language_basis: str
    target_output: Stage
    target_language: str | None
    document_profile: DocumentProfile
    stages: list[StageDecision]
    required_stages: list[Stage]
    skipped_stages: list[Stage]
    reused_stages: list[Stage]
    requires_user_input: bool
    reasons: list[str]


def detect_source_language(text: str) -> tuple[str, float, str]:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 40:
        return "und", 0.0, "insufficient_text"
    arabic = sum("ARABIC" in unicodedata.name(c, "") for c in letters)
    latin = sum("LATIN" in unicodedata.name(c, "") for c in letters)
    # Persian/Urdu-specific letters prevent assuming Arabic from script alone.
    if arabic / len(letters) >= .85 and not any(c in text for c in "پچژگٹڈڑںھے"):
        return "ar", round(arabic / len(letters), 3), "arabic_script_dominant"
    if arabic / len(letters) >= .15 and latin / len(letters) >= .15:
        return "mul", round(min(arabic, latin) / len(letters), 3), "mixed_scripts"
    words = re.findall(r"[a-z]+", text.lower())
    common = {"the", "and", "of", "to", "in", "is", "for", "with", "that", "this", "from", "are", "as", "on", "be"}
    matches = [w for w in words if w in common]
    if latin / len(letters) >= .9 and len(set(matches)) >= 4 and len(matches) / max(len(words), 1) >= .12:
        return "en", .9, "english_function_words"
    return "und", .0, "script_does_not_establish_language"


def _profile(pages: Sequence[PageEvidence]) -> DocumentProfile:
    numbers = [p.page_no for p in pages]
    if len(numbers) != len(set(numbers)):
        raise ValueError("duplicate page evidence")
    count = max(numbers, default=0)
    by_number = {p.page_no: p for p in pages}
    digital, scanned, blank, unknown, pending, completed = [], [], [], [], [], []
    for n in range(1, count + 1):
        p = by_number.get(n)
        if p is None:
            unknown.append(n)
        elif p.verified_blank:
            blank.append(n)
        elif p.ocr_processed:
            scanned.append(n)
            completed.append(n)
        elif p.native_text_usable is True:
            digital.append(n)
        elif p.native_text_usable is False and p.has_images is True:
            scanned.append(n)
            pending.append(n)
        else:
            unknown.append(n)
    classification = "unknown" if unknown else "mixed" if digital and scanned else "scanned" if scanned else "digital" if digital else "unknown"
    return DocumentProfile(classification=classification, page_count=count,
        digital_pages=digital, scanned_pages=scanned, blank_pages=blank,
        unknown_pages=unknown, ocr_required_pages=pending, ocr_completed_pages=completed,
        inspected_page_fraction=(count - len(unknown)) / count if count else 0)


def build_processing_plan(*, pages: Sequence[PageEvidence], target_output: Stage,
    target_language: str | None = None, source_language: str | None = None,
    available_stages: Sequence[Stage] = (), content_kind: Literal["book", "course", "questions"] = "course",
    include_questions: bool = True) -> ProcessingPlan:
    """Plan dependencies; OCR means extraction (native text or image recognition).

    Caller supplies source_language only after explicit user choice or verified
    metadata. available_stages contains validated, identity-matched artifacts.
    Target language is deliberately mandatory for translation/audio decisions:
    the planner never translates an Arabic book merely because UI defaults to AR.
    """
    if target_output not in ORDER or any(stage not in ORDER for stage in available_stages):
        raise ValueError("unsupported stage")
    if content_kind not in ("book", "course", "questions"):
        raise ValueError("unsupported content kind")
    if source_language == "und" or source_language == "mul":
        source_language = None
    detected, confidence, basis = detect_source_language("\n".join(p.text for p in pages))
    if source_language:
        detected, confidence, basis = source_language.lower(), 1., "explicit_source_language"
    profile = _profile(pages)
    available = set(available_stages)
    reasons: list[str] = []
    translation_needed = False
    translation_unknown = False
    if target_output in ("translation", "audio"):
        if not target_language:
            reasons.append("choose_output_language")
            translation_unknown = True
        elif detected in ("und", "mul"):
            reasons.append("confirm_source_language")
            translation_unknown = True
        else:
            translation_needed = detected.lower() != target_language.lower()
    content = "ocr" if content_kind == "book" else content_kind
    deps: dict[str, tuple[str, ...]] = {"ocr": (), "outline": ("ocr",), "rag": ("ocr",),
        "course": ("outline", "rag"), "questions": ("course",), "translation": (content,),
        "audio": (("translation",) if translation_needed else (content,))}
    if content_kind == "course" and include_questions and target_output in ("translation", "audio"):
        deps["translation"] = ("questions",)
        deps["audio"] = ("translation",) if translation_needed else ("questions",)
    needed: set[str] = set()
    reused: set[str] = set()
    def visit(stage: str) -> None:
        if stage in needed:
            return
        needed.add(stage)
        if stage in available:
            reused.add(stage)
            return
        for dependency in deps[stage]:
            visit(dependency)
    # Same-language translation request is satisfied by its selected content.
    root = content if target_output == "translation" and not translation_needed and not translation_unknown else target_output
    if target_output == "translation" and root == content and content_kind == "course" and include_questions:
        root = "questions"
    visit(root)
    if "ocr" in needed and "ocr" not in reused and profile.unknown_pages:
        reasons.append("inspect_unknown_pages")
    if "ocr" in needed and "ocr" not in reused and not pages:
        reasons.append("provide_source_or_verified_extraction")
    decisions = []
    for stage in ORDER:
        if translation_unknown and (stage == "translation" or (stage == "audio" and target_output == "audio")):
            decisions.append(StageDecision(stage=stage, action="await_user", reason="language_choice_required"))
        elif stage in reused:
            decisions.append(StageDecision(stage=stage, action="reuse", reason="verified_matching_artifact"))
        elif stage in needed:
            reason = "native_text_extraction" if stage == "ocr" and not profile.ocr_required_pages and not profile.unknown_pages else "required_dependency"
            decisions.append(StageDecision(stage=stage, action="run", reason=reason))
        else:
            reason = "source_matches_target_language" if stage == "translation" and target_language and detected == target_language else "not_needed_for_selected_output"
            decisions.append(StageDecision(stage=stage, action="skip", reason=reason))
    return ProcessingPlan(source_language=detected, confidence=confidence, language_basis=basis,
        target_output=target_output, target_language=target_language, document_profile=profile,
        stages=decisions, required_stages=[s.stage for s in decisions if s.action in ("run", "await_user")],
        skipped_stages=[s.stage for s in decisions if s.action == "skip"],
        reused_stages=[s.stage for s in decisions if s.action == "reuse"],
        requires_user_input=bool(reasons), reasons=reasons)


def plan_parsed_book(parsed_book: ParsedBook, target_output: Stage, target_language: str | None = None,
    *, source_language: str | None = None, available_stages: Sequence[Stage] = (),
    content_kind: Literal["book", "course", "questions"] = "course", include_questions: bool = True) -> ProcessingPlan:
    """Adapt validated ParsedBook without treating omitted pages as blank.

    OCR-completed pages are ready text; their original scan classification remains
    visible. No block on a page means unknown, requiring source inspection.
    """
    blocks: dict[int, list] = {}
    for block in parsed_book.blocks:
        for ref in block.refs:
            if ref.book_id != parsed_book.book_id or ref.page_no > parsed_book.page_count:
                raise ValueError("invalid ParsedBook page provenance")
            blocks.setdefault(ref.page_no, []).append(block)
    pages = []
    for n in range(1, parsed_book.page_count + 1):
        page_blocks = blocks.get(n, [])
        ocr = any(b.ocr for b in page_blocks)
        text = "\n".join(b.text for b in page_blocks)
        pages.append(PageEvidence(page_no=n, text=text, ocr_processed=ocr,
            native_text_usable=True if text.strip() and not ocr else None))
    return build_processing_plan(pages=pages, target_output=target_output, target_language=target_language,
        source_language=source_language, available_stages=available_stages, content_kind=content_kind,
        include_questions=include_questions)
