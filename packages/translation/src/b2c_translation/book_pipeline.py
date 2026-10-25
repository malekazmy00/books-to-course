"""Per-unit translation flow for the rebuild (D-028 §3), independent of Temporal.

For each ``TranslationUnit``:

1. route by kind: ``noise`` is dropped, ``reference``/``index`` are rendered by rule, the rest
   goes to the engine (``toc`` entries are short headings and are translated);
2. translation memory: a human entry is returned as-is; an engine entry is re-checked;
3. engine request with the unit's source text only, plus the locked terms that occur in it
   and, when the engine supports it, the neighbouring source as context;
4. footnote markers are restored by code (units end at their markers);
5. deterministic checks, then semantic QE only when nothing already blocks;
6. ``qa.policy.decide`` chooses accept / repair / retranslate / review / reject.

A retry only happens when the request changes. Requests run at temperature 0 and are
cached by the gateway, so resending the same request cannot fix anything. The levers, in
order: split a multi-sentence unit into sentences (smaller units fail far less often —
error catalogue §1.5), then a typed repair request when the engine follows instructions.
When no lever is left, the unit stops at its current decision.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from b2c_contracts import QAIssue, TranslationUnit, UnitTranslation

from .engines import EngineRequest, RepairRequest, TranslationEngine
from .qa import CheckContext, QEOutcome, QualityEstimator
from .qa.checks import run_checks
from .qa.leakage import fingerprints_from_templates
from .prompts import ALL_TEMPLATES
from .qa.policy import Decision, decide
from .references import render_reference_unit
from .units import split_sentences

RULE_KINDS = frozenset({"reference", "index"})
_LEAK_FINGERPRINTS = fingerprints_from_templates(t.text for t in ALL_TEMPLATES)


class Memory(Protocol):
    def lookup(self, unit: TranslationUnit, source_language: str,
               target_language: str) -> UnitTranslation | None: ...

    def store(self, unit: TranslationUnit, translation: UnitTranslation) -> None: ...


@dataclass(frozen=True)
class PipelineConfig:
    source_language: str = "en"
    target_language: str = "ar"
    max_attempts: int = 3
    context_chars: int = 400


@dataclass(frozen=True)
class Draft:
    text: str
    issues: tuple[QAIssue, ...]
    qe: QEOutcome | None
    decision: Decision
    lever: str  # "initial", "split_sentences" or "repair"


@dataclass(frozen=True)
class UnitResult:
    translation: UnitTranslation
    drafts: tuple[Draft, ...] = ()


# ------------------------------------------------------------------ helpers
def terms_in(source: str, terms: Mapping[str, str]) -> dict[str, str]:
    """Locked terms whose source form occurs in ``source`` (case-insensitive, whole words)."""
    return {s: t for s, t in terms.items()
            if re.search(rf"(?<![\w]){re.escape(s)}(?![\w])", source, re.IGNORECASE)}


def restore_footnotes(text: str, markers: Sequence[str]) -> str:
    """Units end at their footnote markers, so restoring them is appending them in order.

    A space separates the marker from the text so it cannot merge into a preceding number."""
    return text.rstrip() + "".join(f" {m}" for m in markers) if markers else text.strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UnitTranslator:
    def __init__(self, engine: TranslationEngine, *, estimator: QualityEstimator | None = None,
                 memory: Memory | None = None, terms: Mapping[str, str] | None = None,
                 entities: Mapping[str, str] | None = None,
                 forbidden: Mapping[str, Sequence[str]] | None = None,
                 hints: Mapping[str, str] | None = None,
                 reference_renderer: Callable[[TranslationUnit], str] | None = None,
                 config: PipelineConfig = PipelineConfig(), clock: Callable[[], datetime] = _now) -> None:
        self.engine = engine
        self.estimator = estimator
        self.memory = memory
        self.terms = dict(terms or {})
        self.entities = dict(entities or {})
        self.forbidden = {k: tuple(v) for k, v in (forbidden or {}).items()}
        self.hints = dict(hints or {})  # hint-tier termbase entries (D-039): preferred, minor check
        self.reference_renderer = reference_renderer or render_reference_unit
        self.config = config
        self.clock = clock

    # ------------------------------------------------------------------ public
    def translate(self, unit: TranslationUnit, *, context_before: str | None = None,
                  context_after: str | None = None) -> UnitResult:
        if unit.kind == "noise":
            return UnitResult(UnitTranslation(unit_id=unit.unit_id, target_text="", origin="rule",
                                              decision="dropped", decided_by="policy",
                                              decided_at=self.clock()))
        if unit.kind in RULE_KINDS:
            return self._rule(unit)
        cached = self._memory_hit(unit)
        if cached is not None and cached.decided_by == "human":
            return UnitResult(cached)
        result = self._engine(unit, context_before, context_after)
        if result.translation.decision == "accepted" and self.memory is not None:
            self.memory.store(unit, result.translation)
        return result

    # ------------------------------------------------------------------ routes
    def _rule(self, unit: TranslationUnit) -> UnitResult:
        # Back-of-book index entries are kept verbatim: an Arabic edition rebuilds its index
        # from the translated text, so translating entries one by one has no use.
        rendered = self.reference_renderer(unit) if unit.kind == "reference" else unit.source_text
        target = restore_footnotes(rendered, unit.footnote_markers)
        issues = run_checks(self._context(unit, target))
        decision = decide(issues, None, attempts=0, max_attempts=1, engine_supports_repair=False, origin="rule")
        return UnitResult(decision.to_unit_translation(unit_id=unit.unit_id, target_text=target, engine=None,
                                                       attempts=0, decided_at=self.clock()))

    def _memory_hit(self, unit: TranslationUnit) -> UnitTranslation | None:
        if self.memory is None:
            return None
        return self.memory.lookup(unit, self.config.source_language, self.config.target_language)

    def _engine(self, unit: TranslationUnit, context_before: str | None,
                context_after: str | None) -> UnitResult:
        caps = self.engine.capabilities
        request = EngineRequest(
            unit=unit, source_language=self.config.source_language,
            target_language=self.config.target_language,
            terms=terms_in(unit.source_text, self.terms) if caps.supports_terms else {},
            hints=terms_in(unit.source_text, self.hints) if caps.supports_terms else {},
            context_before=context_before if caps.supports_context else None,
            context_after=context_after if caps.supports_context else None)
        drafts: list[Draft] = []
        text = self.engine.translate(request).text
        lever = "initial"
        split_done = False
        attempts = 1
        while True:
            target = restore_footnotes(text, unit.footnote_markers)
            draft = self._assess(unit, target, attempts, lever)
            drafts.append(draft)
            action = draft.decision.next_action
            if action not in ("repair", "retranslate") or attempts >= self.config.max_attempts:
                break
            sentences = split_sentences(unit.source_text)
            if action == "retranslate" and not split_done and len(sentences) > 1:
                text = " ".join(self.engine.translate(self._sentence_request(request, unit, i, s)).text.strip()
                                for i, s in enumerate(sentences))
                split_done, lever = True, "split_sentences"
            elif caps.supports_repair:
                repair = RepairRequest(previous_target=text, issues=draft.issues)
                text = self.engine.translate(EngineRequest(**{**request.__dict__, "repair": repair})).text
                lever = "repair"
            else:
                # No lever left: an identical request would return the same output, so the
                # unit is out of attempts and the policy makes its final call now.
                drafts[-1] = self._assess(unit, target, self.config.max_attempts, lever)
                break
            attempts += 1
        final = drafts[-1]
        translation = final.decision.to_unit_translation(
            unit_id=unit.unit_id, target_text=final.text, engine=self.engine.identity, attempts=attempts,
            decided_at=self.clock())
        return UnitResult(translation, tuple(drafts))

    def _sentence_request(self, request: EngineRequest, unit: TranslationUnit, index: int,
                          sentence: str) -> EngineRequest:
        sub = unit.model_copy(update={"unit_id": f"{unit.unit_id}#s{index + 1}", "source_text": sentence,
                                      "footnote_markers": []})
        terms = terms_in(sentence, self.terms) if self.engine.capabilities.supports_terms else {}
        hints = terms_in(sentence, self.hints) if self.engine.capabilities.supports_terms else {}
        return EngineRequest(unit=sub, source_language=request.source_language,
                             target_language=request.target_language, terms=terms, hints=hints,
                             context_before=request.context_before, context_after=request.context_after)

    def _assess(self, unit: TranslationUnit, target: str, attempts: int, lever: str) -> Draft:
        issues = run_checks(self._context(unit, target))
        blocking = any(i.severity in ("major", "critical") for i in issues)
        qe = None if blocking or self.estimator is None else self.estimator.estimate(unit, target)
        decision = decide(issues, qe, attempts=attempts, max_attempts=self.config.max_attempts,
                          engine_supports_repair=self.engine.capabilities.supports_repair)
        return Draft(text=target, issues=tuple(decision.issues), qe=qe, decision=decision, lever=lever)

    def _context(self, unit: TranslationUnit, target: str) -> CheckContext:
        return CheckContext(unit=unit, target=target, source_language=self.config.source_language,
                            target_language=self.config.target_language,
                            terms=terms_in(unit.source_text, self.terms),
                            entities=terms_in(unit.source_text, self.entities),
                            forbidden={k: v for k, v in self.forbidden.items()
                                       if k in terms_in(unit.source_text, {k: ""})},
                            hints=terms_in(unit.source_text, self.hints),
                            leak_fingerprints=_LEAK_FINGERPRINTS)


# ------------------------------------------------------------------ book level
def translate_units(units: Sequence[TranslationUnit], translator: UnitTranslator,
                    *, on_result: Callable[[TranslationUnit, UnitResult], None] | None = None
                    ) -> dict[str, UnitResult]:
    """Translate units in book order, giving each its neighbours' source as optional context."""
    limit = translator.config.context_chars
    results: dict[str, UnitResult] = {}
    for index, unit in enumerate(units):
        before = units[index - 1].source_text[-limit:] if index > 0 else None
        after = units[index + 1].source_text[:limit] if index + 1 < len(units) else None
        result = translator.translate(unit, context_before=before, context_after=after)
        results[unit.unit_id] = result
        if on_result is not None:
            on_result(unit, result)
    return results


