# HY translation parallelism check — 2026-10-14

## Change
- Added a second local HY-MT2 serving container and an HAProxy backend using least-connections routing.
- Added a second generation worker polling the same durable `hy-translation` Temporal queue. The original worker remains running.
- The model server serializes requests inside each process, so parallel workers are paired with separate model processes rather than competing on one lock.

## Resource evidence
- WSL: 56 logical CPUs, 98 GiB RAM; about 59 GiB available after both models loaded.
- Each HY process: 16 GiB memory limit, using about 7.1–7.3 GiB; CPU usage about 3.9–4.0 cores per instance.
- HAProxy health probe returned HTTP 200 from both backend instances; its logs show completed POSTs routed to both backends (one ~312 s, one ~73 s).
- Both translation workflows reported active work simultaneously on different batches: book 1 main batch 224 and book 2 main batch 30.

## Validation
- `docker compose ... config -q`: PASS.
- Python syntax compilation of orchestration worker source: PASS.
- Both model services healthy; proxy and second generation worker running.
- No inference output or credentials were copied into this report.

## Current limitation
At the last API snapshot (12:20 UTC), durable saved counts were book 1 main 204/776 and book 2 main 74/2620, unchanged since shortly before the second lane started; both had live batches and zero suspect segments. Book 1 was on a deferred/retry batch and book 2 on attempt 2, so parallelism is operational but a sustained increase in saved units is not yet proven. Continue checking checkpoints and quality retries before raising concurrency further.

During startup, Compose recreated the primary HY container once; the in-flight request was interrupted, while already committed checkpoints remained intact and the durable workflow resumed. No database, artifact store, or saved translation was replaced.


## Follow-up monitor (2026-10-14 14:02 UTC)

- Live checkpoints: book 1 body 212/776, active on batch 232 retry attempt 2; book 1 appendix 131/888 queued; book 2 body 94/2620, active on batch 40 retry attempt 2; book 2 appendix 644/644 saved although Temporal still labels its workflow RUNNING. No suspect units were reported.
- The latest reported validation reason for both active bodies is malformed_numeric_placeholder; the workers have moved into attempt 2, which receives the segment-scoped feedback. A successful post-retry acceptance is not yet visible, so the live effectiveness remains under observation.
- Both HY servers were each using about 4 CPU cores and 7.2 GiB RAM; WSL had about 58 GiB available. Both workers and both models were healthy. No proxy 5xx or worker traceback/timeout appeared in the last 20-minute log sample; latest completed POST in that sample returned 200.
- Focused retry tests: tests/unit/translation/test_hy_retry_prompt.py and tests/unit/translation/test_generation_adaptive.py - 83 passed. These prove prompt construction and segment isolation under unit tests, not live quality acceptance.
- The book2course heartbeat is temporarily scheduled every 30 minutes. It will verify retry acceptance and, only if safe, add two more model/worker lanes without recreating the existing live services; otherwise it keeps two lanes and records why. Return to five-hour cadence after an hour of stable four-lane operation.


## Four-worker expansion and isolated CPU canary (2026-10-14, UTC)

- Added HY model servers 3 and 4 with separate workers 3 and 4, using image terminology-repair-20261013; existing worker/model containers were not recreated. Both new servers reached healthy and both workers started.
- Live Temporal snapshot: three translation activities were running (book 1 body, book 2 body, book 1 appendix); worker 3 had no fourth workflow activity to consume. Saved progress moved book 1 appendix 148 to 154 and book 1 body 215 to 216 during the 14:55:18 to 14:58:26 UTC measurement window. Book 2 body remained 107/2620 while its activity heartbeat remained fresh; deferred units 11 with numeric_placeholder_order_mismatch. Book 1 body retry reason remained malformed_numeric_placeholder.
- At that window, models 1/2/4 had 11 completed chat-completion requests total; model 3 had none. Host had 56 CPUs, about 43 GiB available RAM, about 72 to 75 percent CPU idle, and no meaningful swap-in/out. Model 4 had limited quota throttling; models 1/2 did not. Slow body progress is entangled with validation retries, not evidence of host-wide resource exhaustion.
- Isolated resource canary: only idle hy-translation-3 was recreated at 8 Torch threads / 8 CPU quota; it returned healthy, uses the same read-only model volume and 12 GiB memory limit, and did not interrupt an active workflow. Its worker remains ready, but no fourth translation workflow exists yet, so an 8-thread speedup has not been measured. Do not raise threads on active servers until a comparable request-completion and acceptance-rate measurement can be made.


## Follow-up after 8-thread idle-lane startup (15:05 UTC)

- The book 2 body had a ~9 minute gap after a successful model HTTP 200, with its worker event loop waiting and no new request; without resetting or replaying the activity, it resumed and committed 107 to 109, then reported a fresh heartbeat/checkpoint. The book 1 appendix moved 154 to 164 and the book 1 body remained 216 with fresh heartbeat. This confirms the long gap was not a dead activity; avoid forced reset.
- The 8-thread/8-CPU model 3 canary is healthy with no OOM and about 43 GiB free RAM. It has not yet received an activity because only three translation workflows are active. There is not yet comparable inference data to claim speedup; keep the other live model servers at their current settings.
