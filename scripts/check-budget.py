#!/usr/bin/env python3
"""Rule 8 gate: the services `make up` would start must fit the shared-server budget.

Reads `docker compose config --format json` on stdin. Fails when any service lacks
mem_limit / cpus / cpu_shares, or when the summed limits exceed the budget
(B2C_BUDGET_MEM_GIB, B2C_BUDGET_CPUS; defaults from CLAUDE.md rule 8).
"""
import json
import os
import re
import sys

UNITS = {"": 1 / 2**30, "k": 1 / 2**20, "m": 1 / 2**10, "g": 1.0}


def gib(value) -> float:
    if isinstance(value, (int, float)):
        return value / 2**30
    match = re.fullmatch(r"([\d.]+)([kmg]?)b?", str(value).strip().lower())
    if not match:
        raise ValueError(f"unparseable mem_limit: {value!r}")
    return float(match[1]) * UNITS[match[2]]


def main() -> int:
    budget_mem = float(os.getenv("B2C_BUDGET_MEM_GIB", "85"))
    budget_cpus = float(os.getenv("B2C_BUDGET_CPUS", "40"))
    services = json.load(sys.stdin)["services"]
    missing, rows = [], []
    for name, svc in sorted(services.items()):
        missing += [f"{name}: {key}" for key in ("mem_limit", "cpus", "cpu_shares") if key not in svc]
        rows.append((name, gib(svc.get("mem_limit", 0)), float(svc.get("cpus") or 0)))
    mem = sum(r[1] for r in rows)
    cpus = sum(r[2] for r in rows)
    for name, m, c in sorted(rows, key=lambda r: -r[1]):
        print(f"  {name:32} {m:7.2f} GiB {c:6.2f} cpus")
    print(f"total: {mem:.2f}/{budget_mem:g} GiB, {cpus:.2f}/{budget_cpus:g} cpus, {len(rows)} services")
    ok = True
    if missing:
        print("ERROR: services without limits:\n  " + "\n  ".join(missing), file=sys.stderr)
        ok = False
    if mem > budget_mem or cpus > budget_cpus:
        print("ERROR: declared limits exceed the b2c budget (CLAUDE.md rule 8); "
              "start fewer heavy profiles (PROFILES=...)", file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
