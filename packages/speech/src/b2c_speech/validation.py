"""Phase 8 deterministic provider, consent, audio, and alignment gates."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Protocol
from dataclasses import dataclass
from datetime import datetime, timezone

from b2c_contracts import AudioAsset, TTSRequest, TTSResult, VoiceConsent, VoiceSpec


class TTSProvider(Protocol):
    name: str

    def capabilities(self) -> Mapping[str, object]: ...
    def synthesize(self, req: TTSRequest) -> TTSResult: ...


@dataclass(frozen=True, slots=True)
class AudioMeasurements:
    rms_dbfs: float
    peak_dbfs: float
    noise_floor_dbfs: float
    sample_rate: int
    codec: str
    bitrate_bps: int
    cbr: bool


@dataclass(frozen=True, slots=True)
class AudioCheck:
    passed: bool
    violations: tuple[str, ...]


def validate_voice_consent(
    voice: VoiceSpec,
    consents: Mapping[str, VoiceConsent],
    *,
    now: datetime,
    required_scope: str,
) -> None:
    """Reject cloned voices without a matching, currently valid, in-scope consent."""
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ValueError("now must be timezone-aware UTC")
    if not voice.cloned:
        return
    if not voice.consent_id:
        raise ValueError("cloned voice requires consent_id")
    consent = consents.get(voice.consent_id)
    if consent is None:
        raise ValueError("voice consent not found")
    if consent.signed_at > now:
        raise ValueError("voice consent is not yet effective")
    if consent.expires_at is not None and consent.expires_at <= now:
        raise ValueError("voice consent has expired")
    if consent.scope != required_scope:
        raise ValueError("voice consent scope does not cover this use")


def validate_provider_result(request: TTSRequest, result: TTSResult) -> None:
    """Enforce the shared TTS provider contract for each synthesized segment."""
    if request.segment_id != result.segment_id:
        raise ValueError("provider returned a different segment_id")
    if not result.wav_uri.strip():
        raise ValueError("provider returned an empty wav_uri")


def check_audio_measurements(m: AudioMeasurements) -> AudioCheck:
    """Evaluate the documented ACX hard gate against measurements from a media probe."""
    violations: list[str] = []
    values = (m.rms_dbfs, m.peak_dbfs, m.noise_floor_dbfs)
    if any(not math.isfinite(v) for v in values):
        violations.append("non_finite_audio_measurement")
    else:
        if not -23.0 <= m.rms_dbfs <= -18.0:
            violations.append("rms_out_of_range")
        if m.peak_dbfs > -3.0:
            violations.append("peak_too_high")
        if m.noise_floor_dbfs > -60.0:
            violations.append("noise_floor_too_high")
    if m.sample_rate != 44100:
        violations.append("sample_rate_mismatch")
    if m.codec.casefold() != "mp3":
        violations.append("codec_mismatch")
    if m.bitrate_bps != 192_000:
        violations.append("bitrate_mismatch")
    if not m.cbr:
        violations.append("not_constant_bitrate")
    return AudioCheck(not violations, tuple(violations))


def validate_audio_asset(asset: AudioAsset, measurements: AudioMeasurements) -> AudioCheck:
    """Verify contract-carried loudness against independent probe measurements."""
    result = check_audio_measurements(measurements)
    mismatches = []
    if not math.isclose(asset.rms_dbfs, measurements.rms_dbfs, abs_tol=0.1):
        mismatches.append("asset_rms_probe_mismatch")
    if not math.isclose(asset.peak_dbfs, measurements.peak_dbfs, abs_tol=0.1):
        mismatches.append("asset_peak_probe_mismatch")
    if not mismatches:
        return result
    violations = (*result.violations, *mismatches)
    return AudioCheck(False, violations)


def check_segment_alignment(
    expected: Sequence[tuple[str, float, float]],
    actual: Sequence[tuple[str, float, float]],
    *,
    tolerance_s: float = 0.1,
) -> AudioCheck:
    """Compare aligned text/audio boundaries using the documented plus/minus100 ms tolerance."""
    if tolerance_s < 0:
        raise ValueError("tolerance_s must be nonnegative")
    violations: list[str] = []
    if len(expected) != len(actual):
        return AudioCheck(False, ("segment_count_mismatch",))
    prev_end = -math.inf
    for i, (exp, got) in enumerate(zip(expected, actual, strict=True)):
        exp_sid, exp_start, exp_end = exp
        sid, start, end = got
        if sid != exp_sid:
            violations.append(f"sid_mismatch:{i}")
        if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
            violations.append(f"invalid_timing:{i}")
            continue
        if start < prev_end:
            violations.append(f"overlap_or_out_of_order:{i}")
        prev_end = end
        if abs(start - exp_start) > tolerance_s or abs(end - exp_end) > tolerance_s:
            violations.append(f"alignment_tolerance_exceeded:{i}")
    return AudioCheck(not violations, tuple(violations))
