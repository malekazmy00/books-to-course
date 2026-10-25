"""Learner MP3 delivery (T-AUD-06..08): speech encoding, ID3v2.3 tags, streaming resampling.

Encoding: MPEG-1 Layer III, mono, 44.1 kHz, CBR (default 96 kbps). Tags: ID3v2.3 (not 2.4),
text frames in UTF-16 with BOM (encoding byte 0x01, id3.org/id3v2.3.0 §3.3), TIT2/TALB/TRCK
"k/total"/TPE1 and an optional front cover (APIC).
"""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
from mutagen.id3 import ID3
from mutagen.mp3 import MP3, BitrateMode
from scipy.signal import resample_poly

from b2c_speech.audio import PCM
from b2c_speech.audio.loudness import true_peak_dbtp
from b2c_speech.audio.mp3 import Id3Tags, Mp3Settings, encode_mp3, read_id3, resample_blocks, write_id3v23

SR = 24000
PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def speechlike(seconds, sr=SR, amp=0.2, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    x = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t) + 0.1 * rng.standard_normal(len(t))
    x *= 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    return amp * x / np.max(np.abs(x))


def frames(data: bytes) -> dict[str, bytes]:
    """Raw ID3v2.3 frame bodies, parsed by hand (independent of mutagen)."""
    assert data[:3] == b"ID3"
    size = sum((b & 0x7F) << (7 * (3 - i)) for i, b in enumerate(data[6:10]))
    out, pos = {}, 10
    while pos + 10 <= 10 + size and data[pos:pos + 4].strip(b"\x00"):
        fid, flen = data[pos:pos + 4].decode("ascii"), int.from_bytes(data[pos + 4:pos + 8], "big")  # 2.3: plain int
        out[fid] = data[pos + 10:pos + 10 + flen]
        pos += 10 + flen
    return out


