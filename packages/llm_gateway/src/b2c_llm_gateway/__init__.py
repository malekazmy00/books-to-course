"""b2c_llm_gateway — the only way the project talks to an LLM (CLAUDE.md rule 5).

Local OpenAI-compatible servers only (llama-server); cloud endpoints are refused (D-004).
"""
from .cache import InMemoryLLMCache, LLMCache, PostgresLLMCache, cache_key
from .client import ChatResult, LLMGateway
from .errors import CloudProviderNotAllowed, InvalidLLMOutput, LLMUnavailable

__all__ = [
    "ChatResult",
    "CloudProviderNotAllowed",
    "InMemoryLLMCache",
    "InvalidLLMOutput",
    "LLMCache",
    "LLMGateway",
    "LLMUnavailable",
    "PostgresLLMCache",
    "cache_key",
]
