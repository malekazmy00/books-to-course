#!/usr/bin/env python3
"""Strictly audit MPEG-1 Layer III frames for a constant bitrate/sample rate."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

BITRATES_KBPS = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
SAMPLE_RATES = [44100, 48000, 32000]


def audit(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    offset = 0
    id3_bytes = 0
    if data.startswith(b"ID3"):
        if len(data) < 10:
            raise ValueError("truncated ID3 header")
        size = sum((data[i] & 0x7F) << (7 * (9 - i)) for i in range(6, 10))
        id3_bytes = 10 + size
        offset = id3_bytes
    counts: Counter[tuple[int, int, int, int]] = Counter()
    frames = 0
    while offset + 4 <= len(data):
        header = int.from_bytes(data[offset:offset + 4], "big")
        if header >> 21 != 0x7FF:
            raise ValueError(f"invalid MPEG frame sync at byte {offset}")
        version = (header >> 19) & 0b11
        layer = (header >> 17) & 0b11
        bitrate_index = (header >> 12) & 0b1111
        sample_rate_index = (header >> 10) & 0b11
        padding = (header >> 9) & 1
        if version != 0b11 or layer != 0b01:
            raise ValueError(f"not MPEG-1 Layer III at byte {offset}")
        if bitrate_index in (0, 15) or sample_rate_index == 3:
            raise ValueError(f"free/invalid bitrate or sample rate at byte {offset}")
        bitrate = BITRATES_KBPS[bitrate_index] * 1000
        sample_rate = SAMPLE_RATES[sample_rate_index]
        frame_size = (144 * bitrate // sample_rate) + padding
        if offset + frame_size > len(data):
            raise ValueError(f"truncated MPEG frame at byte {offset}")
        counts[(version, layer, bitrate, sample_rate)] += 1
        frames += 1
        offset += frame_size
    trailing_bytes = len(data) - offset
    if trailing_bytes:
        raise ValueError(f"{trailing_bytes} trailing bytes after final frame")
    rates = {row[2] for row in counts}
    sample_rates = {row[3] for row in counts}
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "file_bytes": len(data),
        "id3_bytes": id3_bytes,
        "frames": frames,
        "frame_rate_counts": {
            f"mpeg1-layer3-{bitrate // 1000}kbps-{sample_rate}hz": count
            for (_, _, bitrate, sample_rate), count in sorted(counts.items())
        },
        "constant_bitrate": len(rates) == 1,
        "constant_sample_rate": len(sample_rates) == 1,
        "trailing_bytes": trailing_bytes,
        "duration_s": frames * 1152 / next(iter(sample_rates)) if frames else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mp3", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.mp3), indent=2))


if __name__ == "__main__":
    main()
