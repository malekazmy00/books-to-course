# B2C post-reboot recovery — 2026-10-09

Scope: existing B2C containers and operator access only. No AI Company operations.

## Confirmed infrastructure

- Dedicated VHDX mounted read/write at `/mnt/wsl/b2cdata`, `/dev/sde`, ext4; marker and model store verified by `scripts/data-disk.sh check`.
- Base plus observability Compose configuration validates. Core API, Temporal, PostgreSQL, MinIO, Redis, model/OCR, audio renderer, and observability containers are running. Existing startup task last result is 0.
- Tailscale Windows service is Running/Automatic, backend is Running/Online. Persistent Funnel is `https://desktop-3khe91s.tail7385b1.ts.net` proxying only loopback 8100.
- HTTPS public `/health` returns 200. Protected library returns 401 without a credential and 200 with the existing in-memory credential; book-main remains present. No credentials were printed or stored by these checks.

## Model boot ordering fix

Fasih, EmbeddingGemma, and BGE had exited with OCI bind-mount errors because Docker attempted startup before the data disk was attached. Existing containers were started after mount verification; all three reached healthy.

`scripts/restore-model-services.sh` now recovers only these existing B2C-owned containers when their stopped state reports a boot mount failure. It preserves manually stopped services and historical/experimental containers. `scripts/windows/start-b2c-stack.ps1` runs this helper after normal Compose startup. The installed ProgramData copy was updated; its prior copy is retained. Shell syntax, PowerShell parsing, B2C ownership checks, and an idempotent live invocation passed. This verifies the current recovery path; a further real reboot has not been performed.

## Translation recovery and checkpoint-tail defect

Appendix translation is completed at 888/888; OCR/outline/index/QA remain completed. Book-body translation had a pending attempt owned by a removed pre-reboot container, with no recent heartbeat and 613/776 previously reported successful segments. The activity has no heartbeat timeout, so it did not quickly recover automatically. A retryable failure was reported for that exact verified dead activity, retaining the same workflow/run and all checkpoint objects.

This exposed a separate recovery defect: the worker discovered its own checkpoint objects only up to the nominal four-segment batch count. Actual page/character-bound batches can have larger indexes, so the restarted activity omitted saved tail objects and could issue duplicate generation requests. Source now discovers the entire exact fingerprint prefix with paginated S3 listing, validates each object's source/model/fingerprint/index, and retains existing completeness checks. Regression cases include batch 1001 on the next listing page and malformed pagination. Focused checkpoint/resume/translation and reboot-owner guard suite: **25 passed**.

A thin generation-worker image changes only the translation orchestration module, based on the previously deployed image. Final candidate digest `sha256:7f7030ddebd99fab260829274e5bdbaf208be1ecde35b17efad7857d619610da`; original image retained as `b2c-generation-worker:before-checkpoint-recovery-20261009`. Only generation-worker was replaced. The retired attempt was failed explicitly to dispatch its retry to the fixed worker, retaining workflow `b2c-translate-c78aa00d3c69e1f54f7d3c94bdd477dcae5e20a1b0e61574759577ecc3aeb7f3` and run `01a11f0e-bfa8-793a-a475-b05420320552`. No source book, database, checkpoint, accepted question, or future-course outline policy was replaced.

## Follow-up

Record the recovered checkpoint count and new saved progress after the fixed activity starts. A background activity heartbeat/timeout policy still needs a separately validated H0 change for prompt automatic recovery of a worker process lost during a future restart. Do not interpret Temporal RUNNING alone as useful progress.

## Owner-requested verification before further inference

The generation worker was paused while validating recovery against the real book. Its immutable unit view contained 776 source units, 614 accepted saved units, 619 physically checkpointed units, five suspect units, and 162 units requiring generation or correction. An isolated internal-network routing probe used a stub gateway and the actual source/checkpoint IDs: zero accepted saved units were submitted again; exactly the 162 missing/suspect units were selected; progress was monotonic. It performed zero external inference requests and zero artifact writes. Synthetic outputs from this probe were not persisted or adopted.

The generation engine now seeds its accepted set with all validated resumed units before visiting the first batch, so the live counter no longer falls back to the number of earlier batches traversed. Tests cover a saved tail with a missing first batch and fully saved books requiring no model requests.

