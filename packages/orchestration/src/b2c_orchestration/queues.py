"""Temporal task queues (docs/04 §2, D-007).

`heavy` runs one activity at a time so heavy services (LLM, Docling, embeddings, TTS) never
compete with each other; the budget math in docs/04 §2 depends on this.
"""
LIGHT = "light"
HEAVY = "heavy"
IO = "io"
AUDIO = "audio"
# Rebuilt book translation (D-028 part D): workflow + its three activities, one unit batch at a time.
TRANSLATE_BOOK = "translate-book"

_LIMITS = {LIGHT: 8, HEAVY: 1, IO: 8, AUDIO: 1, TRANSLATE_BOOK: 1}


def max_concurrent_activities(queue: str) -> int:
    return _LIMITS[queue]
