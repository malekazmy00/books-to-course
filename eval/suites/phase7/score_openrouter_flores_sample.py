#!/usr/bin/env python3
"""Score a saved numbered FLORES response; performs no network calls."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import sacrebleu

NUMBER = re.compile(r"(?<!\w)[-+]?\d+(?:[,.]\d+)*(?:%|(?=\W|$))")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_response", type=Path)
    parser.add_argument("score_output", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.raw_response.read_text(encoding="utf-8"))
    text = raw.get("raw_response", "")
    translations: dict[int, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(\d+)[.)]\s*(.+?)\s*$", line)
        if match:
            index = int(match.group(1))
            if index in translations:
                raise SystemExit(f"duplicate numbered translation line: {index}")
            translations[index] = match.group(2)
    expected_numbers = set(range(1, len(raw.get("sources", [])) + 1))
    if set(translations) != expected_numbers:
        result = {"status": "invalid_response_shape", "translation_lines_found": sorted(translations),
                  "finish_reason": raw.get("finish_reason"), "model_id": raw.get("model_id")}
    else:
        refs = raw["references"]
        hypotheses = [translations[i] for i in sorted(expected_numbers)]
        sources = raw["sources"]
        result = {
            "status": "scored",
            "model_id": raw["model_id"],
            "finish_reason": raw.get("finish_reason"),
            "sample_indices": raw["request"]["indices"],
            "sample_size": len(hypotheses),
            "numeric_digit_exact_rows": sum(
                NUMBER.findall(src) == NUMBER.findall(hyp)
                for src, hyp in zip(sources, hypotheses, strict=True)
            ),
            "chrfpp": sacrebleu.metrics.CHRF(word_order=2).corpus_score(hypotheses, [refs]).score,
            "bleu": sacrebleu.metrics.BLEU().corpus_score(hypotheses, [refs]).score,
            "usage": raw.get("usage", {}),
            "elapsed_s": raw["request"]["elapsed_s"],
            "source_sha256": raw.get("source_sha256"),
            "reference_sha256": raw.get("reference_sha256"),
            "raw_response_file": str(args.raw_response),
        }
        if raw.get("finish_reason") == "length":
            result["status"] = "truncated_unscored"
    args.score_output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.score_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.score_output.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
