from datetime import datetime, timedelta, timezone

import pytest
from b2c_contracts import AudioAsset, TTSRequest, TTSResult, VoiceConsent, VoiceSpec
from b2c_speech.validation import (
    AudioMeasurements,
    check_audio_measurements,
    check_segment_alignment,
    validate_audio_asset,
    validate_provider_result,
    validate_voice_consent,
)

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)
VALID = AudioMeasurements(-20.0, -4.0, -62.0, 44100, "mp3", 192000, True)

def test_cloned_voice_requires_existing_current_in_scope_consent():
    voice = VoiceSpec(provider="omnivoice", voice_id="clone-1", cloned=True, consent_id="c1")
    consent = VoiceConsent(consent_id="c1", artist_name="Artist", scope="course narration",
        signed_at=NOW - timedelta(days=1), expires_at=NOW + timedelta(days=1),
        document_ref="legal/ref", sample_uris=["private://sample"])
    validate_voice_consent(voice, {"c1": consent}, now=NOW, required_scope="course narration")
    with pytest.raises(ValueError, match="scope"):
        validate_voice_consent(voice, {"c1": consent}, now=NOW, required_scope="commercial ads")
    expired = consent.model_copy(update={"expires_at": NOW})
    with pytest.raises(ValueError, match="expired"):
        validate_voice_consent(voice, {"c1": expired}, now=NOW, required_scope="course narration")

def test_noncloned_voice_does_not_require_consent():
    voice = VoiceSpec(provider="supertonic3", voice_id="default", cloned=False)
    validate_voice_consent(voice, {}, now=NOW, required_scope="course narration")

def test_provider_result_keeps_segment_identity():
    req = TTSRequest(segment_id="s1", text_ar_diacritized="text",
        voice=VoiceSpec(provider="supertonic3", voice_id="v1", cloned=False))
    result = TTSResult(segment_id="s1", wav_uri="s3://audio/s1.wav",
        duration_s=1.0, provider_version="test", rtf=0.5)
    validate_provider_result(req, result)
    with pytest.raises(ValueError, match="segment_id"):
        validate_provider_result(req, result.model_copy(update={"segment_id": "s2"}))

def test_acx_measurements_pass_and_each_hard_limit_is_checked():
    assert check_audio_measurements(VALID).passed
    bad = AudioMeasurements(-17.0, -2.0, -55.0, 48000, "wav", 128000, False)
    assert set(check_audio_measurements(bad).violations) == {
        "rms_out_of_range", "peak_too_high", "noise_floor_too_high",
        "sample_rate_mismatch", "codec_mismatch", "bitrate_mismatch", "not_constant_bitrate",
    }

def test_audio_asset_measurement_must_match_independent_probe():
    asset = AudioAsset(lesson_id="l1", mp3_uri="s3://a/l1.mp3", segments=[],
        rms_dbfs=-20.0, peak_dbfs=-4.0, noise_floor_dbfs=-62.0, acx_pass=True,
        asr_cer=0.01, voice=VoiceSpec(provider="supertonic3", voice_id="v1", cloned=False))
    assert validate_audio_asset(asset, VALID).passed
    assert "asset_rms_probe_mismatch" in validate_audio_asset(
        asset, VALID.__class__(-21.0, -4.0, -62.0, 44100, "mp3", 192000, True)
    ).violations

def test_alignment_uses_documented_tolerance_and_checks_ids():
    expected = [("s1", 0.0, 2.0), ("s2", 2.0, 4.0)]
    assert check_segment_alignment(expected, expected).passed
    bad = [("wrong", 0.0, 2.2), ("s2", 2.0, 4.0)]
    result = check_segment_alignment(expected, bad)
    assert "sid_mismatch:0" in result.violations
    assert "alignment_tolerance_exceeded:0" in result.violations
    assert "overlap_or_out_of_order:1" in result.violations

def test_alignment_rejects_bad_tolerance_and_length_mismatch():
    with pytest.raises(ValueError):
        check_segment_alignment([], [], tolerance_s=-0.1)
    assert check_segment_alignment([("s",0,1)], []).violations == ("segment_count_mismatch",)

def test_non_utc_clock_rejected():
    voice = VoiceSpec(provider="supertonic3", voice_id="v", cloned=False)
    with pytest.raises(ValueError, match="UTC"):
        validate_voice_consent(voice, {}, now=datetime(2026, 10, 5), required_scope="x")
