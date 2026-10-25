"""Bounded-memory polyphase resampling (scipy ``resample_poly``), block by block."""
from __future__ import annotations

import math
from collections.abc import Iterator

import numpy as np
from scipy.signal import resample_poly


def resample_blocks(x: np.ndarray, sr_in: int, sr_out: int, *, block_s: float = 30.0) -> Iterator[np.ndarray]:
    """Polyphase resampling (``scipy.signal.resample_poly``, same filter) in bounded blocks.

    Each block is resampled with a margin on both sides that covers the filter, and the margin
    is dropped again; block starts are multiples of the decimation factor, so the result is the
    same sequence as resampling the whole signal at once (to float rounding)."""
    if sr_in <= 0 or sr_out <= 0:
        raise ValueError("sample rates must be positive")
    g = math.gcd(sr_in, sr_out)
    up, down = sr_out // g, sr_in // g
    n = x.shape[-1]
    block = max(down, int(block_s * sr_in) // down * down)
    if up == down:
        for a in range(0, n, block):
            yield x[..., a:a + block]
        return
    need = math.ceil(10 * max(up, down) / up) + 2  # resample_poly's default filter half-length, in input samples
    margin = down * math.ceil(need / down)
    total = -(-n * up // down)
    produced = 0
    for a in range(0, n, block):
        lo, hi = max(0, a - margin), min(n, a + block + margin)
        y = resample_poly(x[..., lo:hi], up, down, axis=-1)
        first = (a - lo) * up // down
        last = total - produced + first if a + block >= n else first + block * up // down
        out = y[..., first:last]
        produced += out.shape[-1]
        yield out


__all__ = ["resample_blocks"]
