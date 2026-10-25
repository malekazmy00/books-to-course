"""Edge-silence trimming of each segment before assembly (T-AUD-08).

XTTS/Fasih return each segment with its own leading/trailing silence, so the real gap between
two segments was ``trailing + planned pause + leading``. After trimming, only a short guard
(``keep_s``) of the original edge is kept so soft onsets/offsets are not clipped, and the
assembler subtracts that guard from the planned pause.
"""
from __future__ import annotations

import numpy as np
import pytest

from b2c_speech.audio.trim import trim_edges

SR = 24000


def tone(seconds, amp=0.3, f=220.0):
    t = np.arange(int(seconds * SR)) / SR
    return amp * np.sin(2 * np.pi * f * t)


def padded(lead, body, trail):
    return np.concatenate([np.zeros(int(lead * SR)), tone(body), np.zeros(int(trail * SR))])


def test_long_edges_are_cut_down_to_the_guard():
    x = padded(0.5, 1.0, 0.8)
    r = trim_edges(x, SR, keep_s=0.04)
    assert r.removed_leading_s == pytest.approx(0.46, abs=0.011)
    assert r.removed_trailing_s == pytest.approx(0.76, abs=0.011)
    assert r.kept_leading_s == pytest.approx(0.04, abs=0.011)
    assert r.kept_trailing_s == pytest.approx(0.04, abs=0.011)
    assert len(r.samples) / SR == pytest.approx(1.08, abs=0.02)


def test_short_edges_are_kept_whole_and_reported():
    x = padded(0.01, 1.0, 0.02)
    r = trim_edges(x, SR, keep_s=0.04)
    assert r.removed_leading_s == 0 and r.removed_trailing_s == 0
    assert r.kept_leading_s == pytest.approx(0.01, abs=0.011)
    assert len(r.samples) == len(x)


def test_voiced_part_is_never_cut():
    x = padded(0.3, 1.0, 0.3)
    r = trim_edges(x, SR, keep_s=0.0, fade_s=0.0)
    voiced = np.flatnonzero(np.abs(x) > 1e-6)
    assert np.abs(r.samples).sum() == pytest.approx(np.abs(x[voiced[0]:voiced[-1] + 1]).sum(), rel=1e-3)


def test_quiet_onset_inside_the_guard_survives():
    x = padded(0.5, 1.0, 0.5)
    onset = int(0.48 * SR)
    x[onset:onset + int(0.02 * SR)] = 0.004  # -48 dBFS: below the gate, inside the 40 ms guard
    r = trim_edges(x, SR, keep_s=0.04, fade_s=0.0)
    assert np.isclose(r.samples, 0.004).sum() >= int(0.015 * SR)


def test_cut_points_are_faded_so_they_do_not_click():
    x = np.full(SR * 2, 0.0)
    x[int(0.5 * SR):int(1.5 * SR)] = tone(1.0)
    x[int(0.45 * SR):int(0.5 * SR)] = 0.002  # low noise in the guard: cut lands on a non-zero sample
    r = trim_edges(x, SR, keep_s=0.04, fade_s=0.005)
    assert abs(r.samples[0]) < 1e-4 and abs(r.samples[-1]) < 1e-4


def test_silent_or_empty_segment_is_returned_unchanged():
    for x in (np.zeros(SR), np.zeros(0)):
        r = trim_edges(x, SR)
        assert len(r.samples) == len(x) and r.removed_leading_s == 0 and r.removed_trailing_s == 0


def test_invalid_arguments_are_refused():
    with pytest.raises(ValueError):
        trim_edges(np.zeros(10), 0)
    with pytest.raises(ValueError):
        trim_edges(np.zeros(10), SR, keep_s=-1)
