"""Offline reading-quality gate ``speech.reading-quality`` (speech readability proposal 2026-10-25).

Deterministic checks on the speech-text document, before any TTS call, of what makes a reading
weak even when the text is correct:
- every phrase fits the engine's real input (characters, words), so the engine never re-splits it
  on its own at an arbitrary point (Fasih hard-wraps at 160 diacritized characters);
- no character a voice cannot speak;
- a sane pause plan: every segment has a boundary, the break strength and pause agree with the
  boundary scale, no pause above the track's silence budget, a section pause before each heading;
- every sentence-or-larger boundary ends with sentence punctuation (final intonation);
- few one-word fragments.
What it cannot judge (prosody naturalness, wrong vowelization) needs audio: ASR back-transcription
(``speech.asr-intelligibility``) and listening.
"""
from __future__ import annotations

from collections import Counter
from statistics import median

from b2c_contracts import SpeechTextDocument

from .normalize import _UNSPEAKABLE
from .prosody import BREAK_STRENGTH, MAX_PAUSE_MS, PARAGRAPH_LEVEL, PAUSE_MS, EngineProfile, engine_profile

READING_QUALITY_GATE = {"max_phrases_over_limit": 0, "max_unspeakable_chars": 0, "max_engine_rechunk": 0,
                        "max_pause_plan_errors": 0, "max_unterminated": 0, "max_fragment_ratio": 0.05}
_TERMINAL = ".!؟:"


def _p(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0


def evaluate_reading_quality(doc: SpeechTextDocument, profile: str | EngineProfile) -> tuple[bool, dict[str, object]]:
    profile = engine_profile(profile)
    segments = doc.segments
    over = rechunk = unterminated = fragments = 0
    unspeakable: Counter[str] = Counter()
    errors: list[str] = []
    boundaries: Counter[str] = Counter()
    for i, s in enumerate(segments):
        text = s.spoken_text.strip()
        words = len(text.split())
        if len(text) > profile.max_phrase_chars or words > profile.max_phrase_words:
            over += 1
        if profile.engine_chunk_chars and len(text) * profile.diacritized_expansion > profile.engine_chunk_chars:
            rechunk += 1
        unspeakable.update(_UNSPEAKABLE.findall(text))
        if words < 2 and s.structure != "heading":
            fragments += 1
        b = s.boundary_after
        boundaries[b or "unset"] += 1
        if b is None:
            errors.append(f"{s.segment_id}: no boundary")
            continue
        if b != "phrase" and text[-1:] not in _TERMINAL:
            unterminated += 1
        if s.break_after != BREAK_STRENGTH[b]:
            errors.append(f"{s.segment_id}: break {s.break_after} does not match boundary {b}")
        if not 0 < s.pause_after_ms <= MAX_PAUSE_MS:
            errors.append(f"{s.segment_id}: pause {s.pause_after_ms} ms outside (0, {MAX_PAUSE_MS}]")
        elif s.pause_after_ms != PAUSE_MS[b]:
            errors.append(f"{s.segment_id}: pause {s.pause_after_ms} ms does not match boundary {b}")
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if nxt is not None and nxt.structure == "heading" and s.structure != "heading" and b != "section":
            errors.append(f"{s.segment_id}: no section pause before a heading")
        if nxt is not None and nxt.unit_ids != s.unit_ids and b not in PARAGRAPH_LEVEL:
            errors.append(f"{s.segment_id}: unit ends on a {b} boundary")
        if nxt is None and b == "phrase":
            errors.append(f"{s.segment_id}: document ends inside a sentence")
    sentence_words: list[int] = []
    current = 0
    for s in segments:
        current += len(s.spoken_text.split())
        if s.boundary_after != "phrase":
            sentence_words.append(current)
            current = 0
    chars = [len(s.spoken_text) for s in segments]
    fragment_ratio = round(fragments / len(segments), 4) if segments else 0.0
    rule = READING_QUALITY_GATE
    metrics: dict[str, object] = {
        "profile": profile.definition(), "segments": len(segments), "boundaries": dict(sorted(boundaries.items())),
        "phrases_over_limit": over, "engine_rechunk_estimate": rechunk,
        "unspeakable_chars": dict(unspeakable.most_common(20)), "pause_plan_errors": errors[:20],
        "pause_plan_error_count": len(errors), "unterminated": unterminated, "fragments": fragments,
        "fragment_ratio": fragment_ratio,
        "phrase_chars": {"p50": float(median(chars)) if chars else 0.0, "p95": _p(chars, 0.95), "max": max(chars, default=0)},
        "sentence_words": {"p50": float(median(sentence_words)) if sentence_words else 0.0,
                           "p95": _p(sentence_words, 0.95), "max": max(sentence_words, default=0),
                           "over_35": sum(w > 35 for w in sentence_words)},
        "thresholds": dict(rule),
    }
    passed = (over <= rule["max_phrases_over_limit"] and sum(unspeakable.values()) <= rule["max_unspeakable_chars"]
              and rechunk <= rule["max_engine_rechunk"] and len(errors) <= rule["max_pause_plan_errors"]
              and unterminated <= rule["max_unterminated"] and fragment_ratio <= rule["max_fragment_ratio"])
    return passed, metrics


__all__ = ["READING_QUALITY_GATE", "evaluate_reading_quality"]
