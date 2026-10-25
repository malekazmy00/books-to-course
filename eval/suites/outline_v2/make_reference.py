"""Build a reference outline (golden starting point) from a PDF's bookmarks (R4, audit 02 §5.6).

    uv run python eval/suites/outline_v2/make_reference.py --pdf source.pdf --name gramsci \
        --out eval/golden/outline/gramsci.json

Each entry is {title, level, page, zone}. Title, level and physical page come from the PDF
document outline (ISO 32000 §12.3.3) through pypdf. ``zone`` (front_matter / body / back_matter)
is proposed from the title (b2c_outline.v2.build.classify) and must be reviewed: the file records
who reviewed it. The source PDF is identified by its SHA-256; no book text is stored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from b2c_outline.v2.build import classify


def bookmarks(pdf: Path) -> list[dict]:
    reader = PdfReader(str(pdf))

    def walk(items, depth=1):
        for item in items:
            if isinstance(item, list):
                yield from walk(item, depth + 1)
            else:
                yield {"title": " ".join(str(item.title).split()), "level": depth,
                       "page": reader.get_destination_page_number(item) + 1}

    out = list(walk(reader.outline))
    seen_body = False
    for entry in out:
        side = classify(entry["title"])
        if side is None:
            seen_body = True
            entry["zone"] = "body"
        else:
            entry["zone"] = "front_matter" if side[0] == "front" and not seen_body else "back_matter"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, type=Path)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--reviewed-by", default="unreviewed")
    args = ap.parse_args()
    sha = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    doc = {"format": "outline-reference/1", "name": args.name, "source_file_sha256": sha,
           "source": "PDF document outline (bookmarks) read with pypdf; zones proposed by title",
           "zones_reviewed_by": args.reviewed_by, "entries": bookmarks(args.pdf)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.out, len(doc["entries"]), "entries")


if __name__ == "__main__":
    main()
