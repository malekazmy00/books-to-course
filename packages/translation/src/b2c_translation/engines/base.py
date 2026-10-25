"""Engine interface for the translation rebuild (D-028).

Channel separation is the core rule: the text an engine translates is the unit's source
text and nothing else. Instructions, terms, context and repair feedback travel in
separate fields; each adapter decides how (if at all) its model can receive them, e.g. a
chat model gets them in the system message, a plain MT model only gets its documented
template. Adapters reach models exclusively through ``TextCompletionGateway``
(llm_gateway, CLAUDE.md rule 5).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from b2c_contracts import EngineIdentity, QAIssue, TranslationUnit


@dataclass(frozen=True)
class EngineCapabilities:
    follows_instructions: bool  # chat/instruction model; False for plain MT models
    supports_terms: bool        # can be told locked term equivalents
    supports_context: bool      # can be given neighbouring source text without translating it
    supports_repair: bool       # can revise a previous draft given typed issues
    max_source_chars: int       # longest unit it translates reliably


@dataclass(frozen=True)
class RepairRequest:
    previous_target: str
    issues: Sequence[QAIssue]


@dataclass(frozen=True)
class EngineRequest:
    unit: TranslationUnit
    source_language: str
    target_language: str
    terms: Mapping[str, str] = field(default_factory=dict)  # only locked terms occurring in the unit
    context_before: str | None = None
    context_after: str | None = None
    repair: RepairRequest | None = None
    # hint-tier termbase entries occurring in the unit (D-039): preferred, not mandatory
    hints: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineResult:
    text: str
    identity: EngineIdentity
    finish_reason: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict)


class TranslationEngine(Protocol):
    @property
    def identity(self) -> EngineIdentity: ...

    @property
    def capabilities(self) -> EngineCapabilities: ...

    def translate(self, request: EngineRequest) -> EngineResult: ...


@dataclass(frozen=True)
class TextCompletion:
    text: str
    model: str
    finish_reason: str | None = None
    usage: Mapping[str, int] = field(default_factory=dict)


class TextCompletionGateway(Protocol):
    """Plain-text chat completion through llm_gateway (cached, budgeted, traced)."""

    def complete_text(self, messages: Sequence[Mapping[str, str]], *, prompt_version: str,
                      max_tokens: int, temperature: float = 0.0) -> TextCompletion: ...
