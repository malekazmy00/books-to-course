# CLAUDE.md — book2course

Research (non-commercial) platform that converts books into structured, Arabic-narrated courses.
Docs are in Arabic with English technical terms. Read them in this order before any work:

1. `docs/00-DECISIONS.md`  — decisions (Accepted / Deferred / Open). Do not re-decide Accepted items.
2. `docs/01-ARCHITECTURE.md` — layers, diagrams, data flow.
3. `docs/02-CONTRACTS.md` + `packages/contracts/src/b2c_contracts/models.py` — source of truth for all data.
4. `docs/03-TESTING.md` — test catalog (IDs, thresholds).
5. `docs/04-OPERATIONS.md` — resource budget, shared-server rules, observability.
6. `docs/05-PHASES.md` — phased plan with gates. **This is your work order.**

## Hard rules

1. **One phase at a time.** Only work on the phase you were assigned. Do not start phase N+1 until the
   gate of phase N passes AND evidence is written to `eval/reports/phase-N.md`.
2. **Tests first.** For the assigned phase, write the tests listed in `docs/03-TESTING.md` first
   (they must fail), then implement until they pass. Never weaken a test or threshold to make it pass;
   if a threshold looks wrong, stop and report.
3. **Contracts are law.** Any change to `packages/contracts` requires bumping `SCHEMA_VERSION`
   and updating `docs/02-CONTRACTS.md` in the same commit.
4. **Layer boundaries.** Layer packages must not import each other. They communicate only through
   `b2c_contracts` models + stored artifacts. Only `packages/orchestration` knows the order of layers.
5. **LLM access only through `packages/llm_gateway`.** No direct HTTP calls to model servers elsewhere.
6. **Local models only** in the current stage (decision D-004). Do not add cloud API providers or keys.
7. **Shared server — do not touch other projects.** Never stop, restart, reconfigure, or exec into
   containers outside compose project `b2c` (e.g. `firecrawl-*`, `ai-company-*`, `memory-qdrant`,
   `infisical-*`, `searxng`, `ollama`, `playwright`, their redis/postgres). Never run global commands
   such as `docker system prune`, `docker volume prune`, or `docker compose down` without `-p b2c`.
8. **Resource budget** (D-007, raised to 61 GiB on 2026-09-27): the project must stay within **61 GiB RAM
   and 40 logical CPUs**. Every service in compose must declare `mem_limit`, `cpus`, and `cpu_shares` per
   `docs/04-OPERATIONS.md`. A `mem_limit` must cover the measured working set incl. mapped binaries —
   undersized limits thrash the shared disk (Phase 0 incident).
   Disk I/O is shared too: b2c data lives on its own disk (D-020), never on the WSL root disk.
9. **Pin versions.** Every image, package, and model must be pinned (tag + digest or exact version, model revision/sha)
   and recorded in the versions table in `docs/04-OPERATIONS.md`.
10. **Items marked ❓ or "VERIFY"** are not facts. Verify against upstream documentation before relying on them,
    and record what you found (link + date) in the phase report.
11. **Reporting format** at the end of every task:
    - What was actually executed, with the real command output (verified).
    - What was NOT executed or only assumed (unverified).
    - Test results with counts (passed / failed / skipped) and the eval numbers vs thresholds.
    - What remains open.
    Never state a gate has passed without the report file and the numbers in it.

## Environment

- Windows host; Ubuntu runs in **WSL2**. Docker runs inside WSL.
- CPU: 2× Intel Xeon E5-2658 v4 (Broadwell-EP): 28 physical cores / 56 threads total. **AVX2 only, no AVX-512.**
- No GPU. WSL sees ~98 GiB RAM. WSL exposes a single NUMA node → **no NUMA pinning** (D-008).
- Other projects already run here and use ~7–10 GiB RAM. Keep it that way.
- Disks: C:+D: = physical disk 0 (WSL root vhdx on C:, slow fsync ≈ 37 ms/op); E: = disk 1 (Ollama models,
  `/mnt/e/ollama-models`); F: = disk 2 (b2c data VHDX, mounted at `/mnt/wsl/b2cdata`, D-020).
- Another project runs a host `healthcheck.sh` that auto-recreates its containers when they fail.
- Forbidden host ports (other projects): 3002, 4000, 6333, 6334, 8080, 8888, 11434.
  Never `wsl --shutdown` / `wsl -t`, never edit `C:\Users\User\.wslconfig`, never restart the docker service.

## Commands (created in Phase 0)

```
make up / make down            # docker compose -p b2c --env-file .env -f docker/compose.yml ... only
make test                      # all tests
make test-unit | test-contract | test-golden | test-integration | test-e2e
make eval PHASE=N              # runs the phase's eval suite, writes eval/reports/phase-N.md
make bench-llm                 # Phase 1 benchmark protocol
```
