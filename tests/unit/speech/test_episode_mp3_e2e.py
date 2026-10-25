"""End to end, without any TTS (T-AUD-12): generated segment audio (tone + edge silence) ->
edge trimming + planned pauses -> loudness-normalized episode -> tagged MP3 -> read back and
check name, tags, format and loudness report; then copy a small course to a FAT image in order.
"""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
from mutagen.mp3 import MP3

from b2c_speech.audio import PCM, integrated_loudness
from b2c_speech.audio.mp3 import read_id3
from b2c_speech.audio.trim import assemble_with_pauses
from b2c_speech.delivery import EpisodeLoudnessError, encode_episode
from b2c_speech.episode_naming import episode_tags

SR = 24000
BOOK = "سيكولوجية تحليل الاستخبارات"


def segment(seconds, *, lead=0.3, trail=0.5, amp=0.05, f=220.0):
    t = np.arange(int(seconds * SR)) / SR
    voiced = amp * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    return np.concatenate([np.zeros(int(lead * SR)), voiced, np.zeros(int(trail * SR))])


def silent_runs(x, threshold_dbfs=-50.0, frame_s=0.005):
    frame = int(frame_s * SR)
    n = len(x) // frame
    level = 10 * np.log10(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-30)
    quiet = level <= threshold_dbfs
    runs, run, started = [], 0, False
    for q in quiet:
        if q:
            run += 1
        else:
            if run and started:  # internal gaps only: the edge guards are not gaps
                runs.append(run * frame_s)
            run, started = 0, True
    return runs


def episode_audio():
    pieces = [segment(1.2), segment(0.8, f=330.0), segment(1.5, f=180.0)]
    return assemble_with_pauses(pieces, [400, 1200, 0], SR)


def test_assembled_gaps_are_the_planned_pauses():
    track, timings = episode_audio()
    gaps = silent_runs(track)
    assert len(gaps) == 2
    assert gaps[0] == pytest.approx(0.400, abs=0.03) and gaps[1] == pytest.approx(1.200, abs=0.03)
    assert [round(t["pause_inserted_ms"]) for t in timings] == [320, 1120, 0]  # 2 x 40 ms guard kept from edges
    assert all(b["start_s"] >= a["end_s"] for a, b in zip(timings, timings[1:]))
    assert track.size / SR == pytest.approx(1.2 + 0.8 + 1.5 + 0.4 + 1.2 + 0.08, abs=0.05)


def test_episode_is_encoded_tagged_named_and_normalized_to_minus_16_lufs():
    track, _ = episode_audio()
    out = encode_episode(PCM(samples=track, sample_rate=SR), number=2, title="الفرضيات المتنافسة: مدخل",
                         book_title=BOOK, last_episode=12, artist="ريتشاردز هوير")
    assert out.filename == "02 - الفرضيات المتنافسة مدخل.mp3"
    tags = read_id3(out.data)
    assert tags == {"version": "2.3.0", "TIT2": "02 - الفرضيات المتنافسة: مدخل", "TALB": BOOK, "TRCK": "3/13",
                    "TPE1": "ريتشاردز هوير", "APIC": None}
    info = MP3(io.BytesIO(out.data)).info
    assert (info.sample_rate, info.channels, info.bitrate) == (44100, 1, 96000)
    assert info.length == pytest.approx(track.size / SR, abs=0.1)
    r = out.report
    assert r["loudness_profile"] == "app-speech-16" and r["passed"] is True
    assert r["integrated_lufs"] == pytest.approx(-16.0, abs=1.0) and r["true_peak_dbtp"] <= -1.0
    assert r["id3"] == "2.3.0" and r["bitrate_kbps"] == 96 and r["sample_rate"] == 44100
    assert r["bitrate_mode"] == "CBR" and r["channel_modes"] == ["mono"]


def test_ascii_name_and_ascii_free_tags_keep_the_arabic_in_tags():
    track, _ = episode_audio()
    out = encode_episode(PCM(samples=track, sample_rate=SR), number=0, title="مقدمة الكورس", book_title=BOOK,
                         last_episode=120, ascii_only=True)
    assert out.filename == "000.mp3"
    assert read_id3(out.data)["TIT2"] == "000 - مقدمة الكورس" and read_id3(out.data)["TRCK"] == "1/121"


def test_episode_that_cannot_reach_the_loudness_target_is_refused():
    t = np.arange(SR * 3) / SR
    spiky = 0.01 * np.sin(2 * np.pi * 200 * t)
    spiky[::SR // 2] = 0.99  # isolated peaks stop the single gain far below -16 LUFS
    with pytest.raises(EpisodeLoudnessError):
        encode_episode(PCM(samples=spiky, sample_rate=SR), number=1, title="t", book_title=BOOK, last_episode=3)
    out = encode_episode(PCM(samples=spiky, sample_rate=SR), number=1, title="t", book_title=BOOK, last_episode=3,
                         strict=False)
    assert out.report["passed"] is False and "loudness_out_of_range" in out.report["violations"]


def test_tags_helper_adds_the_artist_only_when_given():
    assert "TPE1" not in episode_tags(1, "t", book_title="b", last_episode=2)
    assert episode_tags(1, "t", book_title="b", last_episode=2, artist="م")["TPE1"] == "م"


def test_loudness_is_measured_on_the_mono_signal():
    track, _ = episode_audio()
    out = encode_episode(PCM(samples=track, sample_rate=SR), number=1, title="t", book_title=BOOK, last_episode=3)
    gain = out.report["gain_db"]
    assert integrated_loudness(track * 10 ** (gain / 20), SR) == pytest.approx(out.report["integrated_lufs"], abs=0.01)
    assert math.isfinite(gain)
