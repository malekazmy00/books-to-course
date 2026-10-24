"""Deterministic Phase 7 primitives; approvals and QE acceptance remain explicit."""
from __future__ import annotations
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from b2c_contracts import GlossaryTerm, QEResult, TermbaseEntry
_WORD = re.compile(r"\b[\w]+(?:['][\w]+)*\b", re.UNICODE)
_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[,.]\d+)*(?:%|(?=\W|$))")
_SPACE = re.compile(r"\s+")

def _norm(s: str) -> str:
    return _SPACE.sub(" ", s).strip().casefold()

def word_count(text: str) -> int:
    return len(_WORD.findall(text))

@dataclass(frozen=True, slots=True)
class TextChunk:
    index: int
    text: str
    words: int

def chunk_text(text: str, *, target_words: int = 500) -> list[TextChunk]:
    """Pack paragraphs; split long paragraphs by sentence, retaining oversized sentences."""
    if target_words < 1:
        raise ValueError("target_words must be positive")
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    units: list[str] = []
    for p in paragraphs:
        if word_count(p) <= target_words:
            units.append(p)
            continue
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
        for sentence in sentences:
            n = word_count(sentence)
            if n > target_words and not re.search(r"[.!?][\"'=)]?$", sentence):
                words = sentence.split()
                units.extend(" ".join(words[i:i + target_words])
                             for i in range(0, len(words), target_words))
            else:
                units.append(sentence)
    chunks: list[str] = []
    current: list[str] = []
    count = 0
    for unit in units:
        n = word_count(unit)
        if current and count + n > target_words:
            chunks.append("\n\n".join(current))
            current, count = [], 0
        current.append(unit)
        count += n
    if current:
        chunks.append("\n\n".join(current))
    return [TextChunk(i, s, word_count(s)) for i, s in enumerate(chunks)]

def build_termbase(glossary: Sequence[GlossaryTerm], arabic_terms: Mapping[str, str],
                   *, approved_by: str | None = None) -> list[TermbaseEntry]:
    """Attach supplied mappings; never translate or approve terms implicitly."""
    mapping = {_norm(k): v.strip() for k, v in arabic_terms.items()}
    seen: set[str] = set()
    entries: list[TermbaseEntry] = []
    for term in glossary:
        key = _norm(term.term_en)
        if not key or key in seen:
            raise ValueError(f"duplicate or empty English glossary term: {term.term_en!r}")
        seen.add(key)
        arabic = mapping.get(key)
        if not arabic:
            raise ValueError(f"missing Arabic term mapping: {term.term_en}")
        entries.append(TermbaseEntry(term_en=term.term_en.strip(), term_ar=arabic,
            definition_en=term.definition_en.strip(), approved_by=approved_by))
    if set(mapping) - seen:
        raise ValueError(f"Arabic mappings have no glossary source: {sorted(set(mapping)-seen)}")
    return entries

def termbase_gate(termbase: Sequence[TermbaseEntry]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    seen: set[str] = set()
    for entry in termbase:
        key = _norm(entry.term_en)
        if not key or key in seen:
            errors.append(f"duplicate_or_empty_source:{entry.term_en}")
        seen.add(key)
        if not entry.term_ar.strip():
            errors.append(f"empty_translation:{entry.term_en}")
        if not entry.approved_by:
            errors.append(f"approval_missing:{entry.term_en}")
    return not errors, errors

def deterministic_translation_check(source: str, target: str,
                                    termbase: Sequence[TermbaseEntry]) -> QEResult:
    """Check glossary/numeric invariants; never auto-accept before QE calibration and O-1."""
    violations: list[str] = []
    for entry in termbase:
        if re.search(r"(?<!\w)" + re.escape(entry.term_en) + r"(?!\w)", source, re.I):
            if entry.term_ar not in target:
                violations.append(f"missing_term:{entry.term_en}")
        if any(v and v in target for v in entry.forbidden_variants_ar):
            violations.append(f"forbidden_variant:{entry.term_en}")
    if _NUMBER.findall(source) != _NUMBER.findall(target):
        violations.append("numeric_token_mismatch")
    return QEResult(deterministic_pass=not violations, term_violations=violations,
                    route="human_review")

def translation_needs_review(qe: QEResult, *, review: Mapping[str, object] | None) -> bool:
    if qe.route == "auto_accept":
        return False
    return not bool(review and review.get("action") in {"accept", "edit"})


def render_term_instructions(source_chunk: str, termbase: Sequence[TermbaseEntry]) -> list[TermbaseEntry]:
    """Return approved entries actually present in the chunk for prompt-time injection.

    Unapproved or structurally incomplete termbases fail closed; no system-generated mapping
    is allowed to silently pass Gate G3.
    """
    passed, errors = termbase_gate(termbase)
    if not passed:
        raise ValueError(f"termbase has not passed G3: {errors}")
    selected = []
    for entry in termbase:
        if re.search(r"(?<!\w)" + re.escape(entry.term_en) + r"(?!\w)", source_chunk, re.I):
            selected.append(entry)
    return selected


def term_injection_block(source_chunk: str, termbase: Sequence[TermbaseEntry]) -> str:
    """Render the exact approved mappings for an LLM prompt; empty if no terms occur."""
    entries = render_term_instructions(source_chunk, termbase)
    if not entries:
        return ""
    lines = ["Use these approved glossary translations exactly:"]
    lines.extend(f"- {entry.term_en} => {entry.term_ar}" for entry in entries)
    return "\n".join(lines)

# Licensed domain glossary integration used by the translation pipeline.
# Re-exported for callers that import them from b2c_translation.pipeline; kept at the
# bottom because agrovoc/multisource_terms import helpers defined above.
from .agrovoc import AgrovocCompilation, AgrovocIndex, compile_agrovoc_termbase  # noqa: E402,F401
from .multisource_terms import (  # noqa: E402,F401
    MultiSourceCompilation, WikidataConceptIndex, compile_multisource_termbase)
