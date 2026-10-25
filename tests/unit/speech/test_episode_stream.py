"""Streaming episode assembly, normalization and MP3 encoding (D-044 §2; closes D-042's open item).

``encode_episode_stream`` never holds the episode in memory: pass 1 reads the track WAVs block by
block into streaming meters (BS.1770 integrated loudness, true peak, sample peak, clipping, silence),
pass 2 re-reads them, applies the one gain, resamples (polyphase, same filter as the whole-signal
path) and feeds LAME block by block to a file. The meters and the resampler are exact: they give the
same numbers as the whole-signal functions."""
from __future__ import annotations

import tracemalloc

import numpy as np
import pytest

from b2c_speech.audio import PCM, integrated_loudness, true_peak_dbtp, write_wav
from b2c_speech.audio.mp3 import mp3_frame_probe, read_id3
from b2c_speech.audio.qa import APP_SPEECH, measure
from b2c_speech.audio.resample import resample_blocks
from b2c_speech.audio.stream import (LoudnessMeter, SignalStats, TruePeakMeter, WavReader, encode_episode_stream,
                                     resample_stream)
from b2c_speech.delivery import EpisodeLoudnessError, encode_episode

SR = 24000


def speechlike(seconds, *, amp=0.05, seed=1, lead=0.2, trail=0.3):
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t) + 0.1 * rng.standard_normal(n)
    x *= 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    x = amp * x / np.max(np.abs(x))
    return np.concatenate([np.zeros(int(lead * SR)), x, np.zeros(int(trail * SR))])


def chunks(x, sizes):
    i, k = 0, 0
    while i < len(x):
        n = sizes[k % len(sizes)]
        yield x[i:i + n]
        i, k = i + n, k + 1


