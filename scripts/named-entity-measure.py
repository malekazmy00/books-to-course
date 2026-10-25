"""Re-run the deterministic checks of this checkout on the stored drafts of a translation run and
count the translator notes (D-040) that would disappear (D-046). Read-only.

1. export the run (SELECT in a read-only transaction + GET of units.json), as for D-040:

       docker exec -i b2c-worker-1 python - export book-main < scripts/translator-notes-measure.py > run.json

2. export the approved termbase whose ``approved_digest`` is the run's ``termbase_digest``
   (SELECT in a read-only transaction + LIST/GET under ``book-termbase-approved``):

       docker exec -i b2c-worker-1 python - export-termbase book-main < scripts/named-entity-measure.py > tb.json

3. replay twice — once on the commit before D-046, once on this one — then compare:

       python scripts/named-entity-measure.py replay run.json tb.json old.json     # on the old commit
       python scripts/named-entity-measure.py replay run.json tb.json new.json     # on this commit
       python scripts/named-entity-measure.py measure run.json old.json new.json [--cut "2026-10-25 16:28:19.947897+00:00"]

``replay`` also reports how many stored drafts it reproduces exactly (a check of the harness).
Nothing is written to the database, MinIO, the translation memory or the stage registry.
"""
from __future__ import annotations

import collections
import json
import sys

BLOCKING = ("major", "critical")


def export_termbase(book_id: str) -> None:
    import boto3
    import psycopg
    from b2c_core.config import Settings
    from b2c_orchestration.termbase_review import approved_digest, approved_snapshot
    from b2c_translation.termbase import BookTermbase

    s = Settings()
    with psycopg.connect(s.pg_dsn) as c:
        c.read_only = True
        run_id, course_id = c.execute("SELECT run_id, course_id FROM translation_runs WHERE book_id=%s "
                                      "ORDER BY created_at DESC LIMIT 1", (book_id,)).fetchone()
        digests = {r[0] for r in c.execute(
            "SELECT DISTINCT termbase_digest FROM translation_unit_results WHERE run_id=%s", (run_id,))}
    s3 = boto3.client("s3", endpoint_url=s.s3_endpoint, aws_access_key_id=s.s3_access_key,
                      aws_secret_access_key=s.s3_secret_key)
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=s.s3_bucket,
                                                                       Prefix=f"{course_id}/book-termbase-approved/"):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("termbase.json"):
                continue
            raw = s3.get_object(Bucket=s.s3_bucket, Key=obj["Key"])["Body"].read().decode()
            try:
                snap = approved_snapshot(BookTermbase.from_json(raw))
            except Exception:  # noqa: BLE001 - other formats/books are skipped
                continue
            if snap.book_id == book_id and approved_digest(snap) in digests:
                sys.stdout.write(raw)
                return
    sys.exit("no approved termbase matches the run's digest")


def _key(issue) -> list[str]:
    ev = issue["evidence"] if isinstance(issue, dict) else issue.evidence
    cid = issue["check_id"] if isinstance(issue, dict) else issue.check_id
    sev = issue["severity"] if isinstance(issue, dict) else issue.severity
    return [cid, sev, ev.get("term") or ev.get("variant") or ""]


def replay(run_path: str, termbase_path: str, out_path: str) -> None:
    from b2c_contracts import TranslationUnit
    from b2c_translation.book_pipeline import UnitTranslator
    from b2c_translation.qa.checks import run_checks
    from b2c_translation.termbase import BookTermbase

    class _NoEngine:  # never called: only the check context is built
        identity = None
        capabilities = None

    data = json.load(open(run_path, encoding="utf-8"))
    tb = BookTermbase.from_json(open(termbase_path, encoding="utf-8").read())
    translator = UnitTranslator(_NoEngine(), terms=tb.locked_terms(), entities=tb.entity_table(),
                                forbidden=tb.forbidden_variants(),
                                hints=tb.hint_terms() if hasattr(tb, "hint_terms") else {})
    units = {u["unit_id"]: TranslationUnit.model_validate(u) for u in data["units"]}
    out, agree = {}, collections.Counter()
    for r in data["results"]:
        t = r["translation"]
        candidates = [(d["text"], d["issues"]) for d in (r["drafts"] or [])] or [(t["target_text"], t["issues"])]
        per = []
        for text, stored in candidates:
            if t["decision"] == "dropped":
                per.append([])
                continue
            got = [_key(i) for i in run_checks(translator._context(units[r["unit_id"]], text))]
            want = [_key(i) for i in stored if i.get("origin") == "deterministic"]
            agree["reproduced" if sorted(map(tuple, got)) == sorted(map(tuple, want)) else "differs"] += 1
            per.append(got)
        out[r["unit_id"]] = per
    print(dict(agree), file=sys.stderr)
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)


def measure(run_path: str, old_path: str, new_path: str, cut: str | None) -> None:
    data = json.load(open(run_path, encoding="utf-8"))
    old, new = json.load(open(old_path, encoding="utf-8")), json.load(open(new_path, encoding="utf-8"))
    kinds = {u["unit_id"]: u["kind"] for u in data["units"]}
    rows = [r for r in data["results"] if cut is None or r["updated_at"] <= cut]
    noted = [r for r in rows if r["translation"]["decision"] in ("needs_review", "rejected")]
    qe = collections.Counter("passed" if d["qe"]["passed"] else "failed"
                             for r in rows for d in (r["drafts"] or []) if d.get("qe"))
    status, units = collections.Counter(), collections.defaultdict(list)
    removed = collections.Counter()
    for r in rows:
        uid = r["unit_id"]
        for a, b in zip(old[uid], new[uid]):
            for k, v in (collections.Counter(map(tuple, a)) - collections.Counter(map(tuple, b))).items():
                removed[k[0]] += v
    for r in noted:
        uid, t = r["unit_id"], r["translation"]
        blocking = lambda issues: [i for i in issues if i[1] in BLOCKING]  # noqa: E731
        if t["origin"] == "rule":
            s = "gone (rule output accepted)" if not blocking(new[uid][0]) else (
                "same" if blocking(new[uid][0]) == blocking(old[uid][0]) else "reasons changed")
        else:
            s = "same"
            for k in range(len(r["drafts"] or [])):
                if blocking(old[uid][k]) and not blocking(new[uid][k]):
                    s = "to semantic QE (deterministic layer now clean)"
                    break
            if s == "same" and r["drafts"] and blocking(new[uid][-1]) != blocking(old[uid][-1]):
                s = "reasons changed"
        status[s] += 1
        units[s].append(f"{uid} ({kinds[uid]})")
    passed = qe["passed"] / max(1, qe["passed"] + qe["failed"])
    gone = status["gone (rule output accepted)"]
    to_qe = status["to semantic QE (deterministic layer now clean)"]
    json.dump({"results": len(rows), "notes_before": len(noted), "status": dict(status),
               "issues_removed_all_drafts": dict(removed), "qe_pass_rate_in_run": round(passed, 4),
               "notes_gone_certain": gone, "notes_gone_upper_bound": gone + to_qe,
               "notes_gone_expected": round(gone + to_qe * passed, 1), "units": units},
              sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["export-termbase"]:
        export_termbase(args[1] if len(args) > 1 else "book-main")
    elif args[:1] == ["replay"] and len(args) == 4:
        replay(*args[1:4])
    elif args[:1] == ["measure"] and len(args) >= 4:
        measure(*args[1:4], cut=args[args.index("--cut") + 1] if "--cut" in args else None)
    else:
        sys.exit(__doc__)
