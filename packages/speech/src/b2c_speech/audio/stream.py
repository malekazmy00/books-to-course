"""Streaming episode assembly, loudness normalization and MP3 encoding (D-044 §2; D-042 open item).

``delivery.encode_episode`` takes one PCM array: a one-hour episode measured 2.75 GiB peak (D-042),
more than the audio worker's 2 GiB. Here nothing holds the episode:

* ``WavReader`` reads a track WAV from disk block by block;
* ``LoudnessMeter`` (BS.1770-4 gated integrated loudness: K-weighting state carried across blocks,
  energy summed per 100 ms hop), ``TruePeakMeter`` (x4/x8 polyphase oversampling, the same filter as
  ``loudness.true_peak_dbtp``) and ``SignalStats`` (sample peak, clipping runs, 20 ms silence frames,
  RMS, noise floor) are push-based and give the same numbers as their whole-signal versions;
* ``StreamResampler`` produces exactly the sequence ``resample.resample_blocks`` produces;
* ``encode_episode_stream``: pass 1 reads the tracks (with the planned gap between them) into the
  meters and derives the single gain exactly like ``qa.normalize_loudness``; pass 2 reads them again,
  applies the gain, measures the result (QA verdict on what is encoded), resamples to 44.1 kHz and
  feeds LAME block by block into a file, then prepends the ID3v2.3 tag while hashing.

Memory is bounded by ``block_s`` (plus one 100 ms hop value per 100 ms of audio for the gating, about
0.3 MB per hour), not by the episode length.
"""
from __future__ import annotations

import hashlib
import math
import os
import struct
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter, resample_poly

from .loudness import ABSOLUTE_GATE_LUFS, BLOCK_S, OVERLAP, RELATIVE_GATE_LU, k_weighting
from .qa import (CLIP_LEVEL, CLIP_RUN, FLOOR_DB, FRAME_S, NOISE_WINDOW_S, APP_SPEECH, AudioStats, LoudnessProfile,
                 check_track)

COPY_CHUNK = 1 << 20


