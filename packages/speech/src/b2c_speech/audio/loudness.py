"""ITU-R BS.1770-4 integrated loudness (EBU R128 gating) and true peak, in numpy/scipy.

K-weighting: the two biquads of BS.1770 (high-shelf pre-filter + RLB high-pass) are recomputed
for any sample rate from their analogue prototypes (the libebur128 formulation), so 24 kHz TTS
output is measured correctly, not only the 48 kHz coefficients printed in the recommendation.
Gating: 400 ms blocks with 75% overlap, absolute gate -70 LUFS, relative gate -10 LU.
True peak: polyphase oversampling to >= 192 kHz (x4 at 48 kHz, as in BS.1770-4 Annex 2).

Verification: unit tests check the BS.1770 reference behaviour (a 997 Hz sine of amplitude A
reads 20*log10(A) - 3.01 LKFS at 24/44.1/48 kHz); eval/suites/speech_v2 cross-checks real
tracks against ffmpeg ``ebur128`` (see eval/reports/speech-v2-2026-10-25.md).
"""
from __future__ import annotations

import math

import numpy as np
from scipy.signal import lfilter

from .resample import resample_blocks

ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
BLOCK_S = 0.4
OVERLAP = 0.75


def k_weighting(sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Combined K-weighting filter (b, a) for ``sample_rate``."""
    f0, gain_db, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / sample_rate)
    vh = 10 ** (gain_db / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    pre_b = np.array([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0])
    pre_a = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / sample_rate)
    rlb_b = np.array([1.0, -2.0, 1.0])
    rlb_a = np.array([1.0, 2 * (k * k - 1) / (1 + k / q + k * k), (1 - k / q + k * k) / (1 + k / q + k * k)])
    return np.convolve(pre_b, rlb_b), np.convolve(pre_a, rlb_a)


def _channels(samples: np.ndarray) -> np.ndarray:
    return samples[np.newaxis, :] if samples.ndim == 1 else samples


def _whole_signal_blocks(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    b, a = k_weighting(sample_rate)
    weighted = lfilter(b, a, _channels(samples), axis=-1)
    block = int(round(BLOCK_S * sample_rate))
    step = int(round(block * (1 - OVERLAP)))
    n = weighted.shape[-1]
    if n < block:
        return np.array([])
    power = weighted ** 2
    cumulative = np.concatenate([np.zeros((power.shape[0], 1)), np.cumsum(power, axis=-1)], axis=-1)
    starts = np.arange(0, n - block + 1, step)
    energies = (cumulative[:, starts + block] - cumulative[:, starts]) / block  # mean square per channel
    total = energies.sum(axis=0)  # channel weights G = 1.0 for L/R/C (mono/stereo)
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(total)


def block_loudness(samples: np.ndarray, sample_rate: int, *, chunk_s: float = 30.0) -> np.ndarray:
    """Loudness of each 400 ms gating block (LUFS); empty when the signal is shorter than a block.

    Streamed: the K-weighting filter runs chunk by chunk with its state carried over, and the
    weighted energy is summed per 100 ms hop, so memory is bounded by ``chunk_s`` instead of
    holding three full-length float64 copies (filtered signal, power, cumulative sum) of a
    one-hour track. A block is four consecutive hops (75% overlap), which is exact whenever
    the 400 ms block is a whole number of hops (16/22.05/24/32/44.1/48 kHz); otherwise the
    whole-signal formulation is used."""
    block = int(round(BLOCK_S * sample_rate))
    step = int(round(block * (1 - OVERLAP)))
    if block != 4 * step:
        return _whole_signal_blocks(samples, sample_rate)
    x = _channels(samples)
    n = x.shape[-1]
    if n < block:
        return np.array([])
    b, a = k_weighting(sample_rate)
    state = np.zeros((x.shape[0], max(len(a), len(b)) - 1))
    chunk = max(step, int(chunk_s * sample_rate) // step * step)
    hops: list[np.ndarray] = []
    carry = np.zeros(0)
    for lo in range(0, n, chunk):
        weighted, state = lfilter(b, a, x[:, lo:lo + chunk], axis=-1, zi=state)
        power = np.concatenate([carry, (weighted ** 2).sum(axis=0)])  # channel weights G = 1.0
        whole = len(power) // step * step
        hops.append(power[:whole].reshape(-1, step).sum(axis=1))
        carry = power[whole:]
    sums = np.concatenate(hops)
    energies = (sums[:-3] + sums[1:-2] + sums[2:-1] + sums[3:]) / block
    with np.errstate(divide="ignore"):
        return -0.691 + 10 * np.log10(energies)


def integrated_loudness(samples: np.ndarray, sample_rate: int) -> float:
    """Gated integrated loudness in LUFS; -inf for silence or a signal shorter than one block."""
    blocks = block_loudness(samples, sample_rate)
    above = blocks[blocks > ABSOLUTE_GATE_LUFS]
    if above.size == 0:
        return -math.inf
    energy = lambda values: float(np.mean(10 ** ((values + 0.691) / 10)))  # noqa: E731
    relative = -0.691 + 10 * math.log10(energy(above)) + RELATIVE_GATE_LU
    gated = above[above > relative]
    if gated.size == 0:
        return -math.inf
    return -0.691 + 10 * math.log10(energy(gated))


def true_peak_dbtp(samples: np.ndarray, sample_rate: int, *, block_s: float = 10.0) -> float:
    """Streamed in blocks (same result as oversampling the whole signal): x8 oversampling of a
    one-hour 24 kHz track at once would need ~5.5 GB of float64."""
    factor = max(1, math.ceil(192000 / sample_rate))
    x = _channels(samples)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if factor > 1 and x.shape[-1] > 1:
        for up in resample_blocks(x, sample_rate, sample_rate * factor, block_s=block_s):
            if up.size:
                peak = max(peak, float(np.max(np.abs(up))))
    return 20 * math.log10(peak) if peak > 0 else -math.inf


__all__ = ["block_loudness", "integrated_loudness", "k_weighting", "true_peak_dbtp"]
