"""From the English script to the Arabic one, through the book's translation pipeline (D-035).

``script_units``: every generated line of an English script becomes a ``TranslationUnit`` of kind
``prose`` (no new unit kind: the contract is unchanged). Script units are marked as such — unit id
``{book_id}:ep{NNN}:u{seq:05d}``, block id ``{book_id}:episode{NNN}:{line_id}`` and section path
``["episode-script", NN, <section kind>, <line role>]`` — so they never mix with the book's units.
A key-term line sends only its definition: the term label is the approved Arabic term from the
book's termbase plus the original (``المقابل (original): ...``), the same rendering as everywhere else.

``assemble_arabic``: the Arabic script, line by line, from the unit decisions (orchestration runs
``UnitTranslator`` with the book's approved termbase and the run's engine/QE profiles). Book text is
not translated again: its lines are the ACCEPTED target text of the book's own translation units.
``with_texts`` replaces line texts after the post-translation first-mention pass.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from b2c_contracts import (BOOK_TEXT_KINDS, EpisodeScript, EpisodeSection, ScriptLine, ScriptSource, TranslationUnit,
                           UnitTranslation)

SCRIPT_MARKER = "episode-script"


@dataclass(frozen=True)
class ScriptUnit:
    unit: TranslationUnit
    line_id: str
    label: str | None = None  # key term: the original term (the label is rendered from the termbase)


@dataclass(frozen=True)
class BookLine:
    """An accepted (or not) translation of one book unit of the lesson."""
    unit_id: str
    block_id: str
    source_text: str
    target_text: str
    decision: str  # accepted | needs_review | rejected | dropped
    kind: str = "prose"


def script_unit_id(book_id: str, episode: int, seq: int) -> str:
    return f"{book_id}:ep{episode:03d}:u{seq:05d}"


def is_script_unit(unit: TranslationUnit) -> bool:
    return bool(unit.section_path) and unit.section_path[0] == SCRIPT_MARKER


def script_units(script: EpisodeScript, *, page_of: Callable[[str], list[int]] | None = None,
                 key_term_labels: Mapping[str, str] | None = None) -> list[ScriptUnit]:
    """Translation units for the generated lines of an English script (book text excluded)."""
    if script.language != "en":
        raise ValueError("script units are built from the English script")
    labels = {k.casefold(): v for k, v in (key_term_labels or {}).items()}
    out: list[ScriptUnit] = []
    for section in script.sections:
        if section.kind in BOOK_TEXT_KINDS:
            continue
        for line in section.lines:
            source, label = line.text, None
            if line.role == "term" and line.original and line.original.casefold() in labels \
                    and line.text.startswith(f"{line.original}:"):
                source, label = line.text[len(line.original) + 1:].strip(), line.original
            pages = sorted({p for s in line.sources if s.kind == "book" and s.block_id and page_of
                            for p in page_of(s.block_id)}) or [1]
            unit = TranslationUnit(
                unit_id=script_unit_id(script.book_id, script.episode, len(out) + 1), book_id=script.book_id,
                block_ids=[f"{script.book_id}:episode{script.episode:03d}:{line.line_id}"], kind="prose",
                source_text=source, pages=pages,
                section_path=[SCRIPT_MARKER, f"{script.episode:02d}", section.kind, line.role])
            out.append(ScriptUnit(unit, line.line_id, label))
    return [ScriptUnit(su.unit.model_copy(update={
        "prev_unit_id": out[i - 1].unit.unit_id if i else None,
        "next_unit_id": out[i + 1].unit.unit_id if i + 1 < len(out) else None}), su.line_id, su.label)
        for i, su in enumerate(out)]


def assemble_arabic(script: EpisodeScript, units: list[ScriptUnit], translations: Mapping[str, UnitTranslation], *,
                    book_lines: Mapping[str, BookLine], key_term_labels: Mapping[str, str], source_sha256: str,
                    clock: Callable[[], datetime], metrics: dict | None = None) -> EpisodeScript:
    """The Arabic script: generated lines from their unit decisions, book text from the book's
    accepted units. A line without any target text is left out (and counted)."""
    labels = {k.casefold(): v for k, v in key_term_labels.items()}
    by_line = {su.line_id: su for su in units}
    missing: list[str] = []
    sections: list[EpisodeSection] = []
    for section in script.sections:
        if section.kind in BOOK_TEXT_KINDS:
            lines = []
            for uid in section.unit_ids:
                bl = book_lines.get(uid)
                if bl is None or bl.decision == "dropped" or not bl.target_text.strip():
                    if bl is None or bl.decision != "dropped":
                        missing.append(uid)
                    continue
                lines.append(ScriptLine(
                    line_id=f"{section.section_id}.{uid.rsplit(':', 1)[-1]}", text=bl.target_text,
                    sources=[ScriptSource(kind="book", block_id=bl.block_id, quote=bl.source_text[:1200])],
                    unit_ids=[uid], status=_status(bl.decision)))
            sections.append(section.model_copy(update={"lines": lines}))
            continue
        lines = []
        for line in section.lines:
            su = by_line[line.line_id]
            t = translations.get(su.unit.unit_id)
            if t is None or not t.target_text.strip():
                missing.append(su.unit.unit_id)
                continue
            text = t.target_text.strip()
            if su.label is not None:
                text = f"{labels[su.label.casefold()]} ({su.label}): {text}"
            lines.append(line.model_copy(update={"text": text, "unit_ids": [su.unit.unit_id],
                                                 "status": _status(t.decision)}))
        if not lines and section.kind not in ("others_say", "debate", "acknowledgments"):
            missing.append(section.section_id)
        if lines:
            sections.append(section.model_copy(update={"lines": lines}))
    # model_copy skips validation: build the script through the constructor (contract re-checked)
    data = script.model_dump()
    data.update(language="ar", sections=[s.model_dump() for s in sections], source_script_sha256=source_sha256,
                created_at=clock(), metrics={**(metrics or {}), "missing_lines": missing})
    return EpisodeScript.model_validate(data)


def with_texts(script: EpisodeScript, texts: Mapping[str, tuple[str, list[str]]]) -> EpisodeScript:
    """Replace line texts (and record the originals inserted at a first mention)."""
    data = script.model_dump()
    for section in data["sections"]:
        for line in section["lines"]:
            if line["line_id"] in texts:
                text, inserted = texts[line["line_id"]]
                line["text"] = text
                line["inserted_originals"] = sorted(set(line.get("inserted_originals") or []) | set(inserted))
    return EpisodeScript.model_validate(data)


def _status(decision: str) -> str:
    return decision if decision in ("accepted", "needs_review", "rejected") else "rejected"


__all__ = ["BookLine", "SCRIPT_MARKER", "ScriptUnit", "assemble_arabic", "is_script_unit", "script_unit_id",
           "script_units", "with_texts"]
