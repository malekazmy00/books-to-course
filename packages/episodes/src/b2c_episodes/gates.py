"""Promotion gates of an ENGLISH episode script (stage ``episode-script-en``). Provisional policy
(2026-10-25), recorded in every GateResult through the definition.

* ``episodes.grounding`` (deterministic) — every book source of every line re-checked against the
  parsed book: the quote occurs in its block; recap lines cite only PREVIOUS lessons, every other
  lesson-episode line only the episode's own lesson (episode 00: the front matter); dossier sources
  are published claims; concept sources exist. Rate must be 1.0.
* ``episodes.validation`` (semantic) — an independent validator of another family was used, and its
  pass rate over the grounded candidates is at least ``min_validator_pass_rate`` (a low rate means
  the generator is unreliable even though only validated lines are used).
* ``episodes.coverage`` (deterministic, lesson episodes) — the summary and key terms name at least
  ``min_coverage`` of the lesson's first-mentioned concepts.
* ``episodes.length`` (deterministic) — every section and line within the listening budgets.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from b2c_contracts import BOOK_TEXT_KINDS, EpisodeScript, KnowledgeDossier

from . import budgets as B
from .sources import quote_in_text

GROUNDING_GATE_ID = "episodes.grounding"
VALIDATION_GATE_ID = "episodes.validation"
COVERAGE_GATE_ID = "episodes.coverage"
LENGTH_GATE_ID = "episodes.length"
GROUNDING_DEFINITION: dict[str, Any] = {"min_grounding_rate": 1.0,
                                        "rules": ["quote in cited block", "recap cites previous lessons only",
                                                  "other lines cite the episode's lesson (00: front matter)",
                                                  "key terms: the author's definition anywhere in the lessons",
                                                  "dossier claims published", "concepts exist"]}
VALIDATION_DEFINITION: dict[str, Any] = {"min_validator_pass_rate": 0.6, "validator_family": "!= generator family"}
COVERAGE_DEFINITION: dict[str, Any] = {"min_coverage": 0.7,
                                       "target": "lesson first-mention concepts named in summary or key terms"}
EXTERNAL_LINE_EXTRA_WORDS = 12  # "According to <source>: " prefix of attributed lines
# figure readings (D-044) passed their own gate (figure-descriptions: grounding, length) before insertion
OWN_GATE_KINDS = frozenset({"figure_reading"})


@dataclass(frozen=True)
class GateOutcome:
    gate_id: str
    kind: str
    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    definition: dict[str, Any] = field(default_factory=dict)


def check_grounding(script: EpisodeScript, *, block_text: Mapping[str, str], lesson_of: Mapping[str, str],
                    episode_of_lesson: Mapping[str, int], allowed_lessons: set[str] | None = None,
                    dossier: KnowledgeDossier | None = None, concept_ids: set[str] | None = None) -> dict[str, Any]:
    published = {c.claim_id for c in dossier.published()} if dossier is not None else set()
    total, invalid = 0, []
    for section in script.sections:
        if section.kind in BOOK_TEXT_KINDS or section.kind in OWN_GATE_KINDS:
            continue
        for line in section.lines:
            code = line.line_id.split(".")[-1][:1]
            recap = code == "r"
            for s in line.sources:
                total += 1
                bad = None
                if s.kind == "book":
                    lesson = lesson_of.get(s.block_id or "")
                    if not quote_in_text(s.quote or "", block_text.get(s.block_id or "", "")):
                        bad = "quote not in block"
                    elif script.kind == "lesson" and recap and episode_of_lesson.get(lesson or "", 10**9) >= script.episode:
                        bad = "recap cites a lesson that is not a previous episode"
                    elif script.kind == "lesson" and not recap and code != "k" and lesson != script.lesson_id:
                        bad = "cites outside the episode's lesson"
                    elif script.kind == "course_intro" and allowed_lessons is not None and lesson not in allowed_lessons:
                        bad = "cites outside the front matter"
                elif s.kind == "dossier" and s.claim_id not in published:
                    bad = "dossier claim not published"
                elif s.kind == "concept" and concept_ids is not None and s.concept_id not in concept_ids:
                    bad = "unknown concept"
                if bad:
                    invalid.append(f"{line.line_id}: {bad}")
    rate = (total - len(invalid)) / total if total else 0.0
    return {"sources": total, "invalid": invalid[:20], "invalid_count": len(invalid), "grounding_rate": round(rate, 4)}


def check_length(script: EpisodeScript) -> dict[str, Any]:
    over: list[str] = []
    per_section: dict[str, int] = {}
    for section in script.sections:
        if section.kind in BOOK_TEXT_KINDS or section.kind in OWN_GATE_KINDS:
            continue
        n = sum(B.words(x.text) for x in section.lines)
        per_section[section.section_id] = n
        if n > B.SECTION_WORDS[section.kind]:
            over.append(f"{section.section_id}: {n} > {B.SECTION_WORDS[section.kind]} words")
        for line in section.lines:
            limit = B.LINE_WORDS[line.role] + (EXTERNAL_LINE_EXTRA_WORDS if line.attributed_to else 0)
            if line.role == "term":
                limit += len((line.original or "").split()) + 1
            if B.words(line.text) > limit:
                over.append(f"{line.line_id}: {B.words(line.text)} > {limit} words")
    spoken = sum(per_section.values())
    return {"over_budget": over, "section_words": per_section, "generated_words": spoken,
            "est_generated_minutes": round(spoken / 130, 1)}


def evaluate_en_script(script: EpisodeScript, *, grounding: dict[str, Any], coverage: dict[str, Any] | None) -> list[GateOutcome]:
    g = GateOutcome(GROUNDING_GATE_ID, "deterministic", passed=grounding["sources"] > 0 and grounding["grounding_rate"]
                    >= GROUNDING_DEFINITION["min_grounding_rate"], metrics=grounding, definition=dict(GROUNDING_DEFINITION))
    m = script.metrics
    rate = float(m.get("validator_pass_rate") or 0.0)
    v = GateOutcome(VALIDATION_GATE_ID, "semantic", passed=script.validator is not None
                    and script.validator_family != script.generator_family
                    and rate >= VALIDATION_DEFINITION["min_validator_pass_rate"],
                    metrics={"validator_pass_rate": rate, "grounding_rate": m.get("grounding_rate"),
                             "proposed": m.get("proposed"), "grounded": m.get("grounded"), "validated": m.get("validated"),
                             "validator": script.validator.model_id if script.validator else None,
                             "generator": script.generator.model_id if script.generator else None},
                    definition=dict(VALIDATION_DEFINITION))
    length = check_length(script)
    ln = GateOutcome(LENGTH_GATE_ID, "deterministic", passed=not length["over_budget"], metrics=length,
                     definition=B.definition())
    out = [g, v, ln]
    if coverage is not None:
        out.append(GateOutcome(COVERAGE_GATE_ID, "deterministic", passed=coverage["coverage"]
                               >= COVERAGE_DEFINITION["min_coverage"], metrics=coverage,
                               definition=dict(COVERAGE_DEFINITION)))
    return out


__all__ = ["COVERAGE_DEFINITION", "COVERAGE_GATE_ID", "GROUNDING_DEFINITION", "GROUNDING_GATE_ID", "GateOutcome",
           "LENGTH_GATE_ID", "VALIDATION_DEFINITION", "VALIDATION_GATE_ID", "check_grounding", "check_length",
           "evaluate_en_script"]
