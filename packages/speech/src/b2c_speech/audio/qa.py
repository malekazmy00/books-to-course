"""Deterministic audio QA per segment and per track (R7; audit 07 C1, §5.4).

Profiles (owner decision Q2 is pending; the default is the plan's recommendation):
- ``app-speech-16`` (default): integrated loudness -16 LUFS +-1 LU, true peak <= -1 dBTP
  (AES TD1008 / Apple streaming speech practice, audit 07 §5.5).
- ``acx``: RMS -23..-18 dBFS, sample peak <= -3 dBFS, noise floor <= -60 dBFS, 44.1 kHz; the
  192 kbps CBR MP3 part of T-AUD-01 is checked on the encoded file (``mp3_frame_probe``).
Both profiles also require: no clipping, no internal silence longer than 1.5 s, not silent.

Per segment: sample rate/format, not silent, no clipping, leading/trailing/internal silence,
absolute speech rate (characters per second) bounds; across segments of one voice:
seconds-per-character outliers beyond +-35% of the median (truncation / hallucination).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from .loudness import integrated_loudness, true_peak_dbtp
from .wav import PCM

CLIP_LEVEL = 0.999
CLIP_RUN = 3  # consecutive samples at full scale = a clipped run
FRAME_S = 0.02
NOISE_WINDOW_S = 0.5
FLOOR_DB = -160.0


@dataclass(frozen=True)
class LoudnessProfile:
    name: str
    integrated_lufs: float | None = None
    lufs_tolerance: float = 1.0
    max_true_peak_dbtp: float | None = None
    rms_range_dbfs: tuple[float, float] | None = None
    max_peak_dbfs: float | None = None
    max_noise_floor_dbfs: float | None = None
    sample_rates: tuple[int, ...] | None = None
    mp3_bitrate_bps: int | None = None  # delivery encode requirement (checked on the MP3 itself)
    max_internal_silence_s: float = 1.5
    silence_threshold_dbfs: float = -50.0

    def definition(self) -> dict[str, object]:
        return {k: v for k, v in self.__dict__.items()}


APP_SPEECH = LoudnessProfile(name="app-speech-16", integrated_lufs=-16.0, lufs_tolerance=1.0, max_true_peak_dbtp=-1.0)
ACX = LoudnessProfile(name="acx", rms_range_dbfs=(-23.0, -18.0), max_peak_dbfs=-3.0, max_noise_floor_dbfs=-60.0,
                      sample_rates=(44100,), mp3_bitrate_bps=192_000)
PROFILES = {p.name: p for p in (APP_SPEECH, ACX)}
DEFAULT_PROFILE = APP_SPEECH.name  # plan recommendation; owner decision Q2 pending


def profile_by_name(name: str) -> LoudnessProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown loudness profile {name!r}; known: {sorted(PROFILES)}") from None


@dataclass(frozen=True)
class AudioStats:
    duration_s: float
    sample_rate: int
    channels: int
    integrated_lufs: float
    true_peak_dbtp: float
    peak_dbfs: float
    rms_dbfs: float
    noise_floor_dbfs: float
    clipped_samples: int
    clipped_runs: int
    leading_silence_s: float
    trailing_silence_s: float
    longest_internal_silence_s: float
    silent: bool

    def as_metrics(self) -> dict[str, object]:
        return {k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else
                    (None if isinstance(v, float) else v)) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class QAResult:
    passed: bool
    violations: tuple[str, ...]
    metrics: dict[str, object] = field(default_factory=dict)


def _db(value: float) -> float:
    return 20 * math.log10(value) if value > 0 else -math.inf


def _clipped(x: np.ndarray) -> tuple[int, int]:
    hot = np.abs(x) >= CLIP_LEVEL
    count = int(hot.sum())
    if count < CLIP_RUN:
        return count, 0
    edges = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return count, int(np.sum((ends - starts) >= CLIP_RUN))


def _silence(x: np.ndarray, sample_rate: int, threshold_dbfs: float) -> tuple[float, float, float, bool]:
    frame = max(1, int(FRAME_S * sample_rate))
    n = len(x) // frame
    if n == 0:
        return 0.0, 0.0, 0.0, True
    frames = x[: n * frame].reshape(n, frame)
    with np.errstate(divide="ignore"):
        level = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-30)
    loud = level > threshold_dbfs
    if not loud.any():
        return n * FRAME_S, n * FRAME_S, 0.0, True
    first, last = int(np.argmax(loud)), int(n - 1 - np.argmax(loud[::-1]))
    inner = ~loud[first:last + 1]
    longest = run = 0
    for quiet in inner:
        run = run + 1 if quiet else 0
        longest = max(longest, run)
    return first * FRAME_S, (n - 1 - last) * FRAME_S, longest * FRAME_S, False


def _noise_floor(x: np.ndarray, sample_rate: int) -> float:
    window = max(1, int(NOISE_WINDOW_S * sample_rate))
    n = len(x) // window
    if n == 0:
        return FLOOR_DB
    rms = np.sqrt(np.mean(x[: n * window].reshape(n, window) ** 2, axis=1))
    return max(FLOOR_DB, _db(float(rms.min())))


def measure(audio: PCM, *, silence_threshold_dbfs: float = -50.0) -> AudioStats:
    x = audio.mono()
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    rms = float(np.sqrt(np.mean(x ** 2))) if x.size else 0.0
    clipped, runs = _clipped(audio.samples.reshape(-1))
    leading, trailing, internal, silent = _silence(x, audio.sample_rate, silence_threshold_dbfs)
    return AudioStats(
        duration_s=audio.duration_s, sample_rate=audio.sample_rate, channels=audio.channels,
        integrated_lufs=integrated_loudness(audio.samples, audio.sample_rate),
        true_peak_dbtp=true_peak_dbtp(audio.samples, audio.sample_rate), peak_dbfs=_db(peak), rms_dbfs=_db(rms),
        noise_floor_dbfs=_noise_floor(x, audio.sample_rate), clipped_samples=clipped, clipped_runs=runs,
        leading_silence_s=leading, trailing_silence_s=trailing, longest_internal_silence_s=internal, silent=silent)


def check_track(audio: PCM, profile: LoudnessProfile, stats: AudioStats | None = None) -> QAResult:
    s = stats or measure(audio, silence_threshold_dbfs=profile.silence_threshold_dbfs)
    v: list[str] = []
    if s.silent:
        v.append("silent")
    if profile.integrated_lufs is not None and not (
            math.isfinite(s.integrated_lufs) and abs(s.integrated_lufs - profile.integrated_lufs) <= profile.lufs_tolerance):
        v.append("loudness_out_of_range")
    if profile.max_true_peak_dbtp is not None and s.true_peak_dbtp > profile.max_true_peak_dbtp:
        v.append("true_peak_too_high")
    if profile.rms_range_dbfs is not None and not profile.rms_range_dbfs[0] <= s.rms_dbfs <= profile.rms_range_dbfs[1]:
        v.append("rms_out_of_range")
    if profile.max_peak_dbfs is not None and s.peak_dbfs > profile.max_peak_dbfs:
        v.append("peak_too_high")
    if profile.max_noise_floor_dbfs is not None and s.noise_floor_dbfs > profile.max_noise_floor_dbfs:
        v.append("noise_floor_too_high")
    if profile.sample_rates is not None and s.sample_rate not in profile.sample_rates:
        v.append("sample_rate_mismatch")
    if s.clipped_runs:
        v.append("clipping")
    if s.longest_internal_silence_s > profile.max_internal_silence_s:
        v.append("internal_silence_too_long")
    return QAResult(passed=not v, violations=tuple(v), metrics={**s.as_metrics(), "profile": profile.name})


def normalize_loudness(audio: PCM, profile: LoudnessProfile, *, margin_db: float = 0.1) -> tuple[PCM, float]:
    """Single linear gain towards the profile target, never pushing the peak over its ceiling.

    (Two-pass in the EBU sense: measure, then apply one gain. No limiter: if the ceiling stops
    the gain before the target, the loudness gate fails and says so.)"""
    x = audio.samples
    if profile.integrated_lufs is not None:
        current, target = integrated_loudness(x, audio.sample_rate), profile.integrated_lufs
        peak, ceiling = true_peak_dbtp(x, audio.sample_rate), profile.max_true_peak_dbtp
    else:
        rms = _db(float(np.sqrt(np.mean(audio.mono() ** 2))))
        low, high = profile.rms_range_dbfs or (-20.0, -20.0)
        current, target = rms, (low + high) / 2
        peak, ceiling = _db(float(np.max(np.abs(x)))), profile.max_peak_dbfs
    if not math.isfinite(current):
        return audio, 0.0
    gain = target - current
    if ceiling is not None and math.isfinite(peak) and peak + gain > ceiling - margin_db:
        gain = ceiling - margin_db - peak
    scaled = PCM(samples=x * 10 ** (gain / 20), sample_rate=audio.sample_rate, sample_width=audio.sample_width,
                 encoding=audio.encoding)
    return scaled, gain


@dataclass(frozen=True)
class SegmentLimits:
    sample_rate: int
    min_chars_per_s: float = 3.0
    max_chars_per_s: float = 25.0
    min_duration_s: float = 0.2
    max_leading_silence_s: float = 1.0
    max_trailing_silence_s: float = 1.5
    max_internal_silence_s: float = 1.5
    silence_threshold_dbfs: float = -50.0

    def definition(self) -> dict[str, object]:
        return dict(self.__dict__)


def check_segment(audio: PCM, spoken_text: str, limits: SegmentLimits) -> QAResult:
    s = measure(audio, silence_threshold_dbfs=limits.silence_threshold_dbfs)
    chars = len("".join(spoken_text.split()))
    v: list[str] = []
    if audio.sample_rate != limits.sample_rate:
        v.append("sample_rate_mismatch")
    if audio.channels != 1:
        v.append("not_mono")
    if s.silent or s.duration_s < limits.min_duration_s:
        v.append("silent")
    if s.clipped_runs:
        v.append("clipping")
    voiced = max(s.duration_s - s.leading_silence_s - s.trailing_silence_s, 1e-6)
    rate = chars / voiced
    if not s.silent and not limits.min_chars_per_s <= rate <= limits.max_chars_per_s:
        v.append("speech_rate_out_of_range")
    if s.leading_silence_s > limits.max_leading_silence_s:
        v.append("leading_silence_too_long")
    if s.trailing_silence_s > limits.max_trailing_silence_s:
        v.append("trailing_silence_too_long")
    if s.longest_internal_silence_s > limits.max_internal_silence_s:
        v.append("internal_silence_too_long")
    metrics = {**s.as_metrics(), "chars": chars, "chars_per_second": round(rate, 3),
               "seconds_per_char": s.duration_s / chars if chars else None}
    return QAResult(passed=not v, violations=tuple(v), metrics=metrics)


def duration_outliers(stats: Mapping[str, tuple[float, int]], *, tolerance: float = 0.35,
                      min_segments: int = 5) -> list[str]:
    """Segments whose seconds-per-character deviates from the median of the same voice by more
    than ``tolerance`` (audit 07 §5.4: +-35%). Too few segments: no reliable median, no verdict."""
    ratios = {sid: duration / chars for sid, (duration, chars) in stats.items() if chars > 0 and duration > 0}
    if len(ratios) < min_segments:
        return []
    median = float(np.median(list(ratios.values())))
    return sorted(sid for sid, r in ratios.items() if abs(r / median - 1) > tolerance)


__all__ = ["ACX", "APP_SPEECH", "AudioStats", "DEFAULT_PROFILE", "LoudnessProfile", "PROFILES", "QAResult",
           "SegmentLimits", "check_segment", "check_track", "duration_outliers", "measure", "normalize_loudness",
           "profile_by_name"]
