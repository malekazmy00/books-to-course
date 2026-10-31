"""Judge bake-off on planted errors (charter §3.5; amendment §5 step 3).

Sample translations the 2026-10-24 audit judged correct, plant known errors (mutate.py), and ask
each candidate judge — through the project's own ``LLMJudge`` and ``LLMGateway`` — to assess
clean and mutated targets. A judge detects an item when it reports >= 1 major/critical error.

Book text stays off git: inputs and raw outputs are written under --out on the data disk.
Labels are automatic (the audit verdict is a model judgement), declared as such in the report.

    uv run python eval/suites/judge_bakeoff/run.py --gold .../gold.jsonl --out DIR \
        --judge mercury-2=https://api.inceptionlabs.ai/v1@~/.config/book2course/inception-labs-api-key ...
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from b2c_contracts.models import TranslationUnit
from b2c_llm_gateway.client import LLMGateway
from b2c_translation.qa.estimators import LLMJudge

_spec = importlib.util.spec_from_file_location("judge_mutate", Path(__file__).with_name("mutate.py"))
mutate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate_mod)

PROSE_TYPES = {"paragraph", "text", "list_item"}


def load_sample(gold: Path, n_per_book: int, seed: int) -> list[dict]:
    """Clean target = the accepted translation judged correct, else the audit's corrected translation."""
    rows = []
    for line in gold.open(encoding="utf-8"):
        r = json.loads(line)
        src = r.get("source") or ""
        corrected = r.get("corrected_translation")
        if r.get("verdict") == "correct" and r.get("status") == "accepted":
            r["clean_target"], r["clean_origin"] = r.get("pipeline_target") or "", "pipeline_correct"
        elif corrected not in (None, "None", ""):
            r["clean_target"], r["clean_origin"] = corrected, "audit_corrected"
        else:
            continue
        if (r.get("block_type") in PROSE_TYPES and 120 <= len(src) <= 600
                and r.get("source_issue") in (None, "None", "", "{}") and r["clean_target"]):
            rows.append(r)
    rng = random.Random(seed)
    sample = []
    for book in sorted({r["book"] for r in rows}):
        pool = sorted((r for r in rows if r["book"] == book), key=lambda r: r["segment_id"])
        sample += rng.sample(pool, min(n_per_book, len(pool)))
    return sample


