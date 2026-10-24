"""L8 speech validation primitives; model and media adapters remain explicit."""
from .validation import (
    AudioCheck,
    AudioMeasurements,
    TTSProvider,
    check_audio_measurements,
    check_segment_alignment,
    validate_audio_asset,
    validate_provider_result,
    validate_voice_consent,
)

__all__ = [
    "AudioCheck", "AudioMeasurements", "TTSProvider",
    "check_audio_measurements", "check_segment_alignment",
    "validate_audio_asset", "validate_provider_result", "validate_voice_consent",
]
