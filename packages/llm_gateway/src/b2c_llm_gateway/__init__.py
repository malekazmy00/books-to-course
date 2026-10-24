"""b2c_llm_gateway — the only way the project talks to an LLM (CLAUDE.md rule 5).

Local endpoints are the default; remote providers require explicit opt-in and secret files.
"""
from .budget import (
    FreeTokenQuotaExceeded,
    InMemoryOpenRouterBudget,
    OpenRouterQuotaExceeded,
    PostgresFreeTokenBudget,
    PostgresOpenRouterBudget,
)
from .cache import InMemoryLLMCache, LLMCache, PostgresLLMCache, cache_key
from .client import ChatResult, LLMGateway
from .errors import (CloudProviderNotAllowed, InvalidLLMOutput, LLMOutputTruncated, LLMRequestRejected, LLMUnavailable, OpenRouterRateLimited)

__all__ = [
    "ChatResult",
    "InMemoryOpenRouterBudget",
    "FreeTokenQuotaExceeded",
    "PostgresFreeTokenBudget",
    "CloudProviderNotAllowed",
    "InMemoryLLMCache",
    "InvalidLLMOutput",
    "LLMOutputTruncated",
    "LLMRequestRejected",
    "LLMCache",
    "LLMGateway",
    "LLMUnavailable",
    "OpenRouterQuotaExceeded",
    "OpenRouterRateLimited",
    "PostgresOpenRouterBudget",
    "PostgresLLMCache",
    "cache_key",
]
