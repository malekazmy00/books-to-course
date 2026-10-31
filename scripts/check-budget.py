#!/usr/bin/env python3
"""Rule 8 gate: the services `make up` would start must fit the shared-server budget.

Reads `docker compose config --format json` on stdin. Fails when any service lacks
mem_limit / cpus / cpu_shares, or when the summed limits exceed the budget
(B2C_BUDGET_MEM_GIB, B2C_BUDGET_CPUS; defaults from CLAUDE.md rule 8).

With `--running` it reads `docker inspect` output of the running b2c containers instead
(`make budget-live`), so containers started from overlays outside the boot path count too.
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


def rows_from_config(services: dict) -> tuple[list, list]:
    missing, rows = [], []
    for name, svc in sorted(services.items()):
        missing += [f"{name}: {key}" for key in ("mem_limit", "cpus", "cpu_shares") if key not in svc]
        rows.append((name, gib(svc.get("mem_limit", 0)), float(svc.get("cpus") or 0)))
    return rows, missing


def rows_from_inspect(containers: list) -> tuple[list, list]:
    """Docker reports an unset limit as 0 (Memory, NanoCpus, CpuShares)."""
    missing, rows = [], []
    for c in sorted(containers, key=lambda c: c["Name"]):
        name, host = c["Name"].lstrip("/"), c["HostConfig"]
        for key, field in (("mem_limit", "Memory"), ("cpus", "NanoCpus"), ("cpu_shares", "CpuShares")):
            if not host.get(field):
                missing.append(f"{name}: {key}")
        rows.append((name, (host.get("Memory") or 0) / 2**30, (host.get("NanoCpus") or 0) / 1e9))
    return rows, missing


def main() -> int:
    budget_mem = float(os.getenv("B2C_BUDGET_MEM_GIB", "85"))
    budget_cpus = float(os.getenv("B2C_BUDGET_CPUS", "40"))
    running = "--running" in sys.argv[1:]
    data = json.load(sys.stdin)
    rows, missing = rows_from_inspect(data) if running else rows_from_config(data["services"])
    unit = "running containers" if running else "services"
    mem = sum(r[1] for r in rows)
    cpus = sum(r[2] for r in rows)
    for name, m, c in sorted(rows, key=lambda r: -r[1]):
        print(f"  {name:32} {m:7.2f} GiB {c:6.2f} cpus")
    print(f"total: {mem:.2f}/{budget_mem:g} GiB, {cpus:.2f}/{budget_cpus:g} cpus, {len(rows)} {unit}")
    ok = True
    if missing:
        print("ERROR: services without limits:\n  " + "\n  ".join(missing), file=sys.stderr)
        ok = False
    if mem > budget_mem or cpus > budget_cpus:
        print("ERROR: declared limits exceed the b2c budget (CLAUDE.md rule 8); "
              "start fewer heavy profiles (PROFILES=...)" + ("; stop containers outside the budget" if running else ""),
              file=sys.stderr)
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
