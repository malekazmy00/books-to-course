"""Ingest gate v2 (D-043) on a real book: cross-parser agreement, before/after numbers.

Reads the S1 intermediates of one book (outside git), re-runs S2 with the current code and
writes counts only (no book text):
  * the agreement of the two independent extractions with each other (pypdf vs pdfium), per
    page: the noise floor the page threshold is set against;
  * IngestQualityReport v2 for the production v1 ParsedBook, for ParsedBook v2 (Docling) and for
    ParsedBook v2 from the text layer alone, with the pages each per-page check names;
  * S2 determinism (two runs, same SHA) and the glued-word splits S2 made.

Usage:
  python eval/suites/ingest_v2/gate_eval.py --dir DATA/sat --text-layer TL.json --book-id book-main \
      --out out.json [--parsed-out parsed-v2.json]
DATA/<book> holds source.pdf, parsed-v1.json (production artifact) and layout/pages-*.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from b2c_contracts import ParsedBook
from b2c_ingest.layout import pdf_text_layer
from b2c_ingest.normalize import BookMeta, normalize_docling, normalize_text_layer
from b2c_ingest.quality import (PAGE_AGREEMENT_MIN, _f1, _letter_tokens, _reference_pages, quality_report,
                                quality_report_v1)

sys.path.insert(0, str(Path(__file__).parent))
from measure import load_batches  # noqa: E402


def sha(model) -> str:
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def reference_agreement(tl: dict) -> dict:
    a, b = _reference_pages(tl, "text"), _reference_pages(tl, "text_pdfium")
    scores = []
    for p in sorted(set(a) & set(b)):
        wa = Counter(w for w in _letter_tokens(a[p]) if len(w) >= 3)
        wb = Counter(w for w in _letter_tokens(b[p]) if len(w) >= 3)
        if max(sum(wa.values()), sum(wb.values())) < 3:
            continue
        scores.append(_f1(sum((wa & wb).values()), sum(wa.values()), sum(wb.values())))
    scores.sort()
    if not scores:
        return {}
    return {"pages": len(scores), "median": round(statistics.median(scores), 4),
            "p1": round(scores[len(scores) // 100], 4), "p5": round(scores[len(scores) // 20], 4),
            "min": round(scores[0], 4), "below_min": sum(s < PAGE_AGREEMENT_MIN for s in scores),
            "below_0_98": sum(s < 0.98 for s in scores)}


def summary(report) -> dict:
    out = report.model_dump(mode="json")
    out["failed"] = report.failed
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--text-layer", required=True, type=Path)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--parsed-out", type=Path)
    args = ap.parse_args()
    d: Path = args.dir
    if not args.text_layer.exists():
        args.text_layer.write_text(json.dumps(pdf_text_layer(d / "source.pdf"), sort_keys=True, ensure_ascii=False))
    tl = json.loads(args.text_layer.read_text())
    v1 = ParsedBook.model_validate_json((d / "parsed-v1.json").read_text())
    meta = BookMeta(book_id=args.book_id, title=v1.title, role=v1.role)
    parser = {"name": "docling-layout", "version": "2.131.0"}
    t = time.time()
    v2 = normalize_docling(meta, load_batches(d / "layout"), tl, parser=parser)
    secs = round(time.time() - t, 1)
    again = normalize_docling(meta, load_batches(d / "layout"), tl, parser=parser)
    v2t = normalize_text_layer(meta, tl)
    if args.parsed_out:
        args.parsed_out.write_text(v2.model_dump_json())
    t = time.time()
    rep_v2 = quality_report(v2, tl, sha(v2))
    gate_secs = round(time.time() - t, 1)
    result = {
        "book_id": args.book_id, "pages": tl["page_count"], "text_layer_format": tl.get("format"),
        "pdfium_version": tl.get("pdfium_version"), "normalize_seconds": secs, "gate_seconds": gate_secs,
        "deterministic": sha(v2) == sha(again), "parsed_v2_sha256": sha(v2),
        "reference_agreement": reference_agreement(tl),
        "v1": summary(quality_report_v1(v1, tl)),
        "v2_docling": summary(rep_v2),
        "v2_text_layer": summary(quality_report(v2t, tl, sha(v2t))),
        "v2_glued_split_blocks": sum("glued_words_split" in b.normalization for b in v2.blocks),
        "v2_text_layer_glued_split_blocks": sum("glued_words_split" in b.normalization for b in v2t.blocks),
        "v2_flags": dict(Counter(f for b in v2.blocks for f in b.quality_flags)),
    }
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True))
    brief = {k: result[k] for k in ("book_id", "deterministic", "normalize_seconds", "gate_seconds",
                                    "reference_agreement")}
    for name in ("v1", "v2_docling", "v2_text_layer"):
        brief[name] = {"failed": result[name]["failed"],
                       **{k: result[name]["metrics"].get(k) for k in (
                           "cross_parser_word_recall", "cross_parser_word_precision", "pages_below_agreement",
                           "pages_spacing_only", "min_page_agreement", "glued_token_rate", "glued_tokens",
                           "reference_disagreement_pages", "image_text_words", "single_block_full_pages",
                           "full_body_pages", "max_body_block_chars")},
                       "below_pages": result[name]["page_findings"].get("pages_below_agreement", [])[:40]}
    print(json.dumps(brief, indent=1))


if __name__ == "__main__":
    main()
