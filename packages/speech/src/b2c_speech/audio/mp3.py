"""MP3 encoding and ID3v2.3 tagging for learner delivery (D-042).

Encoding (LAME through ``lameenc``, pinned in packages/speech/pyproject.toml):
* MPEG-1 Layer III only (32/44.1/48 kHz). 22.05/24 kHz would be MPEG-2 LSF, which not every
  old player decodes, so the 24 kHz TTS output is resampled to 44.1 kHz (polyphase, scipy).
* mono, constant bitrate. Default 96 kbps: the low end of Apple's recommended range for mono
  MP3 (96-128 kbps, podcasters.apple.com/support/audio-requirements, read 2026-10-25); 64 kbps
  is allowed (inside Apple's RSS range, 64-128 kbps mono) when card space matters.
* streamed in blocks: a one-hour episode never exists at 44.1 kHz as one array.

Tags (``mutagen``, pinned): ID3v2.3, not 2.4 (hardware players often do not read 2.4; Philips:
"ID3 version 2 tags with unsynchronization scheme or UTF8 encoding will not be supported").
Every text frame is written with encoding $01 (Unicode) and a BOM, as id3.org/id3v2.3.0 §3.3
requires, so Arabic titles survive on players that only know v2.3/UCS-2.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

import numpy as np

from .resample import resample_blocks
from .wav import PCM

MPEG1_RATES = (32000, 44100, 48000)
MPEG1_BITRATES_KBPS = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
ENCODER = "lame (lameenc 1.8.4)"
_TRACK = re.compile(r"^(\d+)/(\d+)$")


@dataclass(frozen=True)
class Mp3Settings:
    bitrate_kbps: int = 96
    sample_rate: int = 44100
    channels: int = 1
    quality: int = 2  # LAME -q: 2 = high quality (0 best/slowest, 9 worst)
    block_s: float = 30.0  # streaming block for resampling + encoding

    def __post_init__(self) -> None:
        if self.bitrate_kbps not in MPEG1_BITRATES_KBPS:
            raise ValueError(f"bitrate must be an MPEG-1 Layer III bitrate: {MPEG1_BITRATES_KBPS}")
        if self.sample_rate not in MPEG1_RATES:
            raise ValueError(f"sample rate must be an MPEG-1 rate {MPEG1_RATES} (MPEG-2 LSF is not universally decoded)")
        if self.channels != 1:
            raise ValueError("speech delivery is mono")
        if not 0 <= self.quality <= 9 or self.block_s <= 0:
            raise ValueError("quality must be 0..9 and block_s positive")


def _int16(x: np.ndarray) -> bytes:
    return np.round(np.clip(x, -1.0, 32767 / 32768) * 32768.0).astype("<i2").tobytes()


def encode_mp3(audio: PCM, settings: Mp3Settings = Mp3Settings()) -> bytes:
    """Mono CBR MPEG-1 Layer III. The caller normalizes loudness first (``delivery.encode_episode``)."""
    import lameenc
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(settings.bitrate_kbps)
    encoder.set_in_sample_rate(settings.sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(settings.quality)
    out = bytearray()
    for block in resample_blocks(audio.mono(), audio.sample_rate, settings.sample_rate, block_s=settings.block_s):
        if block.size:
            out += encoder.encode(_int16(block))
    out += encoder.flush()
    return bytes(out)


_VERSIONS = {3: "MPEG-1", 2: "MPEG-2", 0: "MPEG-2.5"}
_LAYERS = {1: 3, 2: 2, 3: 1}
_MODES = {0: "stereo", 1: "joint_stereo", 2: "dual_channel", 3: "mono"}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_L3_KBPS = {3: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
            2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}


def mp3_frame_probe(data: bytes) -> dict[str, object]:
    """Walk every Layer III frame header after the ID3v2 tag: what a player's decoder will see.

    Proves CBR/mono/rate on the frames themselves (lameenc writes no Info/Xing frame, so tag
    based readers report the bitrate mode as unknown)."""
    body = _strip_tags(data)
    pos, frames = 0, 0
    seen: dict[str, set] = {"bitrates_kbps": set(), "sample_rates": set(), "channel_modes": set(), "versions": set(),
                            "layers": set()}
    while pos + 4 <= len(body):
        h = int.from_bytes(body[pos:pos + 4], "big")
        if h >> 21 != 0x7FF:
            break
        version, layer = (h >> 19) & 3, _LAYERS.get((h >> 17) & 3)
        br_idx, sr_idx, padding, mode = (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1, (h >> 6) & 3
        if version == 1 or layer != 3 or br_idx in (0, 15) or sr_idx == 3:
            break
        kbps, rate = _L3_KBPS[3 if version == 3 else 2][br_idx], _RATES[version][sr_idx]
        size = (144 if version == 3 else 72) * kbps * 1000 // rate + padding
        if size < 4 or pos + size > len(body):
            break
        for name, value in (("bitrates_kbps", kbps), ("sample_rates", rate), ("channel_modes", _MODES[mode]),
                            ("versions", _VERSIONS[version]), ("layers", layer)):
            seen[name].add(value)
        frames += 1
        pos += size
    if frames == 0:
        raise ValueError("no MPEG Layer III frames found")
    return {"frames": frames, "trailing_bytes": len(body) - pos, **{k: sorted(v) for k, v in seen.items()}}


@dataclass(frozen=True)
class Id3Tags:
    title: str  # TIT2
    album: str  # TALB (book title)
    track: str  # TRCK "k/total", 1-based
    artist: str | None = None  # TPE1
    cover: bytes | None = None  # APIC, type 3 (front cover)
    cover_mime: str = "image/jpeg"

    def __post_init__(self) -> None:
        m = _TRACK.match(self.track)
        if not m or not 1 <= int(m.group(1)) <= int(m.group(2)):
            raise ValueError("TRCK must be 'k/total' with 1 <= k <= total")
        if self.cover is not None and self.cover_mime not in ("image/jpeg", "image/png"):
            raise ValueError("cover must be image/jpeg or image/png")


def _strip_tags(data: bytes) -> bytes:
    while data[:3] == b"ID3" and len(data) >= 10:
        size = sum((b & 0x7F) << (7 * (3 - i)) for i, b in enumerate(data[6:10]))
        data = data[10 + size + (10 if data[5] & 0x10 else 0):]
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        data = data[:-128]
    return data


def write_id3v23(mp3: bytes, tags: Id3Tags) -> bytes:
    """Replace any ID3 tag of ``mp3`` with one ID3v2.3 tag (UTF-16 + BOM text frames)."""
    from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1, TRCK, Encoding
    utf16 = Encoding.UTF16
    tag = ID3()
    tag.add(TIT2(encoding=utf16, text=[tags.title]))
    tag.add(TALB(encoding=utf16, text=[tags.album]))
    tag.add(TRCK(encoding=utf16, text=[tags.track]))
    if tags.artist:
        tag.add(TPE1(encoding=utf16, text=[tags.artist]))
    if tags.cover is not None:
        tag.add(APIC(encoding=utf16, mime=tags.cover_mime, type=3, desc="", data=tags.cover))
    buffer = io.BytesIO(_strip_tags(mp3))
    tag.save(buffer, v2_version=3, padding=lambda info: 0)
    return buffer.getvalue()


def read_id3(data: bytes) -> dict[str, object]:
    """The delivery tags as a player would read them (for verification and reports)."""
    from mutagen.id3 import ID3
    tag = ID3(io.BytesIO(data))
    text = {fid: (str(tag[fid]) if fid in tag else None) for fid in ("TIT2", "TALB", "TRCK", "TPE1")}
    apic = tag.getall("APIC")
    return {"version": ".".join(str(v) for v in tag.version), **text,
            "APIC": {"mime": apic[0].mime, "bytes": len(apic[0].data)} if apic else None}


__all__ = ["ENCODER", "Id3Tags", "MPEG1_BITRATES_KBPS", "MPEG1_RATES", "Mp3Settings", "encode_mp3", "mp3_frame_probe",
           "read_id3", "resample_blocks", "write_id3v23"]