@pytest.mark.parametrize("sr_out", [44100, 192000])
@pytest.mark.parametrize("sizes", [[1], [997, 13, 5000], [24000 * 3]])
def test_resample_stream_equals_block_resampling(sr_out, sizes):
    x = np.random.default_rng(3).standard_normal(SR // 2 if sizes == [1] else SR * 4) * 0.1
    whole = np.concatenate(list(resample_blocks(x, SR, sr_out, block_s=0.7)))
    streamed = np.concatenate(list(resample_stream(chunks(x, sizes), SR, sr_out, block_s=0.7)))
    assert streamed.shape == whole.shape
    assert np.max(np.abs(streamed - whole)) < 1e-12


def test_meters_equal_whole_signal_measurements():
    x = np.concatenate([speechlike(3.0, seed=1), np.zeros(int(2.0 * SR)), speechlike(2.5, amp=0.2, seed=2)])
    x[SR * 4:SR * 4 + 5] = 0.9995  # a clipped run
    loud, tp, stats = LoudnessMeter(SR), TruePeakMeter(SR), SignalStats(SR)
    for block in chunks(x, [4801, 333, 12000]):
        loud.feed(block)
        tp.feed(block)
        stats.feed(block)
    assert loud.integrated() == pytest.approx(integrated_loudness(x, SR), abs=1e-9)
    assert tp.dbtp() == pytest.approx(true_peak_dbtp(x, SR), abs=1e-9)
    ref = measure(PCM(samples=x, sample_rate=SR))
    got = stats.result()
    for k in ("peak_dbfs", "clipped_runs", "leading_silence_s", "trailing_silence_s", "longest_internal_silence_s",
              "silent", "duration_s"):
        assert got[k] == pytest.approx(getattr(ref, k), abs=1e-9), k


def test_wav_reader_reads_blocks(tmp_path):
    x = speechlike(1.3)
    path = tmp_path / "a.wav"
    path.write_bytes(write_wav(PCM(samples=x, sample_rate=SR)))
    r = WavReader(str(path))
    assert (r.sample_rate, r.channels, r.frames) == (SR, 1, len(x))
    got = np.concatenate(list(r.blocks(1000)))
    assert np.max(np.abs(got - np.round(np.clip(x, -1, 32767 / 32768) * 32768) / 32768)) < 1e-12


def tracks(tmp_path, seconds=(2.0, 1.5, 2.5)):
    paths = []
    for i, s in enumerate(seconds):
        p = tmp_path / f"t{i}.wav"
        p.write_bytes(write_wav(PCM(samples=speechlike(s, seed=i + 1, amp=0.04 + 0.03 * i), sample_rate=SR)))
        paths.append(str(p))
    return paths


def test_streamed_episode_matches_the_in_memory_one(tmp_path):
    paths = tracks(tmp_path)
    out = tmp_path / "ep.mp3"
    result = encode_episode_stream(paths, str(out), number=3, title="الفصل الثالث", book_title="كتاب",
                                   last_episode=12, artist="مؤلف", gap_ms=1200, block_s=0.5)
    data = out.read_bytes()
    assert result.filename == "03 - الفصل الثالث.mp3"
    assert result.report["passed"] and result.report["streamed"]
    assert result.report["integrated_lufs"] == pytest.approx(-16.0, abs=1.0)
    assert result.report["true_peak_dbtp"] <= -1.0 and result.report["bytes"] == len(data)
    assert len(result.sha256) == 64
    tags = read_id3(data)
    assert tags["version"] == "2.3.0" and tags["TRCK"] == "4/13" and tags["TPE1"] == "مؤلف"
    frames = mp3_frame_probe(data)
    assert frames["bitrates_kbps"] == [96] and frames["channel_modes"] == ["mono"] and frames["trailing_bytes"] == 0
    # same signal in memory (tracks + gaps): same gain and loudness
    from b2c_speech.audio import read_wav
    pieces = []
    for i, p in enumerate(paths):
        pieces.append(read_wav(open(p, "rb").read()).mono())
        if i < len(paths) - 1:
            pieces.append(np.zeros(int(1.2 * SR)))
    mem = encode_episode(PCM(samples=np.concatenate(pieces), sample_rate=SR), number=3, title="الفصل الثالث",
                         book_title="كتاب", last_episode=12)
    assert result.report["gain_db"] == pytest.approx(mem.report["gain_db"], abs=1e-6)
    assert result.report["integrated_lufs"] == pytest.approx(mem.report["integrated_lufs"], abs=1e-3)
    assert result.report["duration_s"] == pytest.approx(mem.report["duration_s"], abs=1e-3)
    assert abs(len(data) - len(mem.data)) < 2000


def test_gap_longer_than_the_internal_silence_limit_is_refused(tmp_path):
    with pytest.raises(ValueError):
        encode_episode_stream(tracks(tmp_path), str(tmp_path / "x.mp3"), number=1, title="t", book_title="b",
                              last_episode=3, gap_ms=APP_SPEECH.max_internal_silence_s * 1000 + 1)


def test_loudness_failure_is_strict(tmp_path):
    p = tmp_path / "spiky.wav"
    x = speechlike(3.0, amp=0.002)
    x[SR] = 0.9  # one spike: the true-peak ceiling stops the gain long before -16 LUFS
    p.write_bytes(write_wav(PCM(samples=x, sample_rate=SR)))
    with pytest.raises(EpisodeLoudnessError):
        encode_episode_stream([str(p)], str(tmp_path / "x.mp3"), number=1, title="t", book_title="b", last_episode=3)
    assert not (tmp_path / "x.mp3").exists()
    loose = encode_episode_stream([str(p)], str(tmp_path / "y.mp3"), number=1, title="t", book_title="b",
                                  last_episode=3, strict=False)
    assert not loose.report["passed"] and "loudness_out_of_range" in loose.report["violations"]


def test_memory_does_not_grow_with_the_episode(tmp_path):
    def peak(seconds):
        paths = tracks(tmp_path, (seconds,))
        tracemalloc.start()
        encode_episode_stream(paths, str(tmp_path / "m.mp3"), number=1, title="t", book_title="b", last_episode=3,
                              block_s=1.0)
        _, top = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return top
    short, long = peak(10.0), peak(60.0)
    # the whole 60 s signal as float64 would be 11.5 MB; the streamed peak stays near the short one
    assert long < 1.5 * short + 1_000_000 and long < 0.5 * 60 * SR * 8
