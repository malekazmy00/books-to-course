"""Intelligibility check by ASR back-transcription (R7; audit 07 §5.4 semantic gates).

``IntelligibilityChecker`` is the pluggable interface: transcribe one segment, compare with its
``spoken_text`` by CER/WER after Arabic normalization. No ASR model ships with the project now
(rule: no new heavy model without justification and an owner decision):

- ``NoIntelligibilityCheck`` (default): returns no verdict. The ``speech.asr-intelligibility``
  gate then has no result and a track that requires it stays a *candidate* (never accepted).
- ``FasterWhisperChecker``: optional local adapter for a CTranslate2 Whisper model. It needs the
  ``faster-whisper`` package and a model directory pinned by revision (docs/04 rule 9) — neither
  is installed. Documented option (audit 07 §5.3, Seed-TTS-eval practice): Whisper large-v3 /
  large-v3-turbo (``Systran/faster-whisper-large-v3``, ``deepdml/faster-whisper-large-v3-turbo-ct2``)
  on CPU int8; Whisper-tiny is NOT acceptable as a gate (audit 07 §3.1). Thresholds are set
  against a human recording of the same text (T-TTS-02), not guessed.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from scipy.signal import resample_poly

from .wav import PCM

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})


def asr_normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _DIACRITICS.sub("", text).translate(_FOLD).casefold()
    return " ".join(re.sub(r"[^\w\s]|_", " ", text).split())


def _edits(ref: list[str] | str, hyp: list[str] | str) -> int:
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = asr_normalize(reference).replace(" ", "")
    hyp = asr_normalize(hypothesis).replace(" ", "")
    return _edits(ref, hyp) / max(len(ref), 1)


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = asr_normalize(reference).split(), asr_normalize(hypothesis).split()
    return _edits(ref, hyp) / max(len(ref), 1)


class IntelligibilityChecker(Protocol):
    identity: Mapping[str, str]  # {"role": "asr", "model_id": ..., "revision": ...}

    def transcribe(self, audio: PCM, language: str) -> str: ...


class NoIntelligibilityCheck:
    """No ASR configured: no verdict (the gate stays open, the track stays a candidate)."""
    identity: Mapping[str, str] = {"role": "asr", "model_id": "none"}

    def transcribe(self, audio: PCM, language: str) -> str:  # pragma: no cover - never called
        raise NotImplementedError("no intelligibility checker is configured")


@dataclass(frozen=True)
class IntelligibilityResult:
    passed: bool
    metrics: dict[str, object] = field(default_factory=dict)


def evaluate_intelligibility(checker: IntelligibilityChecker, audio: PCM, spoken_text: str, *, max_cer: float,
                             language: str = "ar") -> IntelligibilityResult | None:
    if isinstance(checker, NoIntelligibilityCheck):
        return None
    hypothesis = checker.transcribe(audio, language)
    cer = character_error_rate(spoken_text, hypothesis)
    wer = word_error_rate(spoken_text, hypothesis)
    ref_len = len(asr_normalize(spoken_text).replace(" ", ""))
    hyp_len = len(asr_normalize(hypothesis).replace(" ", ""))
    metrics = {"cer": round(cer, 4), "wer": round(wer, 4), "max_cer": max_cer,
               "length_ratio": round(hyp_len / max(ref_len, 1), 3), "hypothesis": hypothesis[:500],
               "asr_model": checker.identity.get("model_id"), "asr_revision": checker.identity.get("revision")}
    return IntelligibilityResult(passed=cer <= max_cer, metrics=metrics)


class FasterWhisperChecker:
    """Optional adapter (not installed): CTranslate2 Whisper, deterministic decoding (temperature 0)."""

    def __init__(self, model_dir: str, *, model_id: str, revision: str, compute_type: str = "int8",
                 beam_size: int = 5, cpu_threads: int = 4) -> None:
        if not revision:
            raise ValueError("pin the ASR model revision (rule 9)")
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("faster-whisper is not installed; see b2c_speech.audio.intelligibility") from exc
        self._model = WhisperModel(model_dir, device="cpu", compute_type=compute_type,  # pragma: no cover
                                   cpu_threads=cpu_threads)
        self._beam = beam_size  # pragma: no cover
        self.identity = {"role": "asr", "model_id": model_id, "revision": revision}  # pragma: no cover

    def transcribe(self, audio: PCM, language: str) -> str:  # pragma: no cover - optional dependency
        x = audio.mono()
        if audio.sample_rate != 16000:
            x = resample_poly(x, 16000, audio.sample_rate)
        segments, _ = self._model.transcribe(np.asarray(x, dtype=np.float32), language=language,
                                             beam_size=self._beam, temperature=0.0, vad_filter=False)
        return " ".join(s.text.strip() for s in segments)


__all__ = ["FasterWhisperChecker", "IntelligibilityChecker", "IntelligibilityResult", "NoIntelligibilityCheck",
           "asr_normalize", "character_error_rate", "evaluate_intelligibility", "word_error_rate"]