# ------------------------------------------------------------------ reading
class WavReader:
    """RIFF/WAVE PCM (16/24/32-bit integer, 32/64-bit float) read from a file in blocks, as mono float64."""

    def __init__(self, path: str) -> None:
        self.path = path
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                raise ValueError(f"{path}: not a RIFF/WAVE file")
            fmt = None
            while True:
                chunk = f.read(8)
                if len(chunk) < 8:
                    raise ValueError(f"{path}: WAVE file without fmt/data chunks")
                cid, size = chunk[:4], struct.unpack("<I", chunk[4:])[0]
                if cid == b"fmt ":
                    fmt = f.read(size)
                    f.seek(size & 1, 1)
                elif cid == b"data":
                    if fmt is None or len(fmt) < 16:
                        raise ValueError(f"{path}: data chunk before fmt")
                    self.data_offset, self.data_size = f.tell(), size
                    break
                else:
                    f.seek(size + (size & 1), 1)
        tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
        if tag == 0xFFFE and len(fmt) >= 26:
            tag = struct.unpack("<H", fmt[24:26])[0]
        if channels < 1 or rate < 1 or align != channels * bits // 8:
            raise ValueError(f"{path}: inconsistent WAVE header")
        if (tag, bits) not in ((1, 16), (1, 24), (1, 32), (3, 32), (3, 64)):
            raise ValueError(f"{path}: unsupported WAVE encoding (format {tag}, {bits} bits)")
        actual = os.path.getsize(path) - self.data_offset
        self.data_size = min(self.data_size, actual) // align * align
        self.tag, self.channels, self.sample_rate, self.align, self.bits = tag, channels, rate, align, bits
        self.frames = self.data_size // align

    def _decode(self, raw: bytes) -> np.ndarray:
        if self.tag == 1 and self.bits == 16:
            x = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0
        elif self.tag == 1 and self.bits == 24:
            b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
            v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            x = np.where(v & 0x800000, v - 0x1000000, v).astype(np.float64) / 8388608.0
        elif self.tag == 1:
            x = np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648.0
        else:
            x = np.frombuffer(raw, "<f4" if self.bits == 32 else "<f8").astype(np.float64)
        return x if self.channels == 1 else x.reshape(-1, self.channels).mean(axis=1)

    def blocks(self, frames_per_block: int) -> Iterator[np.ndarray]:
        step = max(1, int(frames_per_block)) * self.align
        with open(self.path, "rb") as f:
            f.seek(self.data_offset)
            left = self.data_size
            while left > 0:
                raw = f.read(min(step, left))
                if not raw:
                    break
                left -= len(raw)
                yield self._decode(raw[:len(raw) // self.align * self.align])


# ------------------------------------------------------------------ resampling
class StreamResampler:
    """Push-based twin of ``resample.resample_blocks``: same blocks, same margins, same output."""

    def __init__(self, sr_in: int, sr_out: int, *, block_s: float = 30.0) -> None:
        if sr_in <= 0 or sr_out <= 0:
            raise ValueError("sample rates must be positive")
        g = math.gcd(sr_in, sr_out)
        self.up, self.down = sr_out // g, sr_in // g
        self.block = max(self.down, int(block_s * sr_in) // self.down * self.down)
        need = math.ceil(10 * max(self.up, self.down) / self.up) + 2
        self.margin = self.down * math.ceil(need / self.down)
        self.buf = np.zeros(0)
        self.buf_start = 0     # global index of buf[0]
        self.seen = 0          # samples pushed
        self.a = 0             # start of the next block to emit
        self.produced = 0

    def push(self, x: np.ndarray) -> list[np.ndarray]:
        x = np.asarray(x, dtype=np.float64)
        if x.size == 0:
            return []
        if self.up == self.down:
            self.seen += x.size
            return [x]
        self.buf = np.concatenate([self.buf, x])
        self.seen += x.size
        out = []
        while self.seen >= self.a + self.block + self.margin:
            out.append(self._emit(last=False))
        return out

    def finish(self) -> list[np.ndarray]:
        if self.up == self.down:
            return []
        out = []
        n = self.seen
        while self.a < n:
            out.append(self._emit(last=self.a + self.block >= n))
        return out

    def _emit(self, *, last: bool) -> np.ndarray:
        a, n = self.a, self.seen
        lo, hi = max(0, a - self.margin), min(n, a + self.block + self.margin)
        y = resample_poly(self.buf[lo - self.buf_start:hi - self.buf_start], self.up, self.down)
        first = (a - lo) * self.up // self.down
        if last:
            total = -(-n * self.up // self.down)
            stop = total - self.produced + first
        else:
            stop = first + self.block * self.up // self.down
        block = y[first:stop]
        self.produced += block.shape[-1]
        self.a = a + self.block
        keep = max(0, self.a - self.margin)
        if keep > self.buf_start:
            self.buf = self.buf[keep - self.buf_start:]
            self.buf_start = keep
        return block


def resample_stream(blocks: Iterable[np.ndarray], sr_in: int, sr_out: int, *, block_s: float = 30.0
                    ) -> Iterator[np.ndarray]:
    r = StreamResampler(sr_in, sr_out, block_s=block_s)
    for x in blocks:
        yield from r.push(x)
    yield from r.finish()


# ------------------------------------------------------------------ meters
class LoudnessMeter:
    """BS.1770-4 integrated loudness, streamed (same hops/gating as ``loudness.block_loudness``)."""

    def __init__(self, sample_rate: int) -> None:
        self.sr = sample_rate
        self.block = int(round(BLOCK_S * sample_rate))
        self.step = int(round(self.block * (1 - OVERLAP)))
        if self.block != 4 * self.step:
            raise ValueError(f"streamed loudness needs a 400 ms block of four whole hops ({sample_rate} Hz)")
        self.b, self.a = k_weighting(sample_rate)
        self.state = np.zeros(max(len(self.a), len(self.b)) - 1)
        self.carry = np.zeros(0)
        self.hops: list[np.ndarray] = []
        self.n = 0

    def feed(self, x: np.ndarray) -> None:
        if x.size == 0:
            return
        weighted, self.state = lfilter(self.b, self.a, x, zi=self.state)
        power = np.concatenate([self.carry, weighted ** 2])
        whole = len(power) // self.step * self.step
        if whole:
            self.hops.append(power[:whole].reshape(-1, self.step).sum(axis=1))
        self.carry = power[whole:]
        self.n += x.size

    def block_loudness(self) -> np.ndarray:
        if self.n < self.block or not self.hops:
            return np.array([])
        sums = np.concatenate(self.hops)
        energies = (sums[:-3] + sums[1:-2] + sums[2:-1] + sums[3:]) / self.block
        with np.errstate(divide="ignore"):
            return -0.691 + 10 * np.log10(energies)

    def integrated(self) -> float:
        blocks = self.block_loudness()
        above = blocks[blocks > ABSOLUTE_GATE_LUFS]
        if above.size == 0:
            return -math.inf
        energy = lambda values: float(np.mean(10 ** ((values + 0.691) / 10)))  # noqa: E731
        relative = -0.691 + 10 * math.log10(energy(above)) + RELATIVE_GATE_LU
        gated = above[above > relative]
        if gated.size == 0:
            return -math.inf
        return -0.691 + 10 * math.log10(energy(gated))


class TruePeakMeter:
    """True peak (dBTP) by polyphase oversampling to >= 192 kHz, streamed."""

    def __init__(self, sample_rate: int, *, block_s: float = 10.0) -> None:
        self.factor = max(1, math.ceil(192000 / sample_rate))
        self.resampler = StreamResampler(sample_rate, sample_rate * self.factor, block_s=block_s) \
            if self.factor > 1 else None
        self.sample_peak = 0.0
        self.over_peak = 0.0
        self.n = 0

    def _take(self, blocks: list[np.ndarray]) -> None:
        for y in blocks:
            if y.size:
                self.over_peak = max(self.over_peak, float(np.max(np.abs(y))))

    def feed(self, x: np.ndarray) -> None:
        if x.size == 0:
            return
        self.n += x.size
        self.sample_peak = max(self.sample_peak, float(np.max(np.abs(x))))
        if self.resampler is not None:
            self._take(self.resampler.push(x))

    def dbtp(self) -> float:
        if self.resampler is not None:
            self._take(self.resampler.finish())
            self.resampler = None
        peak = max(self.sample_peak, self.over_peak if self.n > 1 else 0.0)
        return 20 * math.log10(peak) if peak > 0 else -math.inf


@dataclass
class SignalStats:
    """``qa.measure`` streamed (mono): peak, RMS, clipping runs, silence frames, noise floor."""
    sample_rate: int
    silence_threshold_dbfs: float = -50.0
    n: int = 0
    peak: float = 0.0
    sum_sq: float = 0.0
    clipped: int = 0
    runs: int = 0
    hot_run: int = 0
    frame_carry: np.ndarray = field(default_factory=lambda: np.zeros(0))
    frames: int = 0
    first_loud: int | None = None
    last_loud: int | None = None
    quiet_run: int = 0
    longest_quiet: int = 0
    noise_carry: np.ndarray = field(default_factory=lambda: np.zeros(0))
    noise_min_rms: float | None = None

    def feed(self, x: np.ndarray) -> None:
        if x.size == 0:
            return
        self.n += x.size
        self.peak = max(self.peak, float(x.max()), -float(x.min()))
        self.sum_sq += float(np.dot(x, x))
        hot = (x >= CLIP_LEVEL) | (x <= -CLIP_LEVEL)
        self.clipped += int(hot.sum())
        # a run is counted when it closes; the run touching the block end stays open (``hot_run``)
        if hot.any():
            edges = np.diff(np.concatenate([[0], hot.astype(np.int8), [0]]))
            starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            lengths = [int(n) for n in ends - starts]
            if starts[0] == 0:
                lengths[0] += self.hot_run           # continues the open run of the previous block
            elif self.hot_run >= CLIP_RUN:
                self.runs += 1                        # the open run closed at the block start
            open_last = ends[-1] == x.size
            self.runs += sum(1 for n in (lengths[:-1] if open_last else lengths) if n >= CLIP_RUN)
            self.hot_run = lengths[-1] if open_last else 0
        else:
            if self.hot_run >= CLIP_RUN:
                self.runs += 1
            self.hot_run = 0
        self._frames(x)
        self._noise(x)

    def _frames(self, x: np.ndarray) -> None:
        frame = max(1, int(FRAME_S * self.sample_rate))
        data = np.concatenate([self.frame_carry, x])
        whole = len(data) // frame * frame
        self.frame_carry = data[whole:]
        if not whole:
            return
        f = data[:whole].reshape(-1, frame)
        with np.errstate(divide="ignore"):
            level = 10 * np.log10(np.einsum("ij,ij->i", f, f) / frame + 1e-30)
        for loud in level > self.silence_threshold_dbfs:
            if loud:
                if self.first_loud is None:
                    self.first_loud = self.frames
                elif self.quiet_run:
                    self.longest_quiet = max(self.longest_quiet, self.quiet_run)
                self.last_loud = self.frames
                self.quiet_run = 0
            elif self.first_loud is not None:
                self.quiet_run += 1
            self.frames += 1

    def _noise(self, x: np.ndarray) -> None:
        window = max(1, int(NOISE_WINDOW_S * self.sample_rate))
        data = np.concatenate([self.noise_carry, x])
        whole = len(data) // window * window
        self.noise_carry = data[whole:]
        if whole:
            w = data[:whole].reshape(-1, window)
            rms = float(np.sqrt(np.einsum("ij,ij->i", w, w) / window).min())
            self.noise_min_rms = rms if self.noise_min_rms is None else min(self.noise_min_rms, rms)

    def result(self) -> dict[str, object]:
        db = lambda v: 20 * math.log10(v) if v > 0 else -math.inf  # noqa: E731
        if self.frames == 0:
            leading = trailing = internal = 0.0
            silent = True
        elif self.first_loud is None:
            leading = trailing = self.frames * FRAME_S
            internal, silent = 0.0, True
        else:
            leading = self.first_loud * FRAME_S
            trailing = (self.frames - 1 - (self.last_loud or 0)) * FRAME_S
            internal, silent = self.longest_quiet * FRAME_S, False
        return {"duration_s": self.n / self.sample_rate, "peak_dbfs": db(self.peak),
                "rms_dbfs": db(math.sqrt(self.sum_sq / self.n)) if self.n else -math.inf,
                "noise_floor_dbfs": FLOOR_DB if self.noise_min_rms is None else max(FLOOR_DB, db(self.noise_min_rms)),
                "clipped_samples": self.clipped, "clipped_runs": self.runs + int(self.hot_run >= CLIP_RUN),
                "leading_silence_s": leading,
                "trailing_silence_s": trailing, "longest_internal_silence_s": internal, "silent": silent}


# ------------------------------------------------------------------ the episode
@dataclass(frozen=True)
class StreamedEpisode:
    filename: str
    path: str
    sha256: str
    report: dict[str, object]


def _episode_blocks(readers: Sequence[WavReader], frames: int, gap: int) -> Iterator[np.ndarray]:
    for i, r in enumerate(readers):
        yield from r.blocks(frames)
        if i < len(readers) - 1:
            left = gap
            while left > 0:
                n = min(left, frames)
                yield np.zeros(n)
                left -= n


def _gain(lufs: float, tp: float, profile: LoudnessProfile, margin_db: float) -> float:
    """``qa.normalize_loudness`` (LUFS profiles): one gain to the target, never over the peak ceiling."""
    if profile.integrated_lufs is None:
        raise ValueError("streamed encoding supports integrated-loudness profiles (app-speech-16)")
    if not math.isfinite(lufs):
        return 0.0
    gain = profile.integrated_lufs - lufs
    ceiling = profile.max_true_peak_dbtp
    if ceiling is not None and math.isfinite(tp) and tp + gain > ceiling - margin_db:
        gain = ceiling - margin_db - tp
    return gain


def mp3_frame_probe_file(path: str, *, offset: int = 0) -> dict[str, object]:
    """``mp3.mp3_frame_probe`` over a file, header by header (never the whole file in memory)."""
    from .mp3 import _L3_KBPS, _LAYERS, _MODES, _RATES, _VERSIONS
    seen: dict[str, set] = {k: set() for k in ("bitrates_kbps", "sample_rates", "channel_modes", "versions", "layers")}
    frames = 0
    size_total = os.path.getsize(path)
    pos = offset
    with open(path, "rb") as f:
        while pos + 4 <= size_total:
            f.seek(pos)
            h = int.from_bytes(f.read(4), "big")
            if h >> 21 != 0x7FF:
                break
            version, layer = (h >> 19) & 3, _LAYERS.get((h >> 17) & 3)
            br_idx, sr_idx, padding, mode = (h >> 12) & 15, (h >> 10) & 3, (h >> 9) & 1, (h >> 6) & 3
            if version == 1 or layer != 3 or br_idx in (0, 15) or sr_idx == 3:
                break
            kbps, rate = _L3_KBPS[3 if version == 3 else 2][br_idx], _RATES[version][sr_idx]
            size = (144 if version == 3 else 72) * kbps * 1000 // rate + padding
            if size < 4 or pos + size > size_total:
                break
            for name, value in (("bitrates_kbps", kbps), ("sample_rates", rate), ("channel_modes", _MODES[mode]),
                                ("versions", _VERSIONS[version]), ("layers", layer)):
                seen[name].add(value)
            frames += 1
            pos += size
    if frames == 0:
        raise ValueError("no MPEG Layer III frames found")
    return {"frames": frames, "trailing_bytes": size_total - pos, **{k: sorted(v) for k, v in seen.items()}}


def encode_episode_stream(paths: Sequence[str], out_path: str, *, number: int, title: str, book_title: str,
                          last_episode: int, artist: str | None = None, cover: bytes | None = None,
                          cover_mime: str = "image/jpeg", ascii_only: bool = False,
                          profile: LoudnessProfile = APP_SPEECH, settings=None, strict: bool = True,
                          gap_ms: float = 1200, block_s: float = 10.0) -> StreamedEpisode:
    """Tracks (WAV files, in order, ``gap_ms`` of silence between them) -> one tagged MP3 at ``out_path``."""
    import lameenc

    from ..delivery import ENCODER_PEAK_MARGIN_DB, EpisodeLoudnessError
    from ..episode_naming import episode_filename, episode_tags
    from .mp3 import ENCODER, Id3Tags, Mp3Settings, _int16, write_id3v23
    settings = settings or Mp3Settings()
    if not paths:
        raise ValueError("an episode has at least one track")
    if gap_ms < 0 or gap_ms > profile.max_internal_silence_s * 1000:
        raise ValueError(f"the gap between tracks must be 0..{profile.max_internal_silence_s * 1000:.0f} ms "
                         "(longer is an internal silence the loudness profile refuses)")
    readers = [WavReader(p) for p in paths]
    rate = readers[0].sample_rate
    if any(r.sample_rate != rate for r in readers):
        raise ValueError("tracks of one episode have different sample rates")
    frames = max(1, int(block_s * rate))
    gap = int(round(gap_ms * rate / 1000))
    # pass 1: measure
    loud, peak = LoudnessMeter(rate), TruePeakMeter(rate, block_s=block_s)
    for x in _episode_blocks(readers, frames, gap):
        loud.feed(x)
        peak.feed(x)
    lufs_in, tp_in = loud.integrated(), peak.dbtp()
    gain_db = _gain(lufs_in, tp_in, profile, ENCODER_PEAK_MARGIN_DB)
    g = 10 ** (gain_db / 20)
    # pass 2: gain -> measure what is encoded -> resample -> LAME -> file
    part = out_path + ".part"
    loud2, peak2 = LoudnessMeter(rate), TruePeakMeter(rate, block_s=block_s)
    stats = SignalStats(rate, silence_threshold_dbfs=profile.silence_threshold_dbfs)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(settings.bitrate_kbps)
    encoder.set_in_sample_rate(settings.sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(settings.quality)
    resampler = StreamResampler(rate, settings.sample_rate, block_s=block_s)
    try:
        with open(part, "wb") as f:
            for x in _episode_blocks(readers, frames, gap):
                y = x * g
                loud2.feed(y)
                peak2.feed(y)
                stats.feed(y)
                for z in resampler.push(y):
                    if z.size:
                        f.write(encoder.encode(_int16(z)))
            for z in resampler.finish():
                if z.size:
                    f.write(encoder.encode(_int16(z)))
            f.write(encoder.flush())
        s = stats.result()
        audio_stats = AudioStats(duration_s=float(s["duration_s"]), sample_rate=rate, channels=1,
                                 integrated_lufs=loud2.integrated(), true_peak_dbtp=peak2.dbtp(),
                                 peak_dbfs=float(s["peak_dbfs"]), rms_dbfs=float(s["rms_dbfs"]),
                                 noise_floor_dbfs=float(s["noise_floor_dbfs"]),
                                 clipped_samples=int(s["clipped_samples"]), clipped_runs=int(s["clipped_runs"]),
                                 leading_silence_s=float(s["leading_silence_s"]),
                                 trailing_silence_s=float(s["trailing_silence_s"]),
                                 longest_internal_silence_s=float(s["longest_internal_silence_s"]),
                                 silent=bool(s["silent"]))
        qa = check_track(None, profile, audio_stats)  # type: ignore[arg-type]
        if strict and not qa.passed:
            raise EpisodeLoudnessError(f"episode {number} fails {profile.name}: {', '.join(qa.violations)}")
        raw = episode_tags(number, title, book_title=book_title, last_episode=last_episode, artist=artist)
        tag = write_id3v23(b"", Id3Tags(title=raw["TIT2"], album=raw["TALB"], track=raw["TRCK"],
                                        artist=raw.get("TPE1"), cover=cover, cover_mime=cover_mime))
        frames_info = mp3_frame_probe_file(part)
        digest = hashlib.sha256()
        size = 0
        with open(out_path, "wb") as out, open(part, "rb") as src:
            for chunk in (tag,):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            while chunk := src.read(COPY_CHUNK):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    finally:
        if os.path.exists(part):
            os.remove(part)
    report: dict[str, object] = {
        "episode": number, "loudness_profile": profile.name, "passed": qa.passed, "violations": list(qa.violations),
        "integrated_lufs": qa.metrics.get("integrated_lufs"), "true_peak_dbtp": qa.metrics.get("true_peak_dbtp"),
        "input_integrated_lufs": None if not math.isfinite(lufs_in) else round(lufs_in, 3),
        "gain_db": round(gain_db, 3), "duration_s": round(audio_stats.duration_s, 3), "measured_at": "pre-encode",
        "encoder": ENCODER, "bitrate_kbps": settings.bitrate_kbps,
        "bitrate_mode": "CBR" if frames_info["bitrates_kbps"] == [settings.bitrate_kbps] else "mixed",
        "frames": frames_info["frames"], "channel_modes": frames_info["channel_modes"],
        "mpeg_versions": frames_info["versions"], "trailing_bytes": frames_info["trailing_bytes"],
        "sample_rate": settings.sample_rate, "channels": 1, "id3": "2.3.0", "id3_text_encoding": "UTF-16 (BOM)",
        "bytes": size, "tracks": len(paths), "gap_ms": gap_ms, "streamed": True, "block_s": block_s}
    return StreamedEpisode(filename=episode_filename(number, title, last_episode=last_episode, ascii_only=ascii_only),
                           path=out_path, sha256=digest.hexdigest(), report=report)


__all__ = ["LoudnessMeter", "SignalStats", "StreamResampler", "StreamedEpisode", "TruePeakMeter", "WavReader",
           "encode_episode_stream", "mp3_frame_probe_file", "resample_stream"]
