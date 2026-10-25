"""b2c_concepts — the concept layer (EN, D-032/D-035): for each key concept of the book, the AUTHOR's
definition with exact block/page locations, where it is introduced, a concept graph (prerequisites
and other relations), per-lesson first mentions, and external notes from the knowledge dossier kept
in the external voice. Every author-voice statement is a verified span of the book.

Model calls go through an injected ``complete_text`` client (b2c_llm_gateway in production).
Termbase entries arrive as ``ConceptSeed``s built by orchestration (no import of another layer).
"""
from .book import BookIndex
from .gates import COVERAGE_GATE_ID, GROUNDING_GATE_ID, evaluate_concept_layer
from .grounding import validate_layer
from .layer import ConceptModel, ConceptSeed, build_concept_layer

CONCEPT_LAYER_VERSION = "concept-layer-2026-10-25.1"

__all__ = ["BookIndex", "CONCEPT_LAYER_VERSION", "COVERAGE_GATE_ID", "ConceptModel", "ConceptSeed", "GROUNDING_GATE_ID",
           "build_concept_layer", "evaluate_concept_layer", "validate_layer"]
