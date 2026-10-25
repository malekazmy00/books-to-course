"""Ingest v2 measurement (R3): v1 ParsedBook vs ParsedBook v2 on a real book, plus the
translation audit's source_issue labels. Read-only on its inputs; no book text is written to
the output except counts.

Usage (book data stays outside git):
  python eval/suites/ingest_v2/measure.py --dir DATA/sat --book-id book-main --gold-book book1 \
      --gold /mnt/wsl/b2cdata/eval-datasets/translation-audit-2026-10-24/gold.jsonl --out out.json

DATA/<book> must hold source.pdf, parsed-v1.json (the production artifact) and layout/*.json
(the Docling S1 batches, b2c_ingest.layout.docling_layout_batches).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from b2c_contracts import ParsedBook, ParsedBookV2, parsed_book_v1_from_v2
from b2c_core.source_text import TERMINAL, Vocab
from b2c_ingest.layout import pdf_text_layer
from b2c_ingest.normalize import BookMeta, letter_spacing_residue, normalize_docling, normalize_text_layer
from b2c_ingest.quality import quality_report, quality_report_v1

_WORD = re.compile(r"[a-z]{3,}")


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def load_batches(layout_dir: Path) -> list[dict]:
    out = []
    for p in sorted(layout_dir.glob("pages-*.json")):
        d = json.loads(p.read_text())
        a, z = p.stem.split("-")[1:]
        d.setdefault("b2c_batch", {"format": "docling-document-batch/1", "first_page": int(a), "last_page": int(z)})
        out.append(d)
    return out


def map_v1_to_v2(v1_block: dict, v2_by_page: dict[int, list], tail: bool = False):
    """v2 blocks holding the v1 block's words (or its last 6 words), best overlap first."""
    w = words(v1_block["source"])
    if tail:
        w = w[-6:]
    target = Counter(w)
    if not target:
        return []
    pages = set(v1_block["pages"])
    cands = {id(b): b for p in pages for q in (p - 1, p, p + 1) for b in v2_by_page.get(q, [])}
    scored = []
    for b in cands.values():
        hit = sum((target & Counter(words(b.text))).values())
        if hit:
            scored.append((hit, b))
    scored.sort(key=lambda x: (-x[0], x[1].block_id))
    chosen, covered = [], 0
    for hit, b in scored:
        chosen.append(b)
        covered += hit
        if covered >= 0.6 * sum(target.values()):
            break
    return chosen


def gold_analysis(gold_path: Path, gold_book: str, v1: ParsedBook, v2: ParsedBookV2) -> dict:
    rows = [json.loads(line) for line in gold_path.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if r["book"] == gold_book]
    v2_by_page: dict[int, list] = defaultdict(list)
    for b in v2.blocks:
        for p in sorted({r.page_no for r in b.refs}):
            v2_by_page[p].append(b)
    vocab_v1 = Vocab([b.text for b in v1.blocks if b.type == "paragraph"])
    vocab_v2 = Vocab([b.text for b in v2.blocks if b.type == "paragraph"])
    out: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        issue = (r.get("source_issue") or {}).get("type")
        if issue is None:
            continue
        mapped = map_v1_to_v2(r, v2_by_page)
        c = out[issue]
        c["labelled"] += 1
        if not mapped:
            c["unmapped"] += 1
            continue
        types = {b.type for b in mapped}
        if issue == "cut_at_page_break":
            last = map_v1_to_v2(r, v2_by_page, tail=True)
            b = last[0] if last else mapped[-1]
            ok = "cut_at_page_break" not in b.quality_flags and (
                len({x.page_no for x in b.refs}) > 1 or bool(TERMINAL.search(b.text.rstrip())) or b.zone == "furniture"
                or b.type not in ("paragraph", "list_item"))
            c["resolved" if ok else "unresolved"] += 1
        elif issue == "ocr_noise":
            spaced_v1 = letter_spacing_residue(r["source"], r["block_type"] == "heading", vocab_v1)
            spaced_v2 = any(letter_spacing_residue(b.text, b.type == "heading", vocab_v2) for b in mapped)
            if spaced_v1:
                c["letter_spaced_in_v1"] += 1
                c["resolved" if not spaced_v2 else "unresolved"] += 1
            else:
                c["other_noise_not_assessed"] += 1  # glued words etc.: no v2 rule targets them
        elif issue == "reference_list":
            c["resolved" if types <= {"endnote", "footnote", "bibliography_entry"} else "unresolved"] += 1
        elif issue == "toc_or_index":
            c["resolved" if types <= {"toc_entry", "index_entry"} else "unresolved"] += 1
        elif issue == "header_footer":
            c["resolved" if all(b.zone == "furniture" for b in mapped) else "unresolved"] += 1
        elif issue == "merged_columns":
            c["resolved" if types <= {"table"} else "unresolved"] += 1
        else:
            c["not_assessed"] += 1
    total = len(rows)
    labelled = sum(c["labelled"] for c in out.values())
    resolved = sum(c["resolved"] for c in out.values())
    return {"segments": total, "segments_with_source_issue": labelled,
            "source_issue_rate_v1": round(labelled / total, 4) if total else None,
            "resolved": resolved,
            "upper_bound_source_issue_rate_v2": round((labelled - resolved) / total, 4) if total else None,
            "by_type": {k: dict(v) for k, v in sorted(out.items())}}


