"""Deterministic audio QA (R7; audit 07 C1, §5.4): loudness (ITU-R BS.1770-4 / EBU R128), true peak,
clipping, silence/gaps, duration-per-character outliers, format; profiles app (-16 LUFS, -1 dBTP;
plan recommendation pending owner decision Q2) and ACX (T-AUD-01). T-AUD-02: a boosted file fails.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from b2c_speech.audio import (ACX, APP_SPEECH, PCM, NoIntelligibilityCheck, SegmentLimits, character_error_rate,
                              check_segment, check_track, duration_outliers, evaluate_intelligibility,
                              integrated_loudness, measure, normalize_loudness, profile_by_name, read_wav,
                              true_peak_dbtp, word_error_rate, write_wav)


def tone(seconds=10.0, sr=48000, freq=997.0, amp=0.1, phase=0.0):
    t = np.arange(int(seconds * sr)) / sr
    return amp * np.sin(2 * np.pi * freq * t + phase)


def pcm(x, sr=48000):
    return PCM(samples=np.asarray(x, dtype=np.float64), sample_rate=sr)


def speechlike(seconds=3.0, sr=24000, amp=0.3, seed=1):
    """Amplitude-modulated noise band: syllable-rate envelope, no long silence."""
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    carrier = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t) + 0.1 * rng.standard_normal(n)
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    x = carrier * envelope
    return amp * x / np.max(np.abs(x))


# ------------------------------------------------------------------ BS.1770 reference behaviour
@pytest.mark.parametrize("sr", [48000, 44100, 24000])
@pytest.mark.parametrize("amp", [1.0, 0.1, 0.01])
def test_integrated_loudness_of_997hz_sine(sr, amp):
    # BS.1770: a 997 Hz sine in one channel reads 20*log10(A) - 3.01 LKFS (EBU Tech 3341 basis)
    expected = 20 * math.log10(amp) - 3.01
    assert integrated_loudness(tone(sr=sr, amp=amp), sr) == pytest.approx(expected, abs=0.1)


def test_gating_ignores_silence():
    sr = 48000
    x = np.concatenate([tone(5, sr, amp=0.1), np.zeros(sr * 20)])
    # 20 s of silence would pull an ungated mean down by ~7 LU; gating leaves only the 400 ms
    # blocks that straddle the tone's end (partial energy, above the -10 LU relative gate)
    assert integrated_loudness(x, sr) == pytest.approx(20 * math.log10(0.1) - 3.01, abs=0.25)


def test_silence_has_no_loudness():
    assert integrated_loudness(np.zeros(48000 * 3), 48000) == -math.inf


def test_true_peak_finds_inter_sample_peak():
    sr = 48000
    x = tone(2, sr, freq=sr / 4, amp=0.5, phase=math.pi / 4)  # samples land at +-0.354, true peak 0.5
    sample_peak = 20 * math.log10(np.max(np.abs(x)))
    assert sample_peak == pytest.approx(20 * math.log10(0.5) - 3.01, abs=0.05)
    assert true_peak_dbtp(x, sr) == pytest.approx(20 * math.log10(0.5), abs=0.3)


# ------------------------------------------------------------------ measurements and track gates
def test_measure_reports_core_metrics():
    m = measure(pcm(tone(amp=0.1)))
    assert m.duration_s == pytest.approx(10.0)
    assert m.rms_dbfs == pytest.approx(20 * math.log10(0.1 / math.sqrt(2)), abs=0.05)
    assert m.peak_dbfs == pytest.approx(-20.0, abs=0.05)
    assert m.clipped_runs == 0 and m.longest_internal_silence_s == 0


def test_boosted_file_fails_every_profile():  # T-AUD-02 (neg)
    boosted = np.clip(speechlike(seconds=10, sr=44100) * 8, -1.0, 1.0)
    for profile in (APP_SPEECH, ACX):
        result = check_track(pcm(boosted, 44100), profile)
        assert not result.passed
        assert "clipping" in result.violations
        assert "true_peak_too_high" in result.violations or "peak_too_high" in result.violations


def test_normalized_speech_passes_app_profile():
    x = speechlike(seconds=20, sr=24000, amp=0.05)
    normalized, gain_db = normalize_loudness(pcm(x, 24000), APP_SPEECH)
    result = check_track(normalized, APP_SPEECH)
    assert gain_db > 0
    assert result.passed, result.violations
    assert result.metrics["integrated_lufs"] == pytest.approx(-16.0, abs=1.0)
    assert result.metrics["true_peak_dbtp"] <= -1.0


def test_normalization_never_exceeds_the_true_peak_ceiling():
    # very peaky signal: reaching -16 LUFS would push the peak over -1 dBTP; the gain stops at the ceiling
    sr = 48000
    x = np.zeros(sr * 10)
    x[::4800] = 0.9
    x += tone(10, sr, amp=0.001)
    normalized, _ = normalize_loudness(pcm(x, sr), APP_SPEECH)
    result = check_track(normalized, APP_SPEECH)
    assert result.metrics["true_peak_dbtp"] <= -1.0 + 1e-6
    assert "loudness_out_of_range" in result.violations


def test_acx_profile_requires_its_format():
    x = speechlike(seconds=20, sr=24000, amp=0.05)
    normalized, _ = normalize_loudness(pcm(x, 24000), ACX)
    result = check_track(normalized, ACX)
    assert "sample_rate_mismatch" in result.violations  # ACX: 44.1 kHz
    at_44k, _ = normalize_loudness(pcm(speechlike(seconds=20, sr=44100, amp=0.05), 44100), ACX)
    ok = check_track(at_44k, ACX)
    assert -23.0 <= ok.metrics["rms_dbfs"] <= -18.0 and ok.metrics["peak_dbfs"] <= -3.0


def test_profiles_by_name():
    assert profile_by_name("app-speech-16") is APP_SPEECH and profile_by_name("acx") is ACX
    assert APP_SPEECH.integrated_lufs == -16.0 and APP_SPEECH.max_true_peak_dbtp == -1.0
    with pytest.raises(ValueError):
        profile_by_name("louder")


def test_long_internal_gap_fails_track():
    sr = 24000
    x = np.concatenate([speechlike(2, sr), np.zeros(sr * 3), speechlike(2, sr)])
    normalized, _ = normalize_loudness(pcm(x, sr), APP_SPEECH)
    result = check_track(normalized, APP_SPEECH)
    assert "internal_silence_too_long" in result.violations
    assert result.metrics["longest_internal_silence_s"] == pytest.approx(3.0, abs=0.1)


# ------------------------------------------------------------------ per segment
LIMITS = SegmentLimits(sample_rate=24000)


def test_segment_passes():
    result = check_segment(pcm(speechlike(3.0), 24000), "نص منطوق قصير بطول معقول جدا", LIMITS)
    assert result.passed, result.violations
    chars = len("نص منطوق قصير بطول معقول جدا".replace(" ", ""))  # spaces are not spoken characters
    assert result.metrics["seconds_per_char"] == pytest.approx(3.0 / chars, rel=1e-6)


def test_segment_wrong_rate_fails():
    result = check_segment(pcm(speechlike(3.0, sr=22050), 22050), "نص", LIMITS)
    assert "sample_rate_mismatch" in result.violations


def test_truncated_segment_fails_absolute_rate():
    text = "هذه جملة طويلة جدا لا يمكن أن تنطق في نصف ثانية فقط مهما كانت السرعة"
    result = check_segment(pcm(speechlike(0.5), 24000), text, LIMITS)
    assert "speech_rate_out_of_range" in result.violations


def test_silent_segment_fails():
    result = check_segment(pcm(np.zeros(24000 * 2), 24000), "نص", LIMITS)
    assert "silent" in result.violations


def test_clipped_segment_fails():
    x = np.clip(speechlike(3.0) * 10, -1, 1)
    assert "clipping" in check_segment(pcm(x, 24000), "نص منطوق قصير بطول معقول جدا", LIMITS).violations


def test_duration_outliers_relative_to_median():
    stats = {f"s{i}": (1.0 + 0.01 * i, 10) for i in range(8)}  # ~0.1 s/char
    stats["long"] = (2.0, 10)   # +100%: hallucination/repetition
    stats["short"] = (0.5, 10)  # -50%: truncation
    assert duration_outliers(stats, tolerance=0.35) == ["long", "short"]
    assert duration_outliers({"a": (1.0, 10), "b": (5.0, 10)}, tolerance=0.35, min_segments=5) == []


# ------------------------------------------------------------------ wav io
def test_wav_round_trip_16bit():
    x = speechlike(1.0)
    again = read_wav(write_wav(pcm(x, 24000)))
    assert again.sample_rate == 24000 and again.channels == 1 and again.sample_width == 2
    assert np.max(np.abs(again.samples - x)) < 1 / 16384


def test_read_wav_rejects_garbage():
    with pytest.raises(ValueError):
        read_wav(b"RIFF0000WAVEnope")


# ------------------------------------------------------------------ intelligibility (interface)
def test_cer_and_wer_with_arabic_normalization():
    assert character_error_rate("مَدْرَسَةٌ كبيرة", "مدرسه كبيره") == 0.0  # diacritics, ta marbuta
    assert character_error_rate("ابcد", "ابcد") == 0.0
    assert character_error_rate("كتاب", "كتب") == pytest.approx(0.25)
    assert word_error_rate("ذهب الولد الى المدرسة", "ذهب الولد إلى المدرسه") == 0.0
    assert word_error_rate("a b c d", "a x c") == pytest.approx(0.5)


def test_no_checker_means_no_verdict():
    assert evaluate_intelligibility(NoIntelligibilityCheck(), pcm(speechlike(1.0), 24000), "نص", max_cer=0.12) is None


class FakeASR:
    identity = {"role": "asr", "model_id": "fake-asr", "revision": "1"}

    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, language):
        return self.text


def test_checker_verdict_uses_cer_threshold():
    good = evaluate_intelligibility(FakeASR("ذهب الولد الى المدرسة"), pcm(speechlike(1.0), 24000),
                                    "ذهب الولد إلى المدرسة", max_cer=0.12)
    bad = evaluate_intelligibility(FakeASR("ذهب"), pcm(speechlike(1.0), 24000), "ذهب الولد إلى المدرسة",
                                   max_cer=0.12)
    assert good.passed and good.metrics["cer"] == 0.0 and good.metrics["asr_model"] == "fake-asr"
    assert not bad.passed and bad.metrics["cer"] > 0.12
