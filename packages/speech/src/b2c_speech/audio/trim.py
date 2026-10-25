"""Edge-silence trimming and pause-exact assembly (speech-readability report, open item 3).

Each TTS segment comes back with its own leading and trailing silence, so the gap a listener
hears between two segments was ``trailing + planned pause + leading``, not the planned
``SpeechSegment.pause_after_ms`` (``boundary_after`` / ``break_after``). Here every segment is
cut down to a short guard (``keep_s``, default 40 ms) of its original edge, so soft onsets and
offsets that sit just under the gate are never clipped, and the assembler inserts
``pause - kept trailing guard - kept leading guard`` of digital silence: the quiet span between
two segments is then the planned pause.

The gate is the same -50 dBFS frame level (10 ms frames) the segment QA uses for silence.
A short linear fade at a cut point avoids a click when the guard starts on a non-zero sample.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_THRESHOLD_DBFS = -50.0
DEFAULT_KEEP_S = 0.04
DEFAULT_FADE_S = 0.005
FRAME_S = 0.01


@dataclass(frozen=True)
class TrimResult:
    samples: np.ndarray
    removed_leading_s: float
    removed_trailing_s: float
    kept_leading_s: float  # silence left before the first voiced frame (<= keep_s)
    kept_trailing_s: float


def trim_settings(*, threshold_dbfs: float = DEFAULT_THRESHOLD_DBFS, keep_s: float = DEFAULT_KEEP_S,
                  fade_s: float = DEFAULT_FADE_S) -> dict[str, float]:
    """Recorded in track metadata so a reader knows how the pauses were made."""
    return {"threshold_dbfs": threshold_dbfs, "keep_ms": round(keep_s * 1000, 3), "fade_ms": round(fade_s * 1000, 3)}


@dataclass(frozen=True)
class _Bounds:
    cut_start: int
    cut_end: int
    voiced_start: int
    voiced_end: int
    fade_in: bool
    fade_out: bool


def _bounds(x: np.ndarray, sample_rate: int, threshold_dbfs: float, keep_s: float) -> _Bounds | None:
    frame = max(1, int(round(FRAME_S * sample_rate)))
    n = len(x) // frame
    if n == 0:
        return None
    frames = x[: n * frame].reshape(n, frame)
    with np.errstate(divide="ignore"):
        level = 10 * np.log10(np.einsum("ij,ij->i", frames, frames) / frame + 1e-30)
    loud = np.flatnonzero(level > threshold_dbfs)
    if loud.size == 0:  # silent segment: nothing to anchor on; segment QA already reports it
        return None
    # voiced span at sample resolution inside the first/last loud frames (frames are coarse)
    first_frame, last_frame = int(loud[0]), int(loud[-1])
    gate = 10 ** (threshold_dbfs / 20)
    head = np.flatnonzero(np.abs(x[first_frame * frame:(first_frame + 1) * frame]) > gate)
    start = first_frame * frame + (int(head[0]) if head.size else 0)
    tail_end = len(x) if last_frame == n - 1 else (last_frame + 1) * frame
    tail = np.flatnonzero(np.abs(x[last_frame * frame:tail_end]) > gate)
    end = last_frame * frame + int(tail[-1]) + 1 if tail.size else tail_end
    keep = int(round(keep_s * sample_rate))
    cut_start, cut_end = max(0, start - keep), min(len(x), end + keep)
    return _Bounds(cut_start=cut_start, cut_end=cut_end, voiced_start=start, voiced_end=end,
                   fade_in=cut_start > 0 or abs(x[cut_start]) > gate,
                   fade_out=cut_end < len(x) or abs(x[cut_end - 1]) > gate)


def _fade(out: np.ndarray, b: _Bounds, fade: int) -> None:
    fade = min(fade, len(out) // 2)
    if fade <= 0:
        return
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
    if b.fade_in:
        out[:fade] *= ramp
    if b.fade_out:
        out[-fade:] *= ramp[::-1]


def _check(sample_rate: int, keep_s: float, fade_s: float) -> None:
    if sample_rate <= 0 or keep_s < 0 or fade_s < 0:
        raise ValueError("sample_rate must be positive; keep_s and fade_s must not be negative")


def trim_edges(samples: np.ndarray, sample_rate: int, *, threshold_dbfs: float = DEFAULT_THRESHOLD_DBFS,
               keep_s: float = DEFAULT_KEEP_S, fade_s: float = DEFAULT_FADE_S) -> TrimResult:
    _check(sample_rate, keep_s, fade_s)
    x = np.asarray(samples, dtype=np.float64)
    b = _bounds(x, sample_rate, threshold_dbfs, keep_s)
    if b is None:
        return TrimResult(samples=x, removed_leading_s=0.0, removed_trailing_s=0.0, kept_leading_s=0.0,
                          kept_trailing_s=0.0)
    out = x[b.cut_start:b.cut_end].copy()
    _fade(out, b, int(round(fade_s * sample_rate)))
    return TrimResult(samples=out, removed_leading_s=b.cut_start / sample_rate,
                      removed_trailing_s=(len(x) - b.cut_end) / sample_rate,
                      kept_leading_s=(b.voiced_start - b.cut_start) / sample_rate,
                      kept_trailing_s=(b.cut_end - b.voiced_end) / sample_rate)


def assemble_with_pauses(pieces: Sequence[np.ndarray], pauses_ms: Sequence[int], sample_rate: int, *,
                         threshold_dbfs: float = DEFAULT_THRESHOLD_DBFS, keep_s: float = DEFAULT_KEEP_S,
                         fade_s: float = DEFAULT_FADE_S) -> tuple[np.ndarray, list[dict[str, float]]]:
    """Trim every piece, then join them so the silence between piece i and i+1 is ``pauses_ms[i]``.

    Returns the track and one timing record per piece (start/end of the trimmed piece in the
    track, the inserted digital silence after it, and what was removed/kept at its edges).
    The track is allocated once and filled in place (no trimmed copies, no concatenation)."""
    _check(sample_rate, keep_s, fade_s)
    if len(pieces) != len(pauses_ms):
        raise ValueError("one pause per piece (the last one is ignored)")
    bounds = []
    for p in pieces:
        b = _bounds(np.asarray(p), sample_rate, threshold_dbfs, keep_s)
        bounds.append(b or _Bounds(0, len(p), 0, len(p), False, False))
    inserted = []
    for i, b in enumerate(bounds):
        if i == len(bounds) - 1:
            inserted.append(0)
            continue
        nxt = bounds[i + 1]
        guard = (b.cut_end - b.voiced_end) + (nxt.voiced_start - nxt.cut_start)
        inserted.append(max(0, int(round(pauses_ms[i] / 1000 * sample_rate)) - guard))
    track = np.zeros(sum(b.cut_end - b.cut_start for b in bounds) + sum(inserted))
    fade = int(round(fade_s * sample_rate))
    timings: list[dict[str, float]] = []
    clock = 0
    for p, b, gap in zip(pieces, bounds, inserted):
        end = clock + b.cut_end - b.cut_start
        track[clock:end] = p[b.cut_start:b.cut_end]
        _fade(track[clock:end], b, fade)
        ms = 1000 / sample_rate
        timings.append({"start_s": clock / sample_rate, "end_s": end / sample_rate, "pause_inserted_ms": gap * ms,
                        "removed_leading_ms": b.cut_start * ms, "removed_trailing_ms": (len(p) - b.cut_end) * ms,
                        "kept_leading_ms": (b.voiced_start - b.cut_start) * ms,
                        "kept_trailing_ms": (b.cut_end - b.voiced_end) * ms})
        clock = end + gap
    return track, timings


__all__ = ["DEFAULT_FADE_S", "DEFAULT_KEEP_S", "DEFAULT_THRESHOLD_DBFS", "TrimResult", "assemble_with_pauses",
           "trim_edges", "trim_settings"]
