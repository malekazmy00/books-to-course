## Phase 0 — interim status (STOPPED, awaiting owner decision) — 2026-09-27 UTC

The gate has **not** been evaluated. Work stopped per the owner's rule "stop and report instead of
working around" after an impact on other projects was observed. b2c stack is **down** (volumes kept).

### Done and verified
| Item | Evidence |
|---|---|
| Repo at `/root/projects/book2course`, git history per step | `git log` |
| uv 0.12.19 (sha256 verified) + Python 3.12.14; uv workspace, 13 packages (docs/01 §10) | `pyproject.toml`, `uv.lock` |
| `packages/core` (stage_key re-export, ArtifactStore, ProvRecorder, Settings, JSON logging) | 17 unit tests pass |
| `packages/llm_gateway` (local-only, PG cache, json_schema, 2 retries → InvalidLLMOutput, GenAI spans) | TDD red captured (`phase-0-evidence/tdd-red-llm_gateway.txt`), then 22 pass |
| `packages/orchestration` (HelloPipeline, queues light/heavy=1/io, RetryPolicy per docs/02 §1.3) | TDD red captured, then 5 pass |
| Contract tests T-C-01..08 unchanged | 8 pass (in the 25/25 core+contract run) |
| import-linter, 6 contracts | 6 kept, 0 broken (negative control script written, not yet run) |
| All images pulled and pinned tag+digest | `phase-0-evidence/image-digests.txt` |
| Temporal 1.32.0 on the shared postgres: schema + namespace one-shots succeeded, server healthy, ~101 MiB idle | `phase-0-evidence/io-pressure-investigation.txt` |
| Langfuse v4.46.0 web + worker came up healthy on shared postgres + clickhouse + redis + minio | same file |

### Incident (caused by this work)
1. I set grafana 192m, otel-collector-contrib 256m, cadvisor 192m. Measured afterwards: grafana needs
   ~230 MiB (39 anon + 185 mapped binary); contrib binary ~500 MB. The cgroups thrashed page cache:
   grafana re-read **11.3 GB**, otel-collector 5.7 GB, cadvisor 3.6 GB (+ its disk-usage crawl of every
   image layer on the host). Host `/proc/pressure/io` full avg ≈ **56–63%** for ~15 min (00:40–00:52Z).
2. The host's `healthcheck.sh` (other project) logged `failed=[searxng]` from 17:47 local (00:47Z) and
   **recreated `ai-company-web-access-searxng` (00:56:21Z) and `ai-company-web-access-firecrawl-api`
   (00:56:42Z)**. I ran no command against them; the recreation was done by their own auto-repair,
   very likely triggered by the I/O storm. → **T-P0-05 = FAIL** for this run
   (`phase-0-evidence/t_p0_05_external_recreation_events.txt`).
3. Fixes already applied in compose (not yet re-validated): collector → core distribution
   (measured 17 MiB), grafana 384m, cadvisor 256m with `disk,diskIO` metrics disabled.

### Open problem needing a decision: disk I/O is not in the D-007 budget
Measured `/proc/pressure/io` (full, avg10):
- b2c **down** (other projects only): **~3%**
- b2c **up, idle, after the fixes**: **~12–19%**

Cause: the WSL ext4 disk has slow fsync (`pg_test_fsync`: fdatasync ≈ 37 ms/op ≈ 27 ops/s) and one
journal shared by every container; postgres/clickhouse/redis/grafana-sqlite commits queue behind each
other (seen: infisical-redis waiting in `jbd2_log_wait_commit`).

### Budget note (docs/04 §2)
otel-collector/prometheus/grafana/cadvisor are not in the docs/04 table. With measured sizes
(128m + 384m + 384m + 256m ≈ 1.1 GiB) the **full-plan** total becomes ≈ 60.1 GiB (> 60).
Phase 0 alone is ≈ 43 GiB, so this only matters for later phases — but the table needs an owner decision.
