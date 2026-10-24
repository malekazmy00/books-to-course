# External generation recovery — 2026-10-08

## Verified before migration
- Old QA workflow repeatedly reached its hardcoded one-hour activity timeout (attempt472 observed).
- Old deployed worker lacked the scope checkpoint implementation present in source.
- Nine existing final scope checkpoints contain45 accepted questions from qwen3-coder-30b. No final artifact containing the earlier255 progress count was verified.
- Original QA and translation inputs were preserved in2026-10-08-generation-migration-inputs.json; no credentials included.
- DeepSeek deepseek/deepseek-v4-flash-0731 live structured-output canary: valid,6.49seconds,98input/37output tokens, reported cost$0.000021594. This is a connectivity/schema canary, not a content-quality evaluation.
- Unit+contract suite419passed after recovery changes; additional failed-content API regression included in21passing API upload/status tests.

## Implemented; operational verification pending
- Source/config-validated paginated QA recovery with explicit model-switch opt-in, original question provenance, partial accepted batches.
- Bounded QA/translation retry(max3); activity24h, overall48h. These remain finite execution limits, not deadlines that erase accepted results.
- Dedicated external-generation queue, worker concurrency1, protected provider key file.
- API routes new QA/translation to configured generation queue; failed content cannot be reported as success merely because Temporal completed.
- Broad hardening proposal remains a proposal in docs/proposals/2026-10-08-hardening-plan.md.

Migration outcome will be appended only after observed runtime evidence.

## Deployment and live evidence (04:20 UTC)
- Read-only preflight recovered9final scopes/45questions against397persisted indexed chunks; preserved local question model provenance; zero provider calls or writes during validation.
- Built generation-worker and API from current sources, then deployed only those services. Exact B2C data mount verified; API authentication remains enabled.
- Generation worker started on external-generation using deepseek/deepseek-v4-flash-0731.
- Restarted the two repeatedly failing workflow executions with the same workflow IDs, retained original inputs and archived old progress records. No B2C processing worker was stopped or restarted; AI Company untouched.
- QA new run01a119bd-6ee3-7b98-97fc-9f636c5d9cda, attempt1; runtime progress45accepted/9successful/0failed/102scopes; new OpenRouter inference returnedHTTP200.
- Translation new run01a119bd-7005-7925-b447-2212c76fb0b5, queued behind QA because dedicated worker concurrency is1.
- This proves migration/start/recovery, not final completion. Full-book result and translation remain running/queued respectively.

## First new saved result (04:24:37 UTC)
Runtime QA reached50accepted questions/10successful scopes, with a new DeepSeek final checkpoint containing5questions for m08-chapter-1-introduction-and-overv.l01. Generation plus validation for this actual lesson took about135seconds. One heading-only Preface scope failed validation after3generation attempts; it was not counted as successful. Review finding R15 confirms the approved outline isolated page29's Preface heading while substantive text is present in subsequent pages under a second normalized-equivalent heading. This is an outline/content-eligibility issue, not missing OCR. Full course remains in progress; final success is not claimed.

## Follow-up after owner observed apparent retry (04:31 UTC)
Confirmed the external workflow did not reset: same workflow ID, current run activity attempt1; progress is55accepted questions,11successful scopes,1failed title-only scope/12processed, next current scope13 of102. The original45 checkpoints remain readable and the new scope checkpoint remains saved.
Diagnosis: the old HEAVY worker continued its pre-migration synchronous activity after workflow termination and emitted local llama.cpp HTTP200 calls (through04:29 UTC). Temporal termination closed its workflow but did not stop the already-running activity thread. The current external run is separate and continues correctly. Do not infer provider checkpoint loss from a client showing retry; correlate with activity attempt and S3 accepted count. Operational follow-up: drain that orphaned old activity only after confirming no LIGHT/IO tasks are pending, without affecting the external queue; add bounded cancellation/worker-drain protocol before any future model migration.
After R15 fix, focused outline tests5 passed; full unit+contract suite422 passed. This source fix is not deployed while QA is active.

## Five-hour scheduled check and H0 repair preparation — 2026-10-08 09:11–09:18 UTC
- QA remains RUNNING on external-generation, activity attempt1. It moved across chapters/scopes and did not reset: 236 accepted questions/45 successful scopes/11 failed scopes/56 processed of102, with the prior45 retained. Current scope is m17 chapter9; progress updated09:17UTC.
- At09:11UTC, OpenRouter key meter was $0.578810425 used and $7.421189575 remaining of the $8 key limit. At09:17UTC it was $0.590140519 used and $7.409859481 remaining, +$0.011330094 since the preceding sample. This is key-level usage and may include other use of the same key. The logged token cache through09:11 had126 responses,484,720 input tokens,290,529 output tokens; provider-reported response cost is lower than key-meter delta, so use the official key meter for budget monitoring.
- There are currently11 failed scopes in the workflow progress; only accepted scopes become final questions. Read failed checkpoint diagnostics: output truncation, an unknown/out-of-scope citation, or a quote not exactly present in source. These need later prompt/output-budget/content fixes; quality failures are not counted as accepted.
- Restarted only the idle legacy b2c-worker after Temporal showed no active task assigned to it; it had continued orphaned local HTTP calls after its old workflow was terminated. It re-registered after restart. External-generation worker and current QA/translation workflow IDs were not stopped/restarted; AI Company untouched. No local model calls from the legacy worker appeared in the subsequent log window.
- One real unprocessed lesson canary, with reasoning_effort=none and the existing generation+judge logic:5 generated/5 passed exact-source/automated+model judge; generation48.68s, judge3.79s,total52.46s;7,879 input/1,176 output tokens; reported cost$0.0007231. A prior accidental probe on an already-completed scope was rejected as a duplicate before acceptance and was not saved. The canary supports a future controlled low-reasoning trial, not a broad quality claim.
- Prepared, not deployed: future external worker uses reasoning_effort=none and client timeout600s; workflow/cache identity now includes the reasoning setting when nonempty so outputs are not mixed across settings. Active worker remains on its original settings.
- R15 source fix merges adjacent normalized-equivalent OCR headings only when there is no substantive body text between them. Five focused tests pass; full unit/contract suite422 passes after the fix. Compose validation passes. Import-boundary lint still reports the pre-existing pedagogy→synthesis violation. No services were rebuilt/redeployed for these further fixes.
- Translation workflow remains RUNNING/queued behind QA on the single-concurrency external generation worker; its old progress still shows8 segments. No translation work yet through the external model.
