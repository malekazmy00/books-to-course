## Phase 0 report — 2026-09-29 UTC

### Executed (verified): commands + real outputs
- `make eval PHASE=0` completed with exit 0 after fixes. Evidence is in `eval/reports/phase-0-evidence/`.
- Set up and checked the dedicated 300 GiB expandable VHDX at `F:\wsl-b2c\b2c-data.vhdx`, mounted as ext4 at `/mnt/wsl/b2cdata`; marker and service directories verified by `scripts/data-disk.sh check`.
- Installed the selected local model copy at `/mnt/wsl/b2cdata/models/sha256-1194192cf2a187eb02722edcc3f77b11d21f537048ce04b67ccf8ba78863006a`. Its 18,556,688,736-byte size and SHA-256 match the Ollama manifest; original E: store remained read-only.
- Langfuse internal DNS now uses `langfuse-internal`, avoiding an edge-network address when Phase 0 jobs query its API.
- `llama-server --help` exited 0 in the pinned image; evidence: `eval/reports/phase-0-evidence/llama-server-help.txt`.

### Not executed / assumed
- Phase 1 and later phases were not started. T-B-01 model benchmarks and extraction-quality evaluations remain pending owner acceptance of this Phase 0 gate.
- No external LLM provider or API was called; inference used the local llama.cpp service.

### Tests: passed 62 / failed 0 / skipped 0 (list IDs)
- 52 contract + unit tests passed; 5 integration tests passed.
- T-C-01..08 passed within the contract suite.
- T-P0-01 passed: 6 kept, 0 broken; negative control correctly detected the planted forbidden import.
- T-P0-02 passed: 11 Langfuse observations including a `GENERATION` span with model and GenAI attributes.
- T-P0-03 passed: workflow completed after killing the B2C worker mid-activity; activity attempt 2 completed.
- T-P0-04 passed: 42.250 GiB sum of container memory limits, under the 61 GiB D-007 limit; all containers had Memory/NanoCpus/CpuShares.
- T-P0-05 passed: all 11 running non-B2C containers kept the same IDs and StartedAt timestamps.
- V-04 passed: pinned llama.cpp help output includes the configured model, alias, context, thread, parallel, and Jinja options.

### Metrics vs thresholds
| Metric | Measured | Threshold / result |
|---|---:|---|
| Contract + unit tests | 52 passed | all pass |
| Integration tests | 5 passed | all pass |
| Compose memory limits | 42.250 GiB | <= 61 GiB, pass |
| B2C local model smoke | 90 tokens in 6.43 s, 14.00 tokens/s | smoke passed; benchmark deferred to T-B-01 |
| LLM resident memory | 14.82 GiB / 26 GiB limit | within service limit |
| I/O PSI after warm-up | full avg10 near 1–3.8% | near ~3% external-only baseline |
| External container isolation | 11 unchanged | pass |

### Versions pinned
| Component | Version / digest | Verified |
|---|---|---|
| llama.cpp server | `server-b11176`, `sha256:6257697a7f5d034b8fb499ddb07af3e250506352f94102054252a23f3b85e0af` | 2026-09-29 |
| Qwen3-Coder 30B (Ollama `qwen3-coder:30b`) | blob `sha256:1194192cf2a187eb02722edcc3f77b11d21f537048ce04b67ccf8ba78863006a` | 2026-09-29 |
| pgvector/Postgres | `0.8.6-pg17`, `sha256:cf134a767f474095eeba57e0117be8e568e011a63f33fbf252f14c9b760f8e6f` | 2026-09-29 |
| Temporal server | `1.32.0`, `sha256:c3e752127759616bb1615e0f9ba0e21635aeb5fdeb922de4f371c350955f46ae` | 2026-09-29 |
| Langfuse web / worker | `4.46.0`, pinned digests in `image-digests.txt` | 2026-09-29 |
| ClickHouse | `25.12`, pinned digest in `image-digests.txt` | 2026-09-29 |

### Open issues
- The previous Gemma4 blob fails with `expected 2131, got 720` on the pinned llama.cpp build; the matching upstream issue documents the same failure: https://github.com/ggml-org/llama.cpp/issues/27349. B2C now uses verified local Qwen3-Coder 30B instead.
- Integration pytest emitted a non-fatal cache-directory permission warning; all 5 tests passed.
- DSH Harness browser access was declined by the browser security layer and the Codex Tasks environment list was empty. No alternate access path was attempted; work continued locally under the existing B2C authorization.

### Gate decision: PASS
Phase 0 passes. Keep Phase 1 closed until the owner accepts this gate.
