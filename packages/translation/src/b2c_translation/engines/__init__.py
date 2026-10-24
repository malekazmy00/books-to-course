"""Translation engine adapters (D-028). Engines see source text only; see base.py."""
from .base import (EngineCapabilities, EngineRequest, EngineResult, RepairRequest, TextCompletion,
                   TextCompletionGateway, TranslationEngine)

__all__ = ["EngineCapabilities", "EngineRequest", "EngineResult", "RepairRequest", "TextCompletion",
           "TextCompletionGateway", "TranslationEngine"]
