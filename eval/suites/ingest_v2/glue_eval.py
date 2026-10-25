"""Glued-word repair (D-043) on a real book, checked against an independent parser.

For every token the splitter splits, pdfium (the second text extraction of the same PDF page)
is the automatic judge:
  confirmed     pdfium prints the parts as adjacent words and never prints the glued token;
  false_split   pdfium prints the token whole on that page (the book has the word);
  unverifiable  neither (the token is not on the page in pdfium's reading);
  line_break_compound  pdfium joins the token over a line-end hyphen ('national-|popular'), so
                the PDF cannot say whether the hyphen belongs to a compound.
Glued tokens before/after are counted the same way (a token pdfium prints as 2-3 adjacent words).
The script prints counts only; ``--sample`` writes token/context pairs for a manual review to a
file the caller keeps outside git (book text).

Usage:
  python eval/suites/ingest_v2/glue_eval.py --text-layer TL.json --source pypdf|v1|v2 \
      [--parsed parsed.json] [--sample N --sample-out FILE] [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from b2c_ingest.glued import GlueSplitter

TOKEN = re.compile(r"[A-Za-z]+")


def _join_breaks(text: str) -> str:
    text = text.replace("￾", "").replace("\x02", "")
    return re.sub(r"(?<=[a-z]) ?[-­‐] *\r?\n *(?=[a-z])", "", text)


class Judge:
    def __init__(self, text_layer: dict):
        self.tokens: dict[int, set[str]] = {}
        self.concat: dict[int, dict[str, tuple[str, str]]] = {}
        self.soft: dict[int, set[str]] = {}  # tokens joined over a line-end hyphen (U+FFFE)
        for page in text_layer["pages"]:
            raw = page.get("text_pdfium") or ""
            self.soft[page["page_no"]] = {m.group().replace("\ufffe", "").lower()
                                          for m in re.finditer(r"[A-Za-z]+\ufffe[A-Za-z]+", raw)}
            text = _join_breaks(raw)
            words = list(TOKEN.finditer(text))
            low = [w.group().lower() for w in words]
            self.tokens[page["page_no"]] = set(low)
            cc: dict[str, tuple[str, str]] = {}
            for i in range(len(low) - 1):
                sep = text[words[i].end():words[i + 1].start()]
                cc.setdefault(low[i] + low[i + 1], (" ".join(low[i:i + 2]), sep))
                if i + 2 < len(low):
                    sep2 = sep + "|" + text[words[i + 1].end():words[i + 2].start()]
                    cc.setdefault(low[i] + low[i + 1] + low[i + 2], (" ".join(low[i:i + 3]), sep2))
            self.concat[page["page_no"]] = cc

    def glued(self, text: str, pages: list[int]) -> list[str]:
        out = []
        for tok in TOKEN.findall(_join_breaks(text)):
            low = tok.lower()
            if len(low) >= 4 and not any(low in self.tokens.get(p, ()) for p in pages) \
                    and any(low in self.concat.get(p, {}) for p in pages):
                out.append(tok)
        return out

    def verdict(self, token: str, parts: tuple[str, ...], pages: list[int]) -> tuple[str, str]:
        low = token.lower()
        if any(low in self.soft.get(p, ()) for p in pages):
            # 'national-|popular' at a line end: the PDF does not say whether the hyphen is a
            # compound's or the line break's; neither a confirmed split nor a false one
            return "line_break_compound", ""
        if any(low in self.tokens.get(p, ()) for p in pages):
            return "false_split", ""
        want = " ".join(p.lower() for p in parts)
        for p in pages:
            hit = self.concat.get(p, {}).get(low)
            if hit:
                if hit[0] != want:
                    return "wrong_boundary", hit[1]
                return "confirmed", hit[1]
        return "unverifiable", ""


def items(source: str, text_layer: dict, parsed: dict | None) -> list[tuple[str, list[int]]]:
    if source == "pypdf":
        return [(p["text"], [p["page_no"]]) for p in text_layer["pages"]]
    return [(b["text"], sorted({r["page_no"] for r in b["refs"]})) for b in parsed["blocks"]
            if b.get("type") not in ("table", "code")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text-layer", required=True, type=Path)
    ap.add_argument("--source", required=True, choices=["pypdf", "v1", "v2"])
    ap.add_argument("--parsed", type=Path)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--sample-out", type=Path)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    tl = json.loads(args.text_layer.read_text())
    parsed = json.loads(args.parsed.read_text()) if args.parsed else None
    data = items(args.source, tl, parsed)
    judge = Judge(tl)
    splitter = GlueSplitter([t for t, _ in data])
    verdicts: Counter[str] = Counter()
    rules: Counter[tuple[str, str]] = Counter()
    seps: Counter[str] = Counter()
    tokens = before = after = 0
    sample_pool = []
    for text, pages in data:
        tokens += len(TOKEN.findall(text))
        before += len(judge.glued(text, pages))
        new, splits = splitter.split(text)
        after += len(judge.glued(new, pages))
        for s in splits:
            v, sep = judge.verdict(s.token, s.parts, pages)
            verdicts[v] += 1
            rules[(s.rule + ("+a" if s.parts[0].lower() == "a" else ""), v)] += 1
            if v == "confirmed":
                seps["dash" if re.search(r"[-–—]", sep) else "space" if sep.strip() == "" else "other"] += 1
            i = text.find(s.token)
            sample_pool.append({"token": s.token, "split": " ".join(s.parts), "rule": s.rule, "verdict": v,
                                "pages": pages, "context": text[max(0, i - 60):i + len(s.token) + 60]})
    total = sum(verdicts.values())
    out = {"source": args.source, "tokens": tokens, "glued_before": before, "glued_after": after,
           "glued_rate_before": round(before / max(1, tokens), 6), "glued_rate_after": round(after / max(1, tokens), 6),
           "splits": total, "verdicts": dict(verdicts),
           "by_rule": {f"{r}:{v}": n for (r, v), n in sorted(rules.items())},
           "confirmed_separator": dict(seps),
           "false_split_rate": round(verdicts["false_split"] / max(1, total), 4)}
    print(json.dumps(out, indent=1))
    if args.sample and args.sample_out:
        rnd = random.Random(args.seed)
        pick = rnd.sample(sample_pool, min(args.sample, len(sample_pool)))
        args.sample_out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in pick) + "\n")


if __name__ == "__main__":
    main()