The installed startup routine also invokes `scripts/recover-lost-b2c-activities.sh`. It supplies only B2C container hostname/start-time metadata to a bounded API-container recovery script. The script restricts itself to active B2C QA/translation activities; it preserves a live worker and unrecognized/native worker identities. Only a Docker worker proven removed or restarted after the pending activity/last heartbeat can be recovered. Its scoped Temporal reset preserves heartbeat/checkpoint identities and prevents repeated infrastructure recovery from exhausting content retries. An idempotent live invocation recovered zero activities while the paused current worker was still present. After replacing that exact worker with the tested two-module image, the recovery helper repaired one retired activity without changing the workflow/run or stored content.

This fixes the reboot startup path. Automatic detection of a worker lost outside the startup routine is still a separate heartbeat/watchdog concern; no claim that all process-loss scenarios are covered follows. No further reboot was simulated.

Runtime verification at 2026-10-09 10:46 UTC (13:46 Cairo): fixed worker resumed the same workflow/run with 614/776 accepted completed segments and a fresh checkpoint; progress_state=active. No worker is left paused. API/Fasih/BGE/EmbeddingGemma are healthy. Appendix remains 888/888 completed.

## Public APK connectivity recovery — 2026-10-09

The local API and MagicDNS path returned HTTP 200, but this did not prove public access: host DNS resolved to the private tailnet IP. Forced public-DNS address tests found the default HTTPS port 443 stalling during connection/TLS. Refreshing Funnel and rebinding did not fix that path. The same persistent background Funnel proxy on HTTPS 8443 worked instead.

Public APK base URL: `https://desktop-3khe91s.tail7385b1.ts.net:8443`. Keep the existing bearer credential unchanged. Public health returned HTTP 200 from Windows; authenticated library checks from WSL, forcing each of the three public ingress addresses, all returned HTTP 200. No credential or library payload was logged. Keep port 443 configured for existing clients, but its public path remains faulty; clients must select 8443 for the verified working path. This is a public connectivity workaround, not proof of a repaired upstream 443 service. The background Funnel configuration is persistent; another full reboot has not been performed.

## Translation partial-result rollout

At 2026-10-09T12:22:38.207240+00:00 the fixed generation-worker and API thin images were deployed after confirming zero running B2C translation/QA workflows. Baseline runtime SHA256 values matched the isolated staged manifest; originals retained under :before-partial-results-20261009. Only generation.py, orchestration translation.py and API translation routes changed. A focused independent rerun passed 16 tests (the agent's full focused set passed 39). Source and numeric ordered equality/completeness checks remain strict. Exact raw number responses can pass without placeholder markers; mixed/wrong numbers still fail. Exhausted units return a durable partial artifact after the remaining units are processed rather than failing the whole completed batch traversal.

Post-rollout health is OK, completed appendix remains 888/888, body has 627 validated saved /776, 5 suspect and149 retry units. The original FAILED workflow exposes a valid resume_request. No new translation was queued: the owner will trigger the existing APK button "استئناف المقاطع غير المحفوظة" under book-body translation to validate the UI path. APK changes/failover build are deferred by owner. Known subsequent UI gap: this APK currently enables retry only for FAILED/other failed terminal workflows or manually corrected COMPLETED workflows; a newly persisted partial COMPLETED result needs explicit partial-aware UI handling in the next APK. No claim of that future UI support yet.

## Explicit translation retry cache isolation — 2026-10-09

APK-triggered resume 465f7ce35249e8171ab56fabb0b50ecad8bc536649c5a6674c5841f76a303f5e committed a partial result:629/776 accepted,147 outstanding. All627 previously valid units remain present; two additional units were saved. Active zero at result_committed is terminal, not a stuck activity.

The gateway caches schema-valid output before translation numeric/completeness checks. The previous translation prompt version was shared across workflows, allowing explicit retries to replay rejected responses. A read-only diagnostic on six failed source units reconstructed v4 request keys:15 cache responses were returned, showing7 numeric-placeholder failures,5 numeric-sequence mismatches and3 content-omission flags (response-level counts, not a corpus-wide error distribution; some responses had multiple items). No external inference or artifact writes occurred; temporary skip placeholders were in-memory only and never adopted.

Explicit workflow request namespaces now use phase7-translation-v5:workflow-{workflow_id}. A new owner-triggered resume obtains fresh responses for remaining units while saved valid checkpoints still avoid requests; same-workflow restart keeps its own cache.16 focused tests passed, including real activity calls proving disjoint request versions between failed/partial parent and explicit child, only missing IDs submitted and distinct partial/complete artifacts. A thin worker image retry-cache-scope-20261009 was built; publish only after verifying zero running generation workflows. No new translation workflow is started automatically; owner requested UI-button testing. The current APK's handling of COMPLETED partial results still needs its separately deferred partial-aware retry-button change.