def unit_stats(book: ParsedBook) -> dict:
    """Translation units (D-028 builder) from a v1 book: what the translation stage would get."""
    from b2c_translation.units import build_translation_units

    units = build_translation_units(book)
    lengths = sorted(len(u.source_text) for u in units if u.kind != "noise")
    return {"units": len(units), "kinds": dict(Counter(u.kind for u in units)),
            "median_chars": lengths[len(lengths) // 2] if lengths else 0,
            "joined_page_break": sum("joined_page_break" in u.normalization for u in units),
            "with_footnote_markers": sum(bool(u.footnote_markers) for u in units)}


def sha(model) -> str:
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--gold", type=Path)
    ap.add_argument("--gold-book")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    d: Path = args.dir
    tl_path = d / "text-layer.json"
    if not tl_path.exists():
        tl_path.write_text(json.dumps(pdf_text_layer(d / "source.pdf")))
    tl = json.loads(tl_path.read_text())
    v1 = ParsedBook.model_validate_json((d / "parsed-v1.json").read_text())
    meta = BookMeta(book_id=args.book_id, title=v1.title, role=v1.role)
    batches = load_batches(d / "layout")
    t = time.time()
    v2 = normalize_docling(meta, batches, tl, parser={"name": "docling-layout", "version": "2.131.0"})
    secs = round(time.time() - t, 1)
    again = normalize_docling(meta, load_batches(d / "layout"), tl, parser={"name": "docling-layout", "version": "2.131.0"})
    v2t = normalize_text_layer(meta, tl)
    (d / "parsed-v2.json").write_text(v2.model_dump_json())
    (d / "parsed-v2-text.json").write_text(v2t.model_dump_json())
    result = {
        "book_id": args.book_id, "pages": tl["page_count"], "normalize_seconds": secs,
        "deterministic": sha(v2) == sha(again), "parsed_v2_sha256": sha(v2),
        "v1": quality_report_v1(v1, tl).model_dump(mode="json"),
        "v2_docling": quality_report(v2, tl, sha(v2)).model_dump(mode="json"),
        "v2_text_layer": quality_report(v2t, tl, sha(v2t)).model_dump(mode="json"),
        "v2_types": dict(Counter(b.type for b in v2.blocks)),
        "v2_zones": dict(Counter(b.zone for b in v2.blocks)),
        "v2_flags": dict(Counter(f for b in v2.blocks for f in b.quality_flags)),
        "v1_types": dict(Counter(b.type for b in v1.blocks)),
        "units_from_v1": unit_stats(v1),
        "units_from_v2_projection": unit_stats(parsed_book_v1_from_v2(v2)),
    }
    if args.gold and args.gold_book:
        result["gold"] = gold_analysis(args.gold, args.gold_book, v1, v2)
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({k: result[k] for k in ("book_id", "deterministic", "normalize_seconds")}))


if __name__ == "__main__":
    main()
