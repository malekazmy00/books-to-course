# Local QA failure audit — 2026-10-10

Scope: B2C only. Read-only audit of Temporal immutable input, persisted QA progress, the approved outline and every scope checkpoint in the source chain. No workflow was started, stopped or modified by this audit.

Workflow: `b2c-qa-7ea762566d239039d7d5a4942989d5e6cab11a55bb16e9a358f1640fa7bac7a9`.

## Exact saved state

- Temporal status at capture: CANCELED. Last persisted progress: 2026-10-10T16:37:24.427561+00:00, questions phase, current scope 103, 102 processed, 62 succeeded, 40 failed, target 229 scopes.
- Source chain: original external workflow `b2c-qa-07ff671418659d205e1eed001cf2e51ef98975a78b74404cfb22bcced3fe1498`, then this local workflow. Read all checkpoints individually and merge by scope ID in source order.
- 65 persisted scopes: 62 final and 3 partial, with 322 accepted questions. The local workflow itself has 14 persisted scope checkpoints: 13 final and 1 partial. The other saved scopes are restored from the original workflow.
- The 40 failed scopes reconcile exactly as 37 processed scopes with no checkpoint plus 3 partial scopes with four saved questions each. All 62 final scopes must be skipped on resume; the 12 accepted partial questions must be retained.

## Partial failures with direct evidence

| Scope | Saved questions | Direct persisted diagnostics | Retry behavior |
|---|---:|---|---|
| `m11-cr-i-t-i-c-al-approaches.l01` | 4/5 | Exact-source evidence quote mismatch (`chunk:c63c54ff89bb156508ef`); duplicate question rejection. Inherited external partial checkpoint. | Retain four items and generate only one missing valid question, with exact citations and duplicate exclusion. |
| `m51-consc-i-ousness-of-group-and-cl-.l01` | 4/5 | Exact-source quote mismatch (`chunk:8eb8a1499dedacd0152d`). Inherited external partial checkpoint. | Retain four items and generate one missing valid question. |
| `m64-the-b-i-rth-of-new-part-i-es-of-.l01` | 4/5 | Exact-source quote mismatch (`chunk:e9854827ec1f337abe55`); validation rejected an answer adding Janusfaced-party detail unsupported by its quote. Local partial checkpoint. | Retain four items and generate one missing valid, supported question. |

These three scopes have quality/citation failures, not a demonstrated context-limit failure. Raising context alone does not repair those diagnostics.

## Empty failed scopes — individual classification

Each row below was inside the first 102 processed lesson scopes, absent from both checkpoint sources and therefore has zero durably saved accepted questions. For every row, the exact failure cause is **not recoverable from available checkpoint evidence**. A context overflow is a candidate based on the parent's runtime logs, not a scope-specific proven diagnosis. Retry the scope after fixing context handling and capture its full diagnostic checkpoint, including a zero-item failure.