@dataclass
class BlockText:
    """Arabic text of one source block, built only from accepted units."""
    text: str | None
    complete: bool
    unit_ids: list[str] = field(default_factory=list)
    pending_unit_ids: list[str] = field(default_factory=list)
    merged_into: str | None = None  # set when a page-break join moved this block's text


def assemble_blocks(units: Iterable[TranslationUnit],
                    translations: Mapping[str, UnitTranslation]) -> dict[str, BlockText]:
    """Group unit results per block. A unit spanning several blocks (a paragraph joined
    across a page break) belongs to its first block; the others point to it."""
    blocks: dict[str, BlockText] = {}
    pieces: dict[str, list[str]] = {}
    for unit in units:
        owner = unit.block_ids[0]
        block = blocks.setdefault(owner, BlockText(text=None, complete=True))
        for other in unit.block_ids[1:]:
            blocks.setdefault(other, BlockText(text=None, complete=True, merged_into=owner))
        block.unit_ids.append(unit.unit_id)
        translation = translations.get(unit.unit_id)
        if translation is None or translation.decision not in ("accepted", "dropped"):
            block.complete = False
            block.pending_unit_ids.append(unit.unit_id)
        elif translation.decision == "accepted":
            pieces.setdefault(owner, []).append(translation.target_text)
    for block_id, block in blocks.items():
        if block.merged_into is None and block.complete:
            block.text = " ".join(pieces.get(block_id, [])) or ""
    return blocks


__all__ = ["BlockText", "Draft", "Memory", "PipelineConfig", "RULE_KINDS", "UnitResult", "UnitTranslator",
           "assemble_blocks", "restore_footnotes", "split_sentences", "terms_in", "translate_units"]
