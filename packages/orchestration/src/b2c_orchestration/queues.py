"""Temporal task queues (docs/04 §2, D-007).

`heavy` runs one activity at a time so heavy services (LLM, Docling, embeddings, TTS) never
compete with each other; the budget math in docs/04 §2 depends on this.
"""
LIGHT = "light"
HEAVY = "heavy"
IO = "io"
AUDIO = "audio"

_LIMITS = {LIGHT: 8, HEAVY: 1, IO: 8, AUDIO: 1}


def max_concurrent_activities(queue: str) -> int:
    return _LIMITS[queue]
