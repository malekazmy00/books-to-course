"""Prepare deterministic, page-linked Phase 1 extraction chunks from B2C text output."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load_words(path: Path) -> list[tuple[str, int]]:
    words: list[tuple[str, int]] = []
    page_no = 1
    for match in re.finditer(r"\[PAGE (\d+)\]|\S+", path.read_text(encoding="utf-8")):
        if match.group(1):
            page_no = int(match.group(1))
        else:
            words.append((match.group(0), page_no))
    if not words:
        raise ValueError(f"no extracted words in {path}")
    return words


def write_chunks(source: Path, destination: Path, *, count: int, book_id: str, role: str, section: str) -> None:
    if count < 1:
        raise ValueError("chunk count must be positive")
    words = load_words(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for index in range(count):
            start = len(words) * index // count
            end = len(words) * (index + 1) // count
            part = words[start:end]
            if not part:
                raise ValueError(f"requested {count} chunks from only {len(words)} words")
            row = {
                "chunk_id": f"{book_id}:ch{index + 1:02d}",
                "book_id": book_id,
                "role": role,
                "section_path": [section],
                "pages": sorted({page for _, page in part}),
                "text": " ".join(word for word, _ in part),
                "approx_word_count": len(part),
                "rights_status": "internal_test_only",
            }
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"chunks={count} words={len(words)} output={destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/mnt/wsl/b2cdata/phase1-sources"))
    parser.add_argument("--primary-count", type=int, default=30)
    parser.add_argument("--supplement-count", type=int, default=7)
    args = parser.parse_args()
    write_chunks(args.data_dir / "lane-chapter5-probability.txt", args.data_dir / "lane-primary-30-chunks.jsonl", count=args.primary_count, book_id="lane-intro-stats", role="spine", section="5. Probability")
    write_chunks(args.data_dir / "thinkstats2-chapter3-pmf.txt", args.data_dir / "thinkstats-supplement-7-chunks.jsonl", count=args.supplement_count, book_id="think-stats-2e", role="supplementary", section="3. Probability mass functions")


if __name__ == "__main__":
    main()
