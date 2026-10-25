"""Shared types for translation QA (D-028).

A check is a pure function from a ``CheckContext`` to MQM-aligned ``QAIssue``s. Checks never
call models; semantic judgement belongs to a ``QualityEstimator``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from b2c_contracts import QAIssue, QEResult, TranslationUnit


@dataclass(frozen=True)
class CheckContext:
    unit: TranslationUnit
    target: str  # final text after footnote restoration
    source_language: str = "en"
    target_language: str = "ar"
    terms: Mapping[str, str] = field(default_factory=dict)     # locked termbase (source -> target)
    entities: Mapping[str, str] = field(default_factory=dict)  # named-entity table (source -> target)
    leak_fingerprints: frozenset[str] = frozenset()            # derived from every prompt template version
    # approved termbase: renderings a reviewer ruled out for a locked term (source -> variants)
    forbidden: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # approved hint-tier entries (D-039): preferred renderings, checked only as a minor signal
    hints: Mapping[str, str] = field(default_factory=dict)
    # approved termbase (D-047): other acceptable spellings of an approved name (source -> variants)
    entity_variants: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # D-032 principle 3: originals inserted after translation at a term's first mention, as
    # "العربي (original)". Explicit whitelist: exactly these bracketed glosses are removed before the
    # checks run (b2c_translation.first_mention), any other Latin is still checked.
    glossed_originals: frozenset[str] = frozenset()


Check = Callable[[CheckContext], list[QAIssue]]


@dataclass(frozen=True)
class QEOutcome:
    passed: bool
    result: QEResult
    issues: list[QAIssue] = field(default_factory=list)


class QualityEstimator(Protocol):
    @property
    def name(self) -> str: ...

    def estimate(self, unit: TranslationUnit, target: str) -> QEOutcome: ...