| Scope ID | Saved accepted items | Cause supported by checkpoint | Retry |
|---|---:|---|---|
| `m01-from-subordination-to-revolution.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m02-from-subordination-to-revolution.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m04-gr-amsc-i-lodes-tar-of-renewal.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m26-c-i-v-i-l-soc-i-e-t-y-culture-ec.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m32-the-torturous-contradictions-of-.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m55-soc-i-al-i-s-t-sel-f-management.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m62-the-cr-i-s-i-s-of-author-i-t-y.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m63-popul-ar-upr-i-s-i-ngs.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m65-organ-i-c-i-nt-el-l-ec-tual-s.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m66-conclus-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m69-the-need-for-theore-t-i-c-al-ren.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m71-the-war-of-pos-i-t-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m73-the-vanguardist-party.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m74-the-mass-democratic-party.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m75-the-organic-party.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m76-i-deolog-i-c-al-el-abor-at-i-on-.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m77-popul-ar-educ-at-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m79-engag-i-ng-the-s-tat-e.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m80-bu-i-ld-i-ng-the-soc-i-al-bloc.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m82-t-r-a-s-f-o-r-m-i-s-m-o.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m83-r-ad-i-c-al-i-z-at-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m84-conclus-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m85-the-roiling-ferment-of-revolutio.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m86-the-culmi-nat-i-ng-pha-se-s-of-t.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m88-the-war-of-manoeuvre.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m90-pa-ss-i-ve-revolut-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m91-the-h-i-s-tor-i-c-al-bloc.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m92-the-tr-ansnat-i-onal-popul-ar.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m93-the-a-scendanc-y-of-c-i-v-i-l-so.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m94-w-h-i-ther-the-s-tat-e.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m95-worldv-i-ew-culture-and-l-anguag.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m96-what-i-s-the-part-y-to-do.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m97-conclus-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m99-fundamental-s-faces-and-pha-ses.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m100-domi-nat-i-on.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m101-a-u-t-o-n-om-y.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |
| `m102-c-o-n-s-e-n-t.l01` | 0 | No diagnostic checkpoint; exact cause unproven | Generate missing scope; persist all outcomes |

## Persistence defect and limitations

At the inspected source, `save_scope_checkpoint()` in `packages/orchestration/src/b2c_orchestration/qa.py` returns when `scope_result.items` is empty. Therefore zero-item failed scopes lose their per-scope diagnostic record. The pause result reconstructs only persisted scopes, so its counts can shrink and omit empty failures. This is a diagnostics/visibility defect even though no accepted questions were lost in these empty scopes. Persist every scope result, including zero-item failed scopes; continue restoring final scopes and partial accepted items as before.

No final artifact for the canceled workflow exists via its Temporal result. The prior worker container's log history is unavailable after recreation, so this audit cannot reconstruct every prior request/reply. The last progress status says running even though Temporal says CANCELED, showing that progress must be reconciled with Temporal terminal status in the display.

## Checks and risk

- Read 14 local checkpoints and 51 predecessor checkpoints; inspected each status/items/errors and reconciled 65 unique scope IDs against the first 102 approved-outline lessons.
- Verified 62 final + 3 partial + 37 no-checkpoint = 102 processed; 3 partial + 37 no-checkpoint = 40 failures; saved question sum = 322.
- No inference or new model requests were submitted. No runtime mutation or unrelated project access occurred. This audit required no code tests because it changed only this report.
- All remaining scopes after 102, including current scope 103, still need generation. A resumed job must retain the complete source checkpoint chain, strict source fingerprints and accepted-item duplicate exclusion.


## Follow-up: first resumed scope CPU latency

Read-only follow-up on `b2c-qa-74dc0363bedbae69daed04ea82cd89206a5ddf4eda4019e6bf8296fd2ad022bd`. Persisted progress remained at `m01-from-subordination-to-revolution.l01`, 62 restored final scopes, 322 known questions (310 final +12 partial), 0 newly failed scopes. Runtime is actively processing, not an idle loop.

- llama restarted at 2026-10-10T16:50:37Z; command uses total context 32768 and two parallel slots. `/slots` confirms 16384 per slot, so the former 8192 context limit is repaired.
- First task started at server elapsed303.568s and was canceled at904.167s: 600.599s. The running gateway configuration is `llm_timeout_s=600.0`. This is strong temporal evidence of client read-timeout cancellation; no HTTP error diagnostic was yet persisted at the scope boundary.
- First prompt had6511 tokens. Logs show2048 prompt tokens in85.48s,4096 in207.20s,6144 in389.96s; effective prefill slowed from23.96 to15.76tok/s. It produced no accepted result before cancellation. A new task33 started at905.448s, later logged2048 prompt tokens in129.89s (15.77tok/s); inference is doing work on a further batch/attempt.
- Snapshot resource use: llama CPU1417%,RAM16.35GiB of50GiB. One active slot, one idle; the worker's near-zero CPU reflects waiting for inference. No evidence of RAM limit or context rejection in the current run.
- Code currently allows18000 source characters per batch, duplicates source spans in `quote_options`, includes all322 prior questions in `questions_to_avoid`, and uses up to4096 output tokens for both generation and judging. The source-character bound does not bound the complete prompt. This increases prefill latency, and the entire prior question bank grows with course size.

Practical repair for the next safe deployment: bound the complete serialized prompt using the model tokenizer; send only scope-relevant duplicate examples while keeping global deterministic duplicate rejection after generation; keep exact source quote choices; batch one/two candidates and use a smaller output budget for the judgment's small JSON. Use a local CPU timeout derived from measured prefill+decode, with periodic activity heartbeat and durable per-scope outcomes; extending timeout alone prevents cancellation but does not improve throughput. No hot change, inference replay, workflow mutation, or worker restart was performed by this follow-up. The live gateway object already holds its timeout; changing its environment now would not update it.


## Prepared source repair: bounded duplicate prompt examples

Changed `packages/pedagogy/src/b2c_pedagogy/qa_generation.py` only for prompt construction: duplicate examples are selected from the current accepted questions and prior scopes whose lesson IDs overlap the current scope. Current accepted questions have priority; the example list is bounded to24 full questions and3000 characters of serialized JSON. No question is truncated. An unrelated prior course bank contributes zero examples to a lesson prompt.

The complete global normalized question set is still supplied separately to `_generate_batch` for deterministic duplicate rejection, and the existing post-judge duplicate check remains intact. Exact source quotes, source IDs, provenance and model answer-support checks are unchanged. Thus an example omitted from the prompt cannot bypass global duplicate validation. Parent-scope inheritance remains unchanged.

Focused check: `.venv/bin/pytest tests/unit/pedagogy/test_qa_generation.py -q` →39 passed in1.61s. Added two behavior checks: a1000-question bank cannot expand the duplicate prompt beyond its count/character bounds; and a repeated question from an unrelated lesson omitted from the prompt is still rejected globally without replacing its saved original item.

Prepared source/tests only; no image build, worker restart or live workflow deployment. Limitation: this bounds the duplicate-example part, not the complete source/schema/error payload. Tokenizer-aware full-prompt budgeting, local timeout calibration, and smaller judge output budgets remain separate repairs. Some cross-scope duplicates may be generated before being rejected because only related examples are shown; validation stays strict.


## Read-only follow-up: first two failed scopes (17:31 UTC)

Temporal remains RUNNING for `b2c-qa-74dc0363bedbae69daed04ea82cd89206a5ddf4eda4019e6bf8296fd2ad022bd`. Latest persisted progress at2026-10-10T17:31:34.428279Z has current scope4 `m04-gr-amsc-i-lodes-tar-of-renewal.l01`,62 final scopes,2 failed,64 processed,322 known questions and310 final-scope accepted questions. The two newly failed scopes reconcile with `m01-from-subordination-to-revolution.l01` and `m02-from-subordination-to-revolution.l01`;scope3 was a restored final lesson.

No scope checkpoint object exists under this resumed workflow's prefix at capture. Thus neither failure has a durable diagnostic record or new saved accepted question. The running worker still uses the earlier zero-item checkpoint omission; the prepared source repair has not been deployed. Their exact final reason cannot be responsibly attributed to timeout, quote mismatch or validator rejection from these records alone. Earlier600s task cancellation is evidence of one timeout during the first scope, not proof of every subsequent attempt's outcome.

Inference is active: worker logs show successful model HTTP200 responses at17:15:23,17:18:09,17:26:19,17:29:05 and17:31:34Z. llama task2130 completed499 generated tokens at3.51tok/s;task2630 then started another prompt and logged2048 processed tokens (22% of its prompt),55.95s,36.60tok/s. A200 only proves model transport success, not QA acceptance.

Safe retry plan: allow the current workflow to reach its durable scope/end boundary; do not replay it while RUNNING. At a terminal partial/failed outcome, inspect its full `course-qa.json` diagnostics, persist zero-item results via the prepared source fix, then resume with the full checkpoint chain (original07ff,local7ea,current74dc), exact outline/index identity and the selected local model. Keep all62 final scopes and12 existing partial questions; rerun only failed/incomplete scopes. Deploy the prompt optimization and calibrated local timeout at the safe boundary before that retry. No workflow/service/runtime mutation was performed by this follow-up.


## Prepared durable zero-item failure repair

`qa_generation.py` now checkpoints every exhausted/exception-failed scope before reporting its processed progress boundary, including zero accepted items. Status,scope ID,lesson IDs,attempt count and errors survive a subsequent pause. Checkpoint-write errors propagate rather than being converted into an ordinary generation failure. Empty failed restored scopes are accepted as incomplete retry input; empty final scopes remain invalid.

`orchestration/qa.py` saves and loads zero-item failed scopes under the existing immutable checkpoint/source identity. Source-chain merging continues preferring more accepted questions and then final status, preventing an empty retry from replacing a saved valid bank. On equal rank,the later source wins, preserving updated retry diagnostics.

Focused checks: `.venv/bin/pytest tests/unit/pedagogy/test_qa_generation.py tests/unit/orchestration/test_course_qa_activity.py -q` →52 passed. Coverage includes the real activity test double saving every empty failed scope with source fingerprint and diagnostics; loading/resuming an empty failure without rerunning its saved final sibling; saving before the pause boundary; and fail-closed checkpoint-write errors. Prepared source/tests only; no live deploy/rebuild/restart or workflow mutation.


## Prepared separate judge output budget

Added `_judge_output_budget(candidate_count, caller_max_tokens)` in `qa_generation.py`: `min(caller_max_tokens,max(2048,256+768*candidate_count))`. Generation keeps its original caller ceiling unchanged. Judge batches of1–2 candidates use2048,3 use2560,while5+ retain4096 when the caller supplies4096. Larger caller ceilings still scale with batch size. The conservative reserve retains room for all required boolean/index fields and the existing500-character rationale; no schema field or quality validation changed.

`max_tokens=4096` is a request ceiling, not guaranteed actual consumption. Gateway forwards it as a parameter and separately records response `usage`; it rejects structured responses whose finish_reason is `length`. The observed llama task2130 emitted499 tokens despite its4096 ceiling. Lowering a ceiling does not accelerate a response that naturally stops earlier; prompt reduction is the primary throughput repair here.

Focused checks after this addition:60 passed in2.56s across the pedagogy and QA-activity suites. Seven budget cases cover small/large batches and caller limits; a gateway-spy test proves generation4096 versus judge2048 while retaining valid maximum-length rationale and supported-answer checks. No inference was submitted and no service was built,deployed or restarted. This is conservative source staging, not a measured live performance claim; providers using hidden reasoning tokens require evaluation before further budget reduction.


## Slot-overlap audit: tasks2630 and2773

Temporal read-only Running query returned exactly QA74dc on heavy and MP3 `b2c-mp3-57f9ded6d7b8eea8843b56866c13639d157b3bedcbb885e4301f3503e2f188a3` on audio. Course audio code has no LLM gateway call; QA generation and judgment execute serially in the same activity.

llama task2630 started at elapsed2456.436s; cancellation arrived3056.840s,600.404s later,matching the client timeout. Task2773 launched3056.687s immediately when that client wait ended. Task2630 released at3117.908s,about61s after cancellation,with134 decoded tokens. Thus the transient two-active-slot overlap is consistent with a subsequent QA batch/attempt starting while llama was still applying the canceled request's stop; it is not planned concurrent generation+judgment. There was no worker HTTP200 corresponding to2630 in the captured interval. Latest `/slots` had2630 idle and2773 processing,so there is no indefinitely orphaned2630 inference at capture.

Safe action: leave current2773 request active. Apply bounded-prompt/calibrated-timeout repairs at a durable deployment boundary; add request correlation and cancellation-delay telemetry before treating future slot overlaps as independent work. No cancellation,workflow mutation or service restart was performed.
