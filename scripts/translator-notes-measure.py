"""Measure translator notes (D-040) on a real translation run, read-only.

Two steps, so the running worker image does not need the new code:

1. export (inside the b2c worker container; SELECT on ``translation_runs`` /
   ``translation_unit_results`` in a read-only transaction, and a GET of the units artifact):

       docker exec -i b2c-worker-1 python - export book-main < scripts/translator-notes-measure.py > run.json

2. measure (on the host, with this checkout's packages on PYTHONPATH):

       python scripts/translator-notes-measure.py measure run.json [--examples 10] [--seed 7]

Nothing is written to the database, MinIO, the translation memory or the stage registry.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter


def export(book_id: str) -> None:
    import boto3
    import psycopg
    from b2c_core.config import Settings

    s = Settings()
    with psycopg.connect(s.pg_dsn) as c:
        c.read_only = True
        run_id, units_ref = c.execute(
            "SELECT run_id, units_ref_json FROM translation_runs WHERE book_id=%s ORDER BY created_at DESC LIMIT 1",
            (book_id,)).fetchone()
        rows = c.execute("SELECT unit_id, translation_json, drafts_json, updated_at FROM translation_unit_results "
                         "WHERE run_id=%s ORDER BY seq", (run_id,)).fetchall()
    key = units_ref["uri"].split("/", 3)[3] + "units.json"
    s3 = boto3.client("s3", endpoint_url=s.s3_endpoint, aws_access_key_id=s.s3_access_key,
                      aws_secret_access_key=s.s3_secret_key)
    units = json.loads(s3.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read())
    json.dump({"run_id": run_id, "book_id": book_id, "units_ref": units_ref, "units": units,
               "results": [{"unit_id": u, "translation": t, "drafts": d, "updated_at": str(at)}
                           for u, t, d, at in rows]}, sys.stdout, ensure_ascii=False)


def measure(path: str, examples: int, seed: int) -> None:
    from b2c_contracts import TranslationUnit, UnitTranslation
    from b2c_translation.notes import build_translator_notes

    from b2c_orchestration.promotion import evaluate_translator_notes

    data = json.load(open(path, encoding="utf-8"))
    units = [TranslationUnit.model_validate(u) for u in data["units"]]
    translations = {r["unit_id"]: UnitTranslation.model_validate(r["translation"]) for r in data["results"]}
    drafts = {r["unit_id"]: r["drafts"] for r in data["results"]}
    doc = build_translator_notes(units, translations, drafts, run_id=data["run_id"])
    decisions = {u.unit_id: (translations[u.unit_id].decision if u.unit_id in translations else None) for u in units}
    passed, metrics = evaluate_translator_notes(decisions, [n.unit_id for n in doc.notes])
    decided = metrics["total_units"] - metrics["pending"]
    # coverage over the units that already have a final result (the run is still going)
    decided_only = {k: v for k, v in decisions.items() if v is not None}
    passed_decided, metrics_decided = evaluate_translator_notes(decided_only, [n.unit_id for n in doc.notes])
    # examples: one per primary reason (seeded choice), then a seeded random fill
    rng = random.Random(seed)
    by_reason: dict[str, list[int]] = {}
    for i, n in enumerate(doc.notes):
        by_reason.setdefault(n.reasons[0], []).append(i)
    chosen = {rng.choice(ids) for _, ids in sorted(by_reason.items())}
    rest = [i for i in range(len(doc.notes)) if i not in chosen]
    chosen |= set(rng.sample(rest, max(0, min(examples - len(chosen), len(rest)))))
    sample = sorted(chosen)[:max(examples, len(by_reason))]
    out = {
        "run_id": data["run_id"], "book_id": doc.book_id,
        "counts": {"total_units": doc.total_units, "decided": decided, "accepted": doc.accepted,
                   "dropped": doc.dropped, "annotated": doc.annotated, "pending": doc.pending},
        "unit_decisions": dict(Counter(t.decision for t in translations.values() if t.unit_id in decisions)),
        "gate_full_book": {"passed": passed, **metrics},
        "gate_decided_units": {"passed": passed_decided, **metrics_decided},
        "primary_reason": dict(Counter(n.reasons[0] for n in doc.notes).most_common()),
        "all_reasons": dict(Counter(r for n in doc.notes for r in n.reasons).most_common()),
        "by_decision": dict(Counter(n.unit_decision for n in doc.notes)),
        "by_kind": dict(Counter(n.unit_kind for n in doc.notes).most_common()),
        "best_translation_from": dict(Counter(n.best_translation_from or "none" for n in doc.notes)),
        "best_differs_from_final": sum(1 for n in doc.notes if n.best_translation_from == "earlier_draft"),
        "examples": [doc.notes[i].model_dump(mode="json", exclude={"schema_version"}) for i in sample],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["export"]:
        export(args[1] if len(args) > 1 else "book-main")
    elif args[:1] == ["measure"]:
        n = int(args[args.index("--examples") + 1]) if "--examples" in args else 10
        seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 7
        measure(args[1], n, seed)
    else:
        sys.exit(__doc__)
