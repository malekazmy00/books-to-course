"""Speech-text artifact builder (R7; audit 07 C2/C3, §5.2 S1).

Input: the book's translation units and their current ``UnitTranslation`` results. Only units
whose decision is ``accepted`` (by policy after semantic QE, or by a human reviewer) are spoken;
``dropped`` units are recorded as excluded; any unit still ``needs_review``/``rejected`` or
without a result refuses the whole build (``NotSpeechReady``): audio never speaks a translation
that was not accepted. Apparatus (footnotes, references, TOC, index, captions, tables, noise) is
excluded by kind, and a unit whose accepted text is not in the target language is excluded as
``wrong_language`` (audit 07: 2/15 tracks read English with an Arabic voice).

Output: ``SpeechTextDocument`` — deterministic spoken text in bounded TTS segments, each with
the unit ids, block ids, normalization rules and lexicon hits/misses it came from.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence

from b2c_contracts import (ArtifactRef, SpeechExclusion, SpeechSegment, SpeechTextDocument, TranslationUnit,
                           UnitTranslation)

from .lexicon import PronunciationLexicon
from .normalize import NORMALIZER_VERSION, normalize_text, speech_text_violations
from .segment import split_for_tts

FRONTEND_VERSION = f"speech-text-{NORMALIZER_VERSION}-seg1"
SPOKEN_KINDS = {"prose": "paragraph", "heading": "heading", "list_item": "list_item"}
# Pause after the last segment of a unit, by structure; between sentences of one unit: SENTENCE.
PAUSE_AFTER_MS = {"heading": 700, "paragraph": 500, "list_item": 400, "caption": 500, "question": 600,
                  "answer": 600}
SENTENCE_PAUSE_MS = 250
PUBLISHABLE = ("accepted", "dropped")


class NotSpeechReady(ValueError):
    """The input contains a unit whose translation is not accepted (or has no result)."""


def _letters(text: str) -> tuple[int, int]:
    arabic = len(re.findall(r"[ء-يٱ-ۓ]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return arabic, latin


def wrong_language(text: str, language: str) -> bool:
    arabic, latin = _letters(text)
    total = arabic + latin
    if language == "ar":
        return latin >= 20 and latin / max(total, 1) >= 0.6
    return arabic >= 20 and arabic / max(total, 1) >= 0.6


def strip_restored_markers(text: str, markers: Sequence[str]) -> tuple[str, bool]:
    """Undo ``restore_footnotes`` (markers appended in order, each after a space)."""
    out = text.rstrip()
    stripped = False
    for marker in reversed(list(markers)):
        if marker and out.endswith(" " + marker):
            out = out[: -len(marker) - 1].rstrip()
            stripped = True
        elif marker and out.endswith(marker) and not out[: -len(marker)][-1:].isdigit():
            out = out[: -len(marker)].rstrip()
            stripped = True
    return out, stripped


def build_speech_text(units: Sequence[TranslationUnit], translations: Mapping[str, UnitTranslation], *,
                      book_id: str, source: ArtifactRef, lexicon: PronunciationLexicon | None = None,
                      language: str = "ar", target_chars: int = 200, max_chars: int = 300) -> SpeechTextDocument:
    if language != "ar":
        raise ValueError("only the Arabic speech front end exists (R7); English is not built yet")
    lexicon = lexicon or PronunciationLexicon()
    not_ready = [u.unit_id for u in units
                 if (t := translations.get(u.unit_id)) is None or t.decision not in PUBLISHABLE]
    if not_ready:
        raise NotSpeechReady(f"{len(not_ready)} unit(s) are not accepted (first: {not_ready[0]}); "
                             "speech is made from accepted translations only")
    segments: list[SpeechSegment] = []
    excluded: list[SpeechExclusion] = []
    for unit in units:
        translation = translations[unit.unit_id]
        if translation.decision == "dropped":
            excluded.append(SpeechExclusion(unit_id=unit.unit_id, reason="dropped"))
            continue
        structure = SPOKEN_KINDS.get(unit.kind)
        if structure is None:
            excluded.append(SpeechExclusion(unit_id=unit.unit_id, reason=f"not_spoken_kind:{unit.kind}"))
            continue
        text, stripped = strip_restored_markers(translation.target_text, unit.footnote_markers)
        if wrong_language(text, language):
            excluded.append(SpeechExclusion(unit_id=unit.unit_id, reason="wrong_language"))
            continue
        normalized = normalize_text(text, lexicon=lexicon, structure=structure)
        if stripped:
            normalized.rules.insert(0, "footnote_marker_removed")
        if not re.search(r"\w", normalized.text):
            excluded.append(SpeechExclusion(unit_id=unit.unit_id, reason="empty_after_normalization"))
            continue
        chunks = split_for_tts(normalized.text, target_chars=target_chars, max_chars=max_chars)
        for i, chunk in enumerate(chunks):
            last = i == len(chunks) - 1
            seq = len(segments)
            segments.append(SpeechSegment(
                segment_id=f"{book_id}:s{seq:05d}", seq=seq, unit_ids=[unit.unit_id], block_ids=list(unit.block_ids),
                structure=structure, source_text=text, spoken_text=chunk,
                rules_applied=list(dict.fromkeys(normalized.rules)), lexicon_hits=sorted(set(normalized.lexicon_hits)),
                lexicon_misses=sorted(set(normalized.lexicon_misses)),
                pause_after_ms=PAUSE_AFTER_MS[structure] if last else SENTENCE_PAUSE_MS))
    if not segments:
        raise NotSpeechReady("no spoken text: every unit was excluded")
    return SpeechTextDocument(book_id=book_id, language=language, frontend_version=FRONTEND_VERSION,
                              lexicon_digest=lexicon.digest, source=source, segments=segments, excluded=excluded)


SPEECH_TEXT_GATE = {"max_violations": 0, "max_lexicon_misses": 0, "max_wrong_language": 0, "min_segments": 1}


def evaluate_speech_text(doc: SpeechTextDocument) -> tuple[bool, dict[str, object]]:
    """Deterministic speech-text gate (plan R7): 0 raw digits / ``[n]`` / Latin / URLs in the
    spoken text, 0 Latin words outside the lexicon, 0 accepted units in the wrong language."""
    violations: Counter[str] = Counter()
    for segment in doc.segments:
        violations.update(speech_text_violations(segment.spoken_text))
    missed = sorted({w for s in doc.segments for w in s.lexicon_misses})
    excluded = Counter(e.reason.split(":")[0] if e.reason.startswith("not_spoken_kind") else e.reason
                       for e in doc.excluded)
    rules = Counter(r for s in doc.segments for r in s.rules_applied)
    metrics: dict[str, object] = {
        "segments": len(doc.segments), "units_spoken": len({u for s in doc.segments for u in s.unit_ids}),
        "spoken_chars": sum(len(s.spoken_text) for s in doc.segments),
        "violations": dict(sorted(violations.items())), "lexicon_misses": len(missed), "missed_words": missed[:50],
        "lexicon_hits": len({w for s in doc.segments for w in s.lexicon_hits}),
        "excluded": dict(sorted(excluded.items())), "rules": dict(sorted(rules.items())),
        "frontend_version": doc.frontend_version, "lexicon_digest": doc.lexicon_digest,
    }
    rule = SPEECH_TEXT_GATE
    passed = (sum(violations.values()) <= rule["max_violations"] and len(missed) <= rule["max_lexicon_misses"]
              and excluded.get("wrong_language", 0) <= rule["max_wrong_language"]
              and len(doc.segments) >= rule["min_segments"])
    return passed, metrics


__all__ = ["FRONTEND_VERSION", "NotSpeechReady", "PAUSE_AFTER_MS", "SENTENCE_PAUSE_MS", "SPEECH_TEXT_GATE",
           "SPOKEN_KINDS", "build_speech_text", "evaluate_speech_text", "strip_restored_markers", "wrong_language"]
