"""One learner episode -> one tagged MP3 file (D-038 naming, D-042 encoding).

Order of operations (Apple: loudness "preconditioning steps need to occur before the encoding
process"): mono mixdown -> one linear gain to the loudness profile (default ``app-speech-16``:
-16 LUFS +-1 LU integrated, true peak <= -1 dBTP; podcasters.apple.com/support/audio-requirements)
with a 0.5 dB extra true-peak margin for the encoder's overshoot -> track QA -> MP3 (mono CBR
MPEG-1 Layer III) -> ID3v2.3 tags (TIT2, TALB, TRCK "k/total", TPE1, optional cover).

Loudness is measured with BS.1770 on the mono signal (channel weight 1). Some guides target
-19 LUFS for mono files that are played on two ears (podnews.net/article/lufs-lkfs-for-podcasters);
the profile stays the owner's choice (Q2), the report names the profile used.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .audio.mp3 import ENCODER, Id3Tags, Mp3Settings, encode_mp3, mp3_frame_probe, write_id3v23
from .audio.qa import APP_SPEECH, LoudnessProfile, check_track, normalize_loudness
from .audio.wav import PCM
from .episode_naming import episode_filename, episode_tags

ENCODER_PEAK_MARGIN_DB = 0.5


class EpisodeLoudnessError(ValueError):
    """The episode does not meet its loudness profile after normalization (fail closed)."""


@dataclass(frozen=True)
class EpisodeFile:
    filename: str
    data: bytes
    report: dict[str, object] = field(default_factory=dict)


def encode_episode(audio: PCM, *, number: int, title: str, book_title: str, last_episode: int,
                   artist: str | None = None, cover: bytes | None = None, cover_mime: str = "image/jpeg",
                   ascii_only: bool = False, profile: LoudnessProfile = APP_SPEECH,
                   settings: Mp3Settings = Mp3Settings(), strict: bool = True) -> EpisodeFile:
    mono = PCM(samples=audio.mono(), sample_rate=audio.sample_rate)
    normalized, gain_db = normalize_loudness(mono, profile, margin_db=ENCODER_PEAK_MARGIN_DB)
    qa = check_track(normalized, profile)
    if strict and not qa.passed:
        raise EpisodeLoudnessError(f"episode {number} fails {profile.name}: {', '.join(qa.violations)}")
    raw = episode_tags(number, title, book_title=book_title, last_episode=last_episode, artist=artist)
    tags = Id3Tags(title=raw["TIT2"], album=raw["TALB"], track=raw["TRCK"], artist=raw.get("TPE1"), cover=cover,
                   cover_mime=cover_mime)
    data = write_id3v23(encode_mp3(normalized, settings), tags)
    frames = mp3_frame_probe(data)
    report: dict[str, object] = {
        "episode": number, "loudness_profile": profile.name, "passed": qa.passed, "violations": list(qa.violations),
        "integrated_lufs": qa.metrics.get("integrated_lufs"), "true_peak_dbtp": qa.metrics.get("true_peak_dbtp"),
        "gain_db": round(gain_db, 3), "duration_s": round(normalized.duration_s, 3), "measured_at": "pre-encode",
        "encoder": ENCODER, "bitrate_kbps": settings.bitrate_kbps,
        "bitrate_mode": "CBR" if frames["bitrates_kbps"] == [settings.bitrate_kbps] else "mixed",
        "frames": frames["frames"], "channel_modes": frames["channel_modes"],
        "sample_rate": settings.sample_rate, "channels": 1, "id3": "2.3.0", "id3_text_encoding": "UTF-16 (BOM)",
        "bytes": len(data)}
    return EpisodeFile(filename=episode_filename(number, title, last_episode=last_episode, ascii_only=ascii_only),
                       data=data, report=report)


__all__ = ["ENCODER_PEAK_MARGIN_DB", "EpisodeFile", "EpisodeLoudnessError", "encode_episode"]
