"""Sentence segmentation into bounded TTS chunks (R7; audit 07 C4, M1).

Every chunk is at most ``max_chars`` long and the function always terminates: each step
consumes at least one character (sentence -> clause -> word -> hard cut).
"""
from __future__ import annotations

import re

_SENTENCE_END = re.compile(r"(?<=[.!?؟…])\s+")
_CLAUSE_END = re.compile(r"(?<=[،,;؛:])\s+")


def _bounded(piece: str, max_chars: int) -> list[str]:
    if len(piece) <= max_chars:
        return [piece]
    clauses = [c for c in _CLAUSE_END.split(piece) if c]
    if len(clauses) > 1:
        return [part for clause in clauses for part in _bounded(clause, max_chars)]
    words = piece.split()
    if len(words) > 1:
        out, current = [], ""
        for word in words:
            for part in _bounded(word, max_chars):
                if not current:
                    current = part
                elif len(current) + 1 + len(part) <= max_chars:
                    current += " " + part
                else:
                    out.append(current)
                    current = part
        if current:
            out.append(current)
        return out
    return [piece[i:i + max_chars] for i in range(0, len(piece), max_chars)]  # one unbroken token


def split_for_tts(text: str, *, target_chars: int = 200, max_chars: int = 300) -> list[str]:
    """Sentences, merged greedily up to ``target_chars`` and never above ``max_chars``."""
    if not 0 < target_chars <= max_chars:
        raise ValueError("need 0 < target_chars <= max_chars")
    pieces = [p for sentence in _SENTENCE_END.split(text.strip()) if sentence.strip()
              for p in _bounded(sentence.strip(), max_chars)]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) <= target_chars:
            current += " " + piece
            continue
        if current:
            chunks.append(current)
        current = piece
    if current:
        chunks.append(current)
    return chunks


__all__ = ["split_for_tts"]
