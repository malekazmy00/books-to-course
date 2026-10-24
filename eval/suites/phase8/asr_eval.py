#!/usr/bin/env python3
"""Bounded local Arabic ASR check for a generated test clip."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from faster_whisper import WhisperModel


def normalize_arabic(text: str) -> str:
    chars = []
    for char in text.casefold():
        if unicodedata.category(char).startswith("M"):
            continue
        if unicodedata.category(char).startswith("P") or char.isspace():
            continue
        chars.append(char)
    return "".join(chars)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-repo", default="Systran/faster-whisper-tiny")
    args = parser.parse_args()
    model = WhisperModel(
        args.model_repo, device="cpu", compute_type="int8", cpu_threads=56,
        download_root=str(args.model_dir),
    )
    segments, info = model.transcribe(
        str(args.audio), language="ar", beam_size=5,
        vad_filter=False, condition_on_previous_text=False,
    )
    text = "".join(segment.text for segment in segments).strip()
    expected = normalize_arabic(args.expected)
    hypothesis = normalize_arabic(text)
    errors = edit_distance(expected, hypothesis)
    cer = errors / max(len(expected), 1)
    result = {
        "model": args.model_repo,
        "license": "MIT",
        "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "language_detected": info.language,
        "language_probability": info.language_probability,
        "expected": args.expected,
        "hypothesis": text,
        "normalization": "casefold; strip Unicode combining marks, punctuation, whitespace",
        "normalized_character_count": len(expected),
        "character_errors": errors,
        "cer": cer,
        "duration_s": info.duration,
    }
    out = args.audio.with_suffix(".asr.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "model", "language_detected", "language_probability",
        "normalized_character_count", "character_errors", "cer", "duration_s", "audio_sha256",
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