# ------------------------------------------------------------------ resampling
@pytest.mark.parametrize("sr_in,sr_out", [(24000, 44100), (22050, 44100), (48000, 44100), (44100, 44100)])
def test_block_resampling_equals_whole_signal_resampling(sr_in, sr_out):
    x = speechlike(7.3, sr=sr_in)
    whole = resample_poly(x, sr_out // math.gcd(sr_in, sr_out), sr_in // math.gcd(sr_in, sr_out)) \
        if sr_in != sr_out else x
    blocks = np.concatenate(list(resample_blocks(x, sr_in, sr_out, block_s=1.0)))
    assert len(blocks) == len(whole)
    assert np.max(np.abs(blocks - whole)) < 1e-9


def test_true_peak_is_streamed_and_matches_the_whole_signal():
    x = speechlike(5.0) * 0.9
    up = resample_poly(x, 8, 1)
    assert true_peak_dbtp(x, SR) == pytest.approx(20 * math.log10(float(np.max(np.abs(up)))), abs=1e-9)


# ------------------------------------------------------------------ encoding
def test_speech_settings_default_to_mono_44k1_96kbps_cbr():
    s = Mp3Settings()
    assert (s.bitrate_kbps, s.sample_rate, s.channels) == (96, 44100, 1)


@pytest.mark.parametrize("kbps", [64, 96])
def test_encoded_file_is_mpeg1_layer3_mono_cbr_at_the_requested_bitrate(kbps):
    data = encode_mp3(PCM(samples=speechlike(3.0), sample_rate=SR), Mp3Settings(bitrate_kbps=kbps))
    info = MP3(io.BytesIO(data)).info
    assert info.sample_rate == 44100 and info.channels == 1 and info.layer == 3 and info.version == 1
    assert info.bitrate == kbps * 1000
    assert info.bitrate_mode in (BitrateMode.CBR, BitrateMode.UNKNOWN)  # LAME's Info tag says CBR
    assert info.length == pytest.approx(3.0, abs=0.1)
    assert len(data) == pytest.approx(kbps * 1000 / 8 * 3.0, rel=0.1)


def test_stereo_input_is_mixed_down_to_mono():
    x = speechlike(1.0)
    data = encode_mp3(PCM(samples=np.stack([x, x]), sample_rate=SR))
    assert MP3(io.BytesIO(data)).info.channels == 1


def test_unsupported_bitrate_or_rate_is_refused():
    with pytest.raises(ValueError):
        Mp3Settings(bitrate_kbps=100)
    with pytest.raises(ValueError):
        Mp3Settings(sample_rate=24000)  # MPEG-2 LSF: not every old player decodes it


# ------------------------------------------------------------------ tags
TAGS = Id3Tags(title="03 - التحليل: الفرضيات", album="سيكولوجية تحليل الاستخبارات", track="4/41",
               artist="ريتشاردز هوير")


def test_tags_are_id3v23_with_utf16_bom_text_frames():
    data = write_id3v23(encode_mp3(PCM(samples=speechlike(1.0), sample_rate=SR)), TAGS)
    assert data[:5] == b"ID3\x03\x00"  # major version 3, revision 0
    raw = frames(data)
    for fid in ("TIT2", "TALB", "TRCK", "TPE1"):
        body = raw[fid]
        assert body[0] == 0x01, fid  # $01 = Unicode (UCS-2/UTF-16) in ID3v2.3
        assert body[1:3] in (b"\xff\xfe", b"\xfe\xff"), fid  # BOM required by id3v2.3.0 §3.3
    title = raw["TIT2"][1:].decode("utf-16").rstrip("\x00")
    assert title == "03 - التحليل: الفرضيات"


def test_tags_round_trip_through_mutagen_and_audio_is_untouched():
    audio = encode_mp3(PCM(samples=speechlike(1.0), sample_rate=SR))
    data = write_id3v23(audio, TAGS)
    assert data.endswith(audio[-1000:])
    tags = ID3(io.BytesIO(data))
    assert tags.version == (2, 3, 0)
    assert str(tags["TIT2"]) == TAGS.title and str(tags["TALB"]) == TAGS.album
    assert str(tags["TRCK"]) == "4/41" and str(tags["TPE1"]) == TAGS.artist
    assert read_id3(data) == {"version": "2.3.0", "TIT2": TAGS.title, "TALB": TAGS.album, "TRCK": "4/41",
                              "TPE1": TAGS.artist, "APIC": None}


def test_optional_front_cover():
    data = write_id3v23(encode_mp3(PCM(samples=speechlike(1.0), sample_rate=SR)),
                        Id3Tags(title="00 - مقدمة", album="ك", track="1/3", cover=PNG_1PX, cover_mime="image/png"))
    apic = ID3(io.BytesIO(data)).getall("APIC")
    assert len(apic) == 1 and apic[0].type == 3 and apic[0].mime == "image/png" and apic[0].data == PNG_1PX
    assert read_id3(data)["APIC"] == {"mime": "image/png", "bytes": len(PNG_1PX)}


def test_retagging_replaces_old_tags_instead_of_stacking_them():
    audio = encode_mp3(PCM(samples=speechlike(1.0), sample_rate=SR))
    once = write_id3v23(audio, TAGS)
    twice = write_id3v23(once, Id3Tags(title="جديد", album="ك", track="1/1"))
    assert twice.count(b"ID3\x03") == 1 and str(ID3(io.BytesIO(twice))["TIT2"]) == "جديد"
    assert "TPE1" not in ID3(io.BytesIO(twice))


@pytest.mark.parametrize("track", ["0/3", "4/3", "4", "a/b", "1/0"])
def test_track_number_must_be_k_of_total(track):
    with pytest.raises(ValueError):
        Id3Tags(title="t", album="a", track=track)


def test_true_peak_helper_is_unchanged_for_silence():
    assert true_peak_dbtp(np.zeros(100), SR) == -math.inf
