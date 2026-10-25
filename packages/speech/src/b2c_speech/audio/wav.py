"""Minimal RIFF/WAVE reader and writer (PCM 16/24/32-bit integer and 32/64-bit float).

The TTS providers return WAV; QA and assembly work on float samples in [-1, 1]. Multi-channel
input keeps its channels for loudness (BS.1770 sums channel energies); mono is the delivery form.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class PCM:
    samples: np.ndarray  # shape (n,) mono or (channels, n); float64 in [-1, 1]
    sample_rate: int
    sample_width: int = 2  # bytes per sample of the source encoding
    encoding: str = field(default="pcm_s16le")

    @property
    def channels(self) -> int:
        return 1 if self.samples.ndim == 1 else int(self.samples.shape[0])

    @property
    def frames(self) -> int:
        return int(self.samples.shape[-1])

    @property
    def duration_s(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def mono(self) -> np.ndarray:
        return self.samples if self.samples.ndim == 1 else self.samples.mean(axis=0)


def read_wav(data: bytes) -> PCM:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    offset, fmt, payload = 12, None, None
    while offset + 8 <= len(data):
        chunk, size = data[offset:offset + 4], struct.unpack("<I", data[offset + 4:offset + 8])[0]
        body = data[offset + 8:offset + 8 + size]
        if chunk == b"fmt ":
            fmt = body
        elif chunk == b"data":
            payload = body
        offset += 8 + size + (size & 1)
    if fmt is None or payload is None or len(fmt) < 16:
        raise ValueError("WAVE file without fmt/data chunks")
    tag, channels, rate, _, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:  # WAVE_FORMAT_EXTENSIBLE: the subformat holds the tag
        tag = struct.unpack("<H", fmt[24:26])[0]
    if channels < 1 or rate < 1 or align != channels * bits // 8:
        raise ValueError("inconsistent WAVE header")
    usable = len(payload) - len(payload) % align
    raw = payload[:usable]
    if tag == 1 and bits == 16:
        samples, encoding = np.frombuffer(raw, "<i2").astype(np.float64) / 32768.0, "pcm_s16le"
    elif tag == 1 and bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        values = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        samples, encoding = values.astype(np.float64) / 8388608.0, "pcm_s24le"
    elif tag == 1 and bits == 32:
        samples, encoding = np.frombuffer(raw, "<i4").astype(np.float64) / 2147483648.0, "pcm_s32le"
    elif tag == 3 and bits in (32, 64):
        samples, encoding = np.frombuffer(raw, "<f4" if bits == 32 else "<f8").astype(np.float64), f"pcm_f{bits}le"
    else:
        raise ValueError(f"unsupported WAVE encoding (format {tag}, {bits} bits)")
    if channels > 1:
        samples = samples.reshape(-1, channels).T
    return PCM(samples=samples, sample_rate=rate, sample_width=bits // 8, encoding=encoding)


def write_wav(pcm: PCM) -> bytes:
    """16-bit PCM WAV (dithering is not needed for speech QA/assembly at this level)."""
    x = np.clip(pcm.samples, -1.0, 32767 / 32768)
    interleaved = x if x.ndim == 1 else x.T.reshape(-1)
    data = np.round(interleaved * 32768.0).astype("<i2").tobytes()
    channels = pcm.channels
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 1, channels,
                         pcm.sample_rate, pcm.sample_rate * channels * 2, channels * 2, 16, b"data", len(data))
    return header + data


__all__ = ["PCM", "read_wav", "write_wav"]
