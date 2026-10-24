# Canonical second-book ingestion recovery

## Observed identity and cause

- Course `b2c-demo`, canonical book `book-70c88ae9b2ba9ed6f61e813c`; file SHA-256 `70c88ae9b2ba9ed6f61e813ca80e8685773ce18245477ed38e14d46eb7632292`.
- Source PDF: 3,734,092 bytes / 415 pages. Role stays `spine`.
- Workflow `b2c-ingest-9f82296b6b3f1b0e5abae9b12cf02f56cf7745ef4ca200f43eaeb00a6fbf088b`, run `01a1225f-0fcb-7452-a1d4-06cf944fdba2`.
- Temporal reported STARTED parse activity 1 since 2026-10-09 20:33:38 UTC, last worker `1@04df0bcea97b`, no heartbeat and a 24-hour start-to-close timeout.
- The same container restarted its worker process at 20:59:24 UTC. Its worker startup log and Docker State.StartedAt agree. The original activity was orphaned, not still consuming OCR inference. No outline/index stage had been registered for the canonical source.

## Action and evidence

At 22:42:14 UTC, failed only the confirmed lost parse attempt through Temporal async-activity API with retryable `B2CWorkerLostAfterRestart`. Kept same workflow/run/activity/input and immutable source artifacts. No container restart or other workflow change.

Temporal confirmed attempt 2 started at 22:42:19 UTC on `heavy`, same workflow/run; worker CPU sample reached 98.39% and the current temporary source is the verified 415-page PDF. Parsing is active; no claim of extraction/outline/index completion yet.

The existing post-mount lost-worker recovery script excluded ingestion and indexing. Extended its allowlist to matching B2C workflow/activity pairs: ingestion parse and outline, index approved book. Existing identity/start/heartbeat proof remains mandatory. Six focused recovery tests passed, including active-worker preservation and mismatched workflow/activity rejection. This script is invoked from the existing startup helper and does not require worker deployment.

An isolated watcher (`/tmp/b2c-second-book-index-watch.py`, exec session 1844) checks this canonical ingestion. After it completes, the watcher verifies course/spine identity and absence of unexpected supplementary sources, submits existing automated structural outline approval with 500-token chunks/0.12 overlap, and starts its index. It first checks the canonical progress registry for an existing index to avoid duplicates. It exits on terminal failure or errors rather than pretending to complete. No questions, translation or audio are started by this watcher.
