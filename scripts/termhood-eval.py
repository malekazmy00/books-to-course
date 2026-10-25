"""Measure the termhood classifier (D-039) on the provisional review decisions (D-031).

The gold set is the 1959 machine-review decisions of the first two books
(``termbase-review-book{1,2}.jsonl``): accepted (approved / edited) vs rejected, the rejected
split into "general vocabulary" and noise by the review's own reason. It is a temporary,
machine-made gold set, not a human one.

    uv run python scripts/termhood-eval.py --units units.json --review-dir outputs/... \
        --profile deepseek-mqm --profiles-file docker/translation-profiles.json \
        --key-file /root/.config/book2course/openrouter-api-key --sample 300 --out preds.jsonl

``--units`` is a JSON {"book1": {"units": [...]}, "book2": {...}} of TranslationUnits (read-only
dump of the running books). The candidates are re-extracted with the current extractor, so the
classifier is measured where it would run (after the deterministic noise fixes). The few-shot
example terms are excluded from the sample. Book text goes to the profile's model: use an
external profile only during development with the owner's approval (D-029). The API key is
read from ``--key-file`` and never printed.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

from b2c_contracts import EngineIdentity, TranslationUnit
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway.cache import InMemoryLLMCache
from b2c_translation.termbase import FEW_SHOT_EXAMPLES, TermhoodClassifier, candidate_contexts, extract_candidates
from b2c_translation.termbase.model import source_key


def gold_label(row: dict) -> str:
    if row["decision"] != "rejected":
        return "accepted"
    return "general" if row["reason"].startswith("general vocabulary") else "noise"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--units", required=True)
    ap.add_argument("--review-dir", required=True)
    ap.add_argument("--profiles-file", required=True)
    ap.add_argument("--profile", default="deepseek-mqm")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--sample", type=int, default=300)
    ap.add_argument("--accepted-share", type=float, default=0.6)
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    profiles = json.loads(Path(args.profiles_file).read_text(encoding="utf-8"))
    profile = next(p for p in profiles["engines"] + profiles["qe"] if p["id"] == args.profile)
    key = Path(args.key_file).read_text(encoding="utf-8").strip()
    gateway = LLMGateway(profile["base_url"], profile["model_id"], cache=InMemoryLLMCache(),
                         timeout_s=float(profile.get("timeout_s", 300)), allow_remote=bool(profile.get("allow_remote")),
                         api_key=key, reasoning_effort=profile.get("reasoning_effort"))
    identity = EngineIdentity(provider=profile["provider"], model_id=profile["model_id"],
                              revision=profile.get("revision"), prompt_version="unset")
    classifier = TermhoodClassifier(gateway, identity, book_text_allowed=True, batch_size=args.batch_size)

    dump = json.loads(Path(args.units).read_text(encoding="utf-8"))
    excluded = {source_key(e["term"]) for e in FEW_SHOT_EXAMPLES}
    pool: dict[str, list[tuple[str, object, str, str]]] = {"accepted": [], "general": [], "noise": []}
    texts: dict[str, dict[str, str]] = {}
    for book in sorted(dump):
        units = [TranslationUnit.model_validate(u) for u in dump[book]["units"]]
        texts[book] = {u.unit_id: u.source_text for u in units}
        gold = {}
        for line in (Path(args.review_dir) / f"termbase-review-{book}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            gold[source_key(row["source"])] = row
        for cand in extract_candidates(units):
            row = gold.get(source_key(cand.source))
            if row is None or source_key(cand.source) in excluded:
                continue
            pool[gold_label(row)].append((book, cand, row["reason"], gold_label(row)))
    rng = random.Random(args.seed)
    n_acc = round(args.sample * args.accepted_share)
    n_rej = args.sample - n_acc
    rejected = pool["general"] + pool["noise"]
    sample = rng.sample(pool["accepted"], min(n_acc, len(pool["accepted"]))) + \
        rng.sample(rejected, min(n_rej, len(rejected)))
    print(f"pool: accepted={len(pool['accepted'])} general={len(pool['general'])} noise={len(pool['noise'])}; "
          f"sample={len(sample)}", file=sys.stderr)

    started = time.monotonic()
    preds: dict[tuple[str, str], object] = {}
    for book in sorted(texts):
        cands = [c for b, c, _, _ in sample if b == book]
        verdicts = classifier.classify(cands, candidate_contexts(cands, texts[book]))
        for c in cands:
            preds[(book, c.source)] = verdicts.get(c.source)
    seconds = time.monotonic() - started

    confusion: Counter = Counter()
    with open(args.out, "w", encoding="utf-8") as fh:
        for book, cand, reason, label in sample:
            verdict = preds.get((book, cand.source))
            tier = verdict.tier if verdict is not None else "unclassified"
            confusion[(label, tier)] += 1
            fh.write(json.dumps({"book": book, "source": cand.source, "kind": cand.kind, "gold": label,
                                 "review_reason": reason, "tier": tier,
                                 "reason": verdict.reason if verdict is not None else None},
                                ensure_ascii=False) + "\n")

    def count(label, *tiers):
        return sum(confusion[(label, t)] for t in tiers)

    acc = sum(count("accepted", t) for t in ("locked", "hint", "dropped", "unclassified"))
    metrics = {
        "sample": len(sample), "seconds": round(seconds, 1), "model": profile["model_id"],
        "confusion": {f"{g}->{t}": n for (g, t), n in sorted(confusion.items())},
        # kept = not dropped (an unclassified candidate is kept: the build treats it as not dropped)
        "sensitivity_accepted_kept": round(count("accepted", "locked", "hint", "unclassified") / max(1, acc), 4),
        "accepted_locked_share": round(count("accepted", "locked") / max(1, acc), 4),
        "noise_dropped_share": round(count("noise", "dropped") / max(1, count("noise", "locked", "hint", "dropped",
                                                                               "unclassified")), 4),
        "general_not_locked_share": round(count("general", "hint", "dropped") / max(1, count(
            "general", "locked", "hint", "dropped", "unclassified")), 4),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
