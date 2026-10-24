#!/usr/bin/env python3
"""Offline deterministic-QE audit for a saved numbered translation sample."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from b2c_translation.pipeline import deterministic_translation_check


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_response", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw_bytes = args.raw_response.read_bytes()
    raw = json.loads(raw_bytes)
    translations: dict[int, str] = {}
    for line in raw.get("raw_response", "").splitlines():
        match = re.match(r"^\s*(\d+)[.)]\s*(.+?)\s*$", line)
        if match:
            index = int(match.group(1))
            if index in translations:
                raise SystemExit(f"duplicate translation item: {index}")
            translations[index] = match.group(2)
    sources = raw.get("sources", [])
    expected = set(range(1, len(sources) + 1))
    if set(translations) != expected:
        raise SystemExit("translation item IDs do not match source rows")

    results = [
        deterministic_translation_check(source, translations[i], []).model_dump()
        for i, source in enumerate(sources, 1)
    ]
    payload = {
        "status": "audited",
        "model_id": raw.get("model_id"),
        "sample_size": len(results),
        "deterministic_pass": sum(bool(row["deterministic_pass"]) for row in results),
        "violation_count": sum(len(row["term_violations"]) for row in results),
        "routes": {"human_review": len(results)},
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "source_sha256": raw.get("source_sha256"),
        "reference_sha256": raw.get("reference_sha256"),
        "items": [
            {"item": i, "deterministic_pass": bool(row["deterministic_pass"]),
             "violations": row["term_violations"], "route": row["route"]}
            for i, row in enumerate(results, 1)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps({k: v for k, v in payload.items() if k != "items"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