def build_items(sample: list[dict], seed: int) -> list[dict]:
    items = []
    for i, r in enumerate(sample):
        donor = sample[(i + len(sample) // 2) % len(sample)]["clean_target"]
        items.append({"item_id": f"{r['segment_id']}#clean", "segment_id": r["segment_id"], "book": r["book"],
                      "mutation": "clean", "clean_origin": r["clean_origin"], "source": r["source"],
                      "target": r["clean_target"]})
        for kind in mutate_mod.MUTATIONS:
            out = mutate_mod.mutate(kind, r["clean_target"], seed=seed, source=r["source"], donor=donor)
            if out is not None:
                items.append({"item_id": f"{r['segment_id']}#{kind}", "segment_id": r["segment_id"],
                              "book": r["book"], "mutation": kind, "source": r["source"], "target": out})
    return items


def parse_judge(spec: str) -> tuple[str, str, str, str, str | None]:
    """``label=model@base_url@key_file[@reasoning_effort]`` (model may differ from label)."""
    label, rest = spec.split("=", 1)
    model, base_url, key_file, *effort = rest.split("@", 3)
    return label, model, base_url, str(Path(key_file).expanduser()), (effort[0] if effort else None)


def run_judge(spec: str, items: list[dict], out_dir: Path, workers: int, max_tokens: int,
              retry_failed: bool = False, interval_s: float = 0.0) -> Path:
    label, model, base_url, key_file, effort = parse_judge(spec)
    gateway = LLMGateway(base_url, model, allow_remote=True, api_key=Path(key_file).read_text().strip(),
                         timeout_s=180, reasoning_effort=effort)
    judge = LLMJudge(gateway, prompt_version="qe-mqm-v1", max_tokens=max_tokens)
    path = out_dir / f"results-{label}.jsonl"
    done = set()
    if retry_failed and path.exists():  # failed-closed rows (429, truncation) are asked again
        kept = [line for line in path.open(encoding="utf-8") if not json.loads(line)["failed_closed"]]
        path.write_text("".join(kept), encoding="utf-8")
    if path.exists():
        done = {json.loads(line)["item_id"] for line in path.open(encoding="utf-8")}

    def one(item: dict) -> dict:
        unit = TranslationUnit(unit_id=item["segment_id"].replace(":", "-") + ":u00000",
                               book_id=item["book"], block_ids=[item["segment_id"]], kind="prose",
                               source_text=item["source"], pages=[1], section_path=[])
        if interval_s:
            time.sleep(interval_s)
        t0 = time.monotonic()
        outcome = judge.estimate(unit, item["target"])
        issues = [{"check_id": i.check_id, "severity": i.severity, "evidence": i.evidence} for i in outcome.issues]
        failed_closed = any(i["check_id"].startswith("llm_judge_") for i in issues)
        flagged = any(i["severity"] in ("major", "critical") for i in issues) and not failed_closed
        return {"item_id": item["item_id"], "mutation": item["mutation"], "book": item["book"],
                "judge": label, "model": model, "flagged": flagged, "failed_closed": failed_closed,
                "issues": issues, "seconds": round(time.monotonic() - t0, 2)}

    todo = [it for it in items if it["item_id"] not in done]
    with path.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(one, todo):
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            fh.flush()
    usage = getattr(gateway, "usage_totals", {})
    (out_dir / f"usage-{label}.json").write_text(json.dumps(usage, indent=1))
    return path


def summarize(out_dir: Path) -> dict:
    summary = {}
    for path in sorted(out_dir.glob("results-*.jsonl")):
        rows = [json.loads(line) for line in path.open(encoding="utf-8")]
        label = path.stem.removeprefix("results-")
        by = {}
        for r in rows:
            by.setdefault(r["mutation"], []).append(r)
        s = {"items": len(rows), "failed_closed": sum(r["failed_closed"] for r in rows),
             "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else None}
        for kind, rs in sorted(by.items()):
            ok = [r for r in rs if not r["failed_closed"]]
            s[kind] = {"n": len(ok), "flagged": sum(r["flagged"] for r in ok)}
        mutated = [r for r in rows if r["mutation"] != "clean" and not r["failed_closed"]]
        clean = [r for r in rows if r["mutation"] == "clean" and not r["failed_closed"]]
        s["recall_planted"] = round(sum(r["flagged"] for r in mutated) / len(mutated), 3) if mutated else None
        s["false_alarm_clean"] = round(sum(r["flagged"] for r in clean) / len(clean), 3) if clean else None
        summary[label] = s
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--judge", action="append", default=[])
    ap.add_argument("--per-book", type=int, default=15)
    ap.add_argument("--seed", type=int, default=20261031)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--interval", type=float, default=0.0, help="seconds between requests of one worker")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if not a.summary_only:
        items = build_items(load_sample(a.gold, a.per_book, a.seed), a.seed)
        items_path = a.out / "items.jsonl"
        items_path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
        manifest = {"gold_sha256": hashlib.sha256(a.gold.read_bytes()).hexdigest(), "seed": a.seed,
                    "per_book": a.per_book, "max_tokens": a.max_tokens, "items": len(items),
                    "items_sha256": hashlib.sha256(items_path.read_bytes()).hexdigest(),
                    "by_mutation": {k: sum(i["mutation"] == k for i in items)
                                    for k in ("clean", *mutate_mod.MUTATIONS)}}
        (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
        print(json.dumps(manifest), file=sys.stderr)
        with ThreadPoolExecutor(max_workers=max(1, len(a.judge))) as pool:
            list(pool.map(lambda j: run_judge(j, items, a.out, a.workers, a.max_tokens, a.retry_failed, a.interval),
                          a.judge))
    summary = summarize(a.out)
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
