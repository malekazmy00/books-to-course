# Translation timeout recovery — 2026-10-23

## Scope and live evidence

Book2Course only. No AI Company services were inspected or changed.

At 2026-10-23 12:18 UTC, the authenticated units endpoint returned 2,620 units: 2,530 translated and 90 untranslated. The persisted accepted set still matches 2,530 checkpointed IDs. Failure classes remain 52 provider timeouts/unavailability, 23 numeric-token mismatches, 10 numeric-placeholder ordering failures, 4 unexpected target closing brackets, and 1 missing numeric placeholder. All stored work remains intact.

At 12:18 UTC, Temporal listed only one running workflow (MP3); the recorded translation workflow ID was not found, though the authenticated units endpoint could still reconstruct its source/checkpoints from S3. After confirming the 2,530 checkpoints and no active translation workflow, the two orphaned model requests were canceled by replacing workers2/3, restarting HAProxy with the updated timeout, then replacing HY2/HY3. The MP3 worker/workflow was not touched.

## Source changes prepared

- Mark HTTP 408/500/502/504 and HTTPX read/write timeouts as ambiguous outcomes, and do not replay the identical request at the gateway layer.
- In translation batching, stop retries when the upstream outcome is ambiguous.
- In the HY adapter, cap generated tokens at min(request limit, 3 × source tokens + 128, with a 256-token floor) and stop Transformers generation when the client disconnects. The previous endpoint kept generating after HAProxy returned 504 and gave every short segment the full 8,192-token budget.
- Live confirmation: worker2 logged HTTP 504 at 12:01:14 UTC after exactly 1,800 seconds; the source progress stayed 2,530/2,620. Inference connections remained open to HY2 and HY3 with high CPU, confirming that the old HY server does not stop generation on client disconnect.
- When that failure is deferred, go directly to bounded adaptive splitting instead of retrying the entire original segment; valid saved translations continue to be adopted and checkpointed.
- Record upstream_timeout_ambiguous and the actual number of attempts in per-segment failure detail.
- Reduce the future HY request and HAProxy timeout from 30 minutes to 15 minutes. This is configuration-only until a safe rollout.

## Verification

Focused suites passed: 169 tests across the LLM gateway, adaptive translation, translation resume, and HY retry prompt suites. The new regressions prove that a 504/read timeout is not replayed verbatim and that an ambiguous timeout splits a long source segment while returning a valid complete translation.

## Rollout and canary status

Built `b2c-hy-translation:timeout-recovery-20261023` and `b2c-hy-worker:timeout-recovery-20261023`. After validating Compose configuration, deployed all four HY model servers and all four generation workers with `--no-deps --no-build`, and restarted only the HY proxy to load its 900-second timeout config. The four model servers report healthy on the new image; the four worker containers are running on the new image. Orphan generation-worker containers were not removed.

HY4 smoke translation returned HTTP 200 in 13.3 seconds and rendered “Intelligence” as “استخبارات” in context. A synthetic client-cancel test timed out client-side at 1.2 seconds; after cancellation, HY4 CPU returned to near idle within about 15 seconds, confirming the disconnect stop criterion. That large synthetic input temporarily raised HY4 memory to 11.1 GiB of 12 GiB; the idle server was restarted after the test and is reloading, so concurrency must remain conservative.

The authenticated units endpoint confirmed 2,530/2,620 saved, 90 missing, and zero suspect saved segments. A new resume workflow `b2c-translate-be560ad8355b4153be57acfb0bb6392fc909ae2eabddcf5551ad46bdc8d30bde` is RUNNING and inherited those 2,530 checkpoints. At 12:53 UTC it had advanced through batch 269; 1 unit was active and 2 units were deferred after numeric validation (one `numeric_placeholder_order_mismatch`, one `numeric_token_mismatch`), each with feedback version 1. These are not final failures; the bounded adaptive-split recovery still follows the initial pass. No saved inference was replayed. A 100% accepted result is not claimed until Temporal completes and unit inspection confirms 2,620/2,620, zero missing, and zero suspect segments.
## Live follow-up — 2026-10-23 13:31 UTC

The authenticated translation status endpoint reports RUNNING at batch 553/804, current unit book-70c88ae9b2ba9ed6f61e813c:366:22, with a fresh checkpoint (44 seconds old at the read). The accepted durable set remains 2,530/2,620 and has zero suspect segments. There are 15 provisional deferred units so far; the set includes long units with provider truncation and units failing exact numeric/marker validation. The 90 missing count also includes source units not yet visited in this pass, so it is not a final failure count.

