# Translation failure review and recovery ∫w^~)ﬁt 2026-10-18

## Scope
B2C only. No AI Company services or data were changed. Existing validated translation checkpoints were retained.

## Failure analysis

### Book 1"È›y¯ßy‘ original run `b2c-translate-8737796b∫w^~)ﬁv`
The checkpoint had 768/776 accepted segments, 8 validation failures, and 26 deferred page entries (some page entries refer to partially completed batches; they are not 26 distinct failed segments). The eight failed segment reasons were:
- numeric placeholder order / missing numeric placeholder / numeric token mismatch (3)
- terminology mismatch (2)
- unavailable adaptive split on a short unit (1)
- unexpected closing bracket (1)
- table-cell placeholder mismatch (1)

Two additional checkpointed segments did not pass the source-completeness check in the new attempt, so the new run correctly started at 766 accepted segments and is revalidating the unresolved set. At the latest observation it was working on page 60, attempt 2.

### Book 2+ßuÁ‚ùÁT original run `b2c-translate-34a38982È›y¯ßy÷`
The earlier failure ledger showed repeated numeric marker duplication/missing/order issues, suspiciously short output, and bracket ordering. After resume, five new unresolved units had reached the deferred ledger by page 99:
- misordered target bracket
- missing numeric placeholder
- suspiciously short translation
- numeric token mismatch
- numeric placeholder order mismatch

The failure records include validator reason codes and the retry feedback version, confirming the model is being told the specific observed defect rather than receiving a generic"È›y¯ßy‹retrzÈ›y¯ßy› prompt.

## General repairs implemented
- Periodic Temporal activity heartbeats run while the synchronous provider call is waiting, reducing the risk of an activity timing out while its thread continues writing checkpoints.
- Deferred failures are retried one source unit at a time; each successful repair is committed immediately with its own checkpoint.
- Retry prompts carry a typed validator reason and evidence, ask for the smallest local correction, and preserve already-correct meaning/wording.
- Duplicate numeric markers are normalized by source order only when the counts prove a one-to-one mapping. Missing numeric values are never invented; strict validation still rejects them.
- Adaptive split recovery keeps its recovery label in the checkpoint.

## Verification and rollout
- Focused translation/orchestration suites: **218 passed**.
- Overlay worker image import/marker repair smoke check passed.
- Recreated only the four B2C HY translation workers with the recovery image; did not remove orphan containers or restart API, Temporal, Postgres, MinIO, or other services.
- Book 1 has a new resume workflow: `b2c-translate-eb7b4c82∫w^~)ﬁv`, RUNNING. Its latest checkpoint reported 766/776 accepted segments, page 60, attempt 2.
- Book 2 workflow `b2c-translate-34a38983∫w^~)ﬁv` was signaled to resume and is RUNNING. Its latest checkpoint reported 770/2620 accepted segments, page 99, with five unresolved units recorded for isolated retry after the remaining first-pass units are processed.
- HY model containers showed active CPU work during the latest check. Saved checkpoints are being used; no full-book inference was restarted.

## Current limitation
The retries are live and not yet complete. The worker∫w^~)ﬁus deferred-unit pass occurs after the remaining first-pass work, so Book 2∫w^~)ﬁus five unresolved units will be retried after its current pass reaches the end. Do not call either translation complete or claim zero failures until the final checkpoint lists no missing/failed units and Temporal closes successfully.
