"""L7 Translation deterministic pipeline primitives."""
from .generation import (SourceSegment, TranslatedSegment, TranslationPack, plan_translation_batches,
                         translate_segments)
from .pipeline import (
    TextChunk,
    build_termbase,
    chunk_text,
    deterministic_translation_check,
    render_term_instructions,
    term_injection_block,
    termbase_gate,
    translation_needs_review,
    word_count,
)

__all__ = [
    "TextChunk", "build_termbase", "chunk_text", "deterministic_translation_check",
    "render_term_instructions", "term_injection_block", "termbase_gate",
    "translation_needs_review", "word_count", "plan_translation_batches",
    "SourceSegment", "TranslatedSegment", "TranslationPack", "translate_segments",
]