The status record exposes each affected unit ID, validation/provider category, reason, attempt count, and feedback version. Current validation failures use feedback version 1 scoped to the affected segment; provider truncations go to bounded recovery without replaying a saved segment. The final retry/adaptive stage has not completed, so no final per-unit judgment is made yet.

The short OCR fragment 'vanguard i sm' is confirmed by adjacent source text as 'vanguardism'. A specialized term-only prompt produced a misleading gloss and was rejected. The prompt now normalizes only the source spelling and retains the regular contextual translation instructions. HY4 alone was rebuilt as b2c-hy-translation:ocr-fragment-normalized-20261023; it is healthy. A direct canary returned HTTP 200, finish_reason=stop, and a concise Arabic string, but this single sample is not considered a validated semantic translation. HY1–HY3 remain on the timeout-recovery image until a safe rollout point.

Latest focused checks after the OCR-prompt change: 174 tests passed across gateway, adaptive translation, resume, strict-gate, and HY retry prompt suites; ruff check --select F passed. Compose config validated with all declared profiles enabled. The live translation request was left running. No final 100% claim is made.

## Live follow-up - 2026-10-23 13:51 UTC

A subsequent authenticated read shows the same resume workflow RUNNING at batch 621/804, with the current unit on page 375. Checkpoints remain fresh (57 seconds at the read). The accepted checkpoint count is still 2,530/2,620, zero suspect; 20 deferred units are provisional, and the total remaining also contains later units not yet visited. No checkpointed output was replayed.

Added compact deterministic validation evidence to per-unit failure records: expected/draft numeric token sequences and protected marker lists, with source excerpts omitted. This will let the next failure report identify the actual token delta for that segment instead of only reporting the general validator reason. The additional regression verifies that evidence is retained without exposing source excerpts. Current focused suite: 175 passed; changed-file F lint: passed.

Built worker image b2c-hy-worker:diagnostic-timeout-recovery-20261023 (about 1.98 GB). The new failure-record regression passed inside the image with networking disabled. This image is staged only; live workers remain untouched while the translation workflow is active. Compose overlay now points to the staged worker and OCR-normalized server tags, and config validation must be repeated before a later safe rollout.

The HY4 OCR-normalized canary is diagnostic only; it completed without truncation but is not a semantic gold evaluation. HY1-HY3 remain on the timeout-recovery server image. No 100% completion claim is made.


## Live follow-up — 2026-10-23 14:00 UTC

The body translation workflow remains RUNNING at batch 673/804, with a live unit on page 384. The durable set remains 2,530/2,620; the 90 missing are still provisional because the pass and deferred recovery are unfinished. The latest checkpoint age was about 2.5 minutes while HY1 was consuming ~393% CPU, so this is an active model call; no worker/model was restarted and no saved inference was replayed.

The failure review found bibliography fragments receiving neighboring page context, which can inject unrelated numbers into citation text and trigger numeric validators. Bibliographic/context isolation and an exact-source preservation path were added for recognized short citations only, after model retries fail; ordinary content still follows normal translation/splitting. A regression covers Ibid, author/title citations, “Cited in…”, page-context isolation, and retry exhaustion. Focused suite: 181 passed; changed-file Ruff checks passed. The broader units endpoint returned HTTP 200 in API logs but took over 15 seconds for the client to receive/parse, so it was not polled repeatedly while the API was busy. These source changes are not deployed to live workers yet; rollout waits for a safe request boundary.


## Live follow-up — 2026-10-23 15:15 UTC

The workflow advanced through batch 776/804 after a roughly 15-minute interval with active HY inference. It is at page 409 with four active segments and 43 provisional deferred units. Eight of the current deferred units are tagged upstream_timeout_ambiguous; other reasons include numeric mismatch/order, truncation, and bracket validation. This confirms slow/ambiguous inference remains a live issue, not an idle workflow. The saved set still reads 2,530/2,620; no checkpointed output was replayed. Keep all active model/worker containers running until their requests finish.

The page-407 timeout group consists of four distinct segment IDs, each recorded as upstream_timeout_ambiguous; exact original text and lengths remain pending a bounded unit read after the current pass, because the full units endpoint is costly under load. The new per-unit diagnostic image and bibliography policy remain staged, not deployed. Do not call these 43 final failures until deferred recovery is complete.
