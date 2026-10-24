## Phase 5 report — 2026-10-04

### Scope
Bounded implementation slice for Phase 5 EN enrichment/pedagogy: citation-resolution and source-role validation, lesson asset cross-reference checks, and two deterministic MCQ lint rules. No provider/model calls; no claim that Phase 3/4 gates or Phase 5 have passed.

### Executed (verified)
- pytest synthesis/pedagogy validation tests — 6 passed.
- Full unit and contract suite — 122 passed, 0 skipped.
- Import boundaries — 6 kept, 0 broken.

### Implemented
- packages/synthesis/src/b2c_synthesis/validation.py: citations must resolve against the supplied StructuredChunk set; primary sentences must cite spine chunks and supplementary sentences must cite supplementary chunks. Reports stable finding codes and locations; connective sentences may cite either role. Duplicate IDs in the supplied index are reported.
- packages/pedagogy/src/b2c_pedagogy/validation.py: validates lesson IDs and summary/glossary/question sentence references, evidence per summary point, and at least one valid question above Bloom remember. The MCQ linter flags duplicate normalized options and “all/none of the above” options.
- Unit tests cover valid and negative citation/asset/MCQ cases. Contracts were not changed.

### Limits / not executed
- These are structural/provenance validators; they do not establish semantic citation entailment, Ragas faithfulness, answerability, distractor plausibility, or overall question quality.
- Haladyna coverage is intentionally partial. T-PED-01's 30-item defective-question set and its >=90% detection target were not run.
- T-SYN-02..05: no human 30-sentence faithfulness corpus, duplicate-pair gold set, contradiction fixture, or unsupported-topic negative test.
- T-PED-02..06: no solver, prior-lesson review-quiz restriction, FSRS parity, or broader datetime test in this slice.
- No generated course output or expert review was evaluated; G2 remains open.

### Gate decision
NOT RUN / NOT PASSED. This is an implementation baseline only. Next required evidence: wire validators into the generation pipeline, add calibrated semantic/solver checks and the predeclared gold sets, then independent expert review against G2.

## Outline-driven course Q/A generation slice (2026-10-04)

Added `packages/pedagogy/src/b2c_pedagogy/qa_generation.py`. Its callable accepts the accepted `CourseOutline`, source `ParsedBook`, source-linked `StructuredChunk` set, and the project's configured/injected `LLMGateway`. It derives every lesson scope from outline block spans, every module/group scope from the outline hierarchy, and one whole-course scope; the caller does not enumerate scopes. Every emitted item retains exact chunk ID, book ID, role, physical page numbers, and the verbatim supporting quote. The 2026-10-05 source-selection correction keeps new lesson scopes within the accepted spine block span because the outline contract has only course-level supplementary-book IDs, not a per-lesson supplementary mapping. Group/course scopes may still use approved supplementary chunks.

For each requested scope it partitions all supplied chunks into bounded context batches, asks the provider for factual questions whose answers are source quotes, rejects unknown citations/quotes, repeated or answer-leaking questions, and invokes structured support/clarity validation. It regenerates only missing or rejected items within bounded regeneration rounds and returns `final` only when the exact requested count passes; otherwise that scope has `failed`, preserves any individually validated partial items for bounded recovery, and records machine-readable errors; a failed child never makes its parent final. Whole-result JSON can be persisted atomically. It does not add a review queue or mark a failed/generated result final. This is product content generation for Phase 5, separate from Phase 3 retrieval qrels.

Focused mock-gateway tests cover outline-derived lesson/module/course coverage, exact source/page/role provenance, regeneration after an invalid quote, fail-closed exhaustion, unapproved outlines, and bounded context batches plus the actual LLMGateway structured API using a mock HTTP provider: 7 passed. No live Q/A was requested from any provider, and no adapter credentials/quota were used. Full unit+contract suite: 142 passed, 0 skipped. A live provider run is still needed to validate that the configured model follows the schemas and the semantic judge is useful on actual course material; this is not a Phase 5 gate pass. The automated judge is a model-based check, not a formal proof of truth.

## Course-level generation scope hardening (2026-10-04)

Review found that lesson and module scopes were built from spine chunks resolved inside accepted lesson spans, but the whole-course scope included every supplied spine chunk. On a complete book with an outline covering only selected chapters, this could expose unrelated spine content to course-level question generation.

The course scope now includes only spine chunks resolved into at least one accepted lesson span, while retaining supplementary chunks whose book IDs are listed by the accepted outline. A focused regression case adds a third spine chunk outside all lesson spans and confirms it is excluded from the course scope. Focused verification: .venv/bin/pytest tests/unit/pedagogy/test_qa_generation.py -q — 10 passed. No Phase 4 files or services were changed. This remains a narrow scope/provenance fix; it does not close Phase 5 or validate question semantics.



## Phase 5 pipeline connection — 2026-10-04

The source-grounded Q/A generator is now connected to the B2C service path, rather than remaining a callable-only package:

- Outline approval artifacts now retain the exact spine and supplementary ParsedBook refs plus reviewer and approval timestamp. This preserves the inputs needed for later lesson enrichment.
- `POST /v1/courses/{course_id}/qa/generate` accepts an approved-outline artifact ref and its index-workflow ID. It only queues after the index workflow is complete and its result matches the same course and approved-outline hash. Counts are bounded at 1–20 questions per lesson/module/course; default counts are 5/5/10. `force_regenerate` gives a new deterministic-workflow/cache key when a fresh run is wanted.
- `GenerateCourseQAWorkflow` runs on the existing `heavy` queue. Its activity checks reviewer approval provenance, loads the exact parsed spine, fetches only approved book IDs from Postgres, and compares persisted chunk IDs/counts/roles against the completed index result before generation.
- Results (including failed-scope details) are saved immutably as `generate-course-qa/course-qa.json`. `GET /v1/course-qa/{workflow_id}` returns workflow status and, on completion, the generated result from the artifact store.
- An inferred outline keeps `requires_review=true` by contract even after a human signs off. Q/A now unlocks only when the approval artifact carries a reviewer and timestamp; it does not erase inferred provenance. Direct calls without that approval signal still fail closed.

Tests added for the full activity path with a structured mock gateway, exact indexed-chunk manifest matching, rejection of absent indexed chunks, API queueing/outline-hash binding, and approval-artifact source refs. Focused suite: `.venv/bin/pytest tests/unit/pedagogy/test_qa_generation.py tests/unit/api/test_book_upload.py tests/unit/orchestration/test_course_qa_activity.py -q` — 27 passed. Full unit+contract suite after the integrated changes: 203 passed, 0 skipped; import boundaries: 6 kept, 0 broken; Ruff on the seven changed Python files: passed.

Connection status: source code is wired through API → Temporal heavy queue → Postgres chunks + configured gateway → immutable artifact → status/result API. At the time this note was first appended the images were not built; the operational build and isolated workflow checks are recorded below. No live provider/model generation or expert review was performed. Therefore this is an integrated implementation slice, not a Phase 5 gate pass or a claim of production validation. Remaining Phase 5 evidence is still the predeclared T-SYN-02..05 and T-PED-01..06 corpus/solver/FSRS checks, plus G2 expert review.


### Operational build and isolated workflow check — 2026-10-04

- Ran `make build` (Compose build only; no `up`, restart, or service reconfiguration). API build completed; worker build installed the locked workspace, downloaded the Dockerfile-pinned Docling OCR/table assets, and completed successfully. BuildKit recorded API duration 3m03s and worker duration 17m04s.
- Built images: `b2c-api:dev` = `sha256:f0d3bfdec7ca0d9b1a1f4f1b5dd1e57b4ee811f0123b4d93ab9c39d13eb1bb75` (702,643,189 bytes); `b2c-worker:dev` = `sha256:27b3d5a1b9b88c8712cb5349dc843a1bdaa4ad657e292dd4466f0487c17502fe` (7,050,617,408 bytes).
- Ran both built images with `--rm --network none`: worker image imported `GenerateCourseQAWorkflow` and `CourseQAActivities`; API image imported successfully and registered both QA routes.
- Ran `GenerateCourseQAWorkflow` end to end against an isolated temporary Temporal test server, a synchronous activity executor, and fake artifact/chunk/gateway adapters. Result: `final`, 2/2 expected lessons covered, 4 scopes, cache miss as expected for the first run. This exercises Temporal serialization, workflow-to-activity task-queue routing, activity execution, and result propagation; it does not exercise B2C's live PostgreSQL/MinIO/provider integrations.
- Safety boundary verified by container IDs and start times: existing `b2c-api-1` and `b2c-worker-1` remain running from their previous images (`df2dc787...` / `3c0ebbab...`, started 02:49 UTC); `b2c-lightrag` remains healthy and running from its pinned image, started 14:52 UTC. Building only retagged the new image names; it did not replace running containers or touch the LightRAG service.

Exact operational boundary: current code is built and validated in isolated image/workflow contexts; the running API/worker still use older image IDs. Rollout and a live-provider QA request remain unexecuted, so an authorized B2C deployment followed by a small course-scoped end-to-end smoke is still needed before runtime integration is claimed. Do not report Phase 5/G2 as passed.


### Directly actionable T-SYN / T-PED inventory

- T-SYN-01 is enforced by `LessonSentence.citations` being non-empty and strengthened in the new Q/A path by exact in-source quote/chunk validation. This is deterministic structural evidence, not semantic entailment.
- T-PED-06 already has a UTC-aware `ArtifactRef` and `ReviewLogEntry` validator plus `test_naive_datetime_rejected`; the existing invariant is active.
- T-PED-03's lesson-asset validator checks for a question above `remember`, but the new `GroundedQA` output is not yet adapted into the contract `Question` with Bloom labels. Thus the testable validator exists but the generation path cannot claim T-PED-03.
- T-PED-01's current linter implements only duplicate-option and all/none-of-the-above checks. It is not evidence of ≥90% detection over the 30-item Haladyna set.
- T-SYN-02..05 and T-PED-02/04/05 are not safely convertible into gate evidence from the current checkout alone: the required adjudicated sentence/pair corpus, explicit contradiction/unsupported-topic cases, calibrated solver set, prior-only review-quiz route, and selected FSRS parity dependency/fixture are absent. No artificial data or thresholds were added to make these appear complete.

This leaves a concrete next implementation boundary: decide/validate the output adapter between `GroundedQA` and the versioned `Question` contract (including Bloom classification and lesson sentence IDs), then provide/approve the domain gold sets and solver/FSRS choices needed to execute the remaining gates. The current API endpoint remains a stored, source-grounded Q/A artifact service; it is not yet the full lesson enrichment/quiz product API.


### Final rebuilt images and repeat verification — 2026-10-04

- Rebuilt API and worker images after switching both Temporal clients to the explicit Pydantic data converter. Current tags: b2c-api:dev = sha256:a48b16e22aac7fcfff7df0a9d79a8a80903e2b29cb518fa33d673850caf752f8 (702,643,381 bytes); b2c-worker:dev = sha256:9e70e8424e157ffb67f0c05ee951acc39fc4ea6c7c6b3b02fe670ac6fa80ed02 (7,050,659,559 bytes). Both builds succeeded.
- Repeated offline container checks using --network none: worker imports Phase 5 workflow/activity and API; API image registers POST /v1/courses/{course_id}/qa/generate and GET /v1/course-qa/{workflow_id}.
- Repeated isolated Temporal workflow smoke with current source and explicit converter. It returned final, covered 2/2 lessons across 4 scopes, with expected initial cache miss. Uses a temporary test server and fake DB/artifact/model adapters, not production services.
- Final local checks: 213 passed in tests/unit + tests/contract; Ruff passed on seven changed Python files; import boundaries 6 kept, 0 broken.
- No live rollout was performed. Existing b2c-api-1 and b2c-worker-1 remain running on prior image IDs (df2dc787... and 3c0ebbab...); b2c-lightrag remains on pinned image (5bdbd524...) with original start time. Updated images are ready, but runtime against live PostgreSQL, MinIO, Temporal, and a real model needs a B2C rollout and scoped end-to-end request. No Phase 5 gate or G2 review is claimed passed.


### Isolated live QA provider canary - 2026-10-05

A bounded real-provider request exercised the existing Phase 5 CourseQAActivities.generate path in a one-off container based on the current B2C worker image. The runner loaded the accepted outline artifact and verified its indexed-book/chunk manifest from B2C MinIO and PostgreSQL, then injected an isolated LLMGateway configured for deepseek/deepseek-v4-flash-0731 through OpenRouter. The OpenRouter key was mounted read-only from its external file. The general API and worker configuration, running containers, and default provider were not changed or restarted.

- Scope: one approved lesson (m01.l01), one question; regeneration disabled for this canary. Candidate generation and the existing structured answer-support/clarity judge made two sequential requests.
- Result: final; one final lesson scope and one item. The artifact is generate-course-qa, key 904427761ac88e47cc33a8d1ce224ec08d2ef0e8a43f2c37c9f4f5cac69a5e68, SHA-256 3e5fdb901050c874f84954cf2bda7b049604294138ec87f0823183f69c5dc61.
- Evidence check: citation chunk lane-ch5-probability:chunk:e56edfd67e8c03ba3c43 exists in the indexed source set; page 2 is recorded; the stored evidence quote matches the cited chunk text. All six stored checks are true: exact chunk ID, exact source quote, answer supported, clear/answerable question, scope relevance, and deduplication. The question, answer, and source quote are intentionally omitted from this operational report.
- Usage reported by OpenRouter: 5,828 prompt tokens and 274 completion tokens across two requests; reported total cost $0.000237574 (cost was reported for both calls). A conservative pre-request reservation used $0.00328525 of the $0.02 canary cap. No retries, fallback, or additional inference were run.
- The OpenRouter credits read immediately before this canary showed $0.083857 used and $7.916142926 remaining (per read-only account check recorded by the operator). The credits endpoint had not reflected this request at the first post-request check; provider-reported usage above is the current cost evidence pending a later balance refresh.

This validates one live generation-and-judgment path with stored exact-source provenance on one lesson. It does not validate all lessons, a ten-lesson course, production routing/default selection, broad semantic quality, or expert review. Phase 5 remains NOT PASSED; the previously listed T-SYN/T-PED and G2 evidence remains outstanding.


### Full-course live QA recovery and source-scope correction — 2026-10-05

A limited recovery used the approved two-book course phase5-live-tidx01-20261004 and the existing CourseQAActivities.generate activity inside isolated one-off containers. The API and persistent worker were not changed/restarted; the default provider was not changed. The temporary LLMGateway used deepseek/deepseek-v4-flash-0731 via OpenRouter with reasoning disabled, 90-second per-request timeout, per-lesson reservation cap, no fallback, and a read-only external key-file mount.

**Root cause and code correction.** The original lesson builder appended every chunk from each supplementary book to every lesson. For m01.l03 (“Permutations and Combinations”), five of the first chunks belonged to unrelated Think Stats material (“linear models”/hypothesis testing); the judge rejected those questions as outside lesson scope. The outline model has no lesson-to-supplementary mapping. New lesson scopes now use only spine chunks inside the accepted lesson block span; course-level scopes retain approved supplementary sources. Reuse of previously final supplemental-backed questions remains allowed only after exact source, page, role, model, quote, and all automated relevance/support checks pass. A regression verifies spine-only lesson scopes absent a mapping and retention of supplementary content at course scope.

A second recovery finding was that failed scopes discarded already validated questions (items=[]), preventing safe completion of a 4/5 scope. Failed results now preserve validated partial items. A regression proves partial items can be reused and that invalid/incomplete child scopes do not make group/course scopes final. The quote-option filter also rejects common question and exercise instructions (including “which”, “define”, and “suppose”).

**Scope results.** The already-final m01.l01, m01.l02, and m01.l09 were reused from the earlier artifact after source provenance/relevance checks; they were not regenerated. The seven recovered lesson scopes each ended with exactly five final Q/A items:

| Scope | Final items | Provider-reported cost |
|---|---:|---:|
| m01.l03 | 5/5 | $0.001130821 |
| m01.l04 | 5/5 | $0.00107563528 |
| m01.l05 | 5/5 | $0.000437022 |
| m01.l06 | 5/5 | $0.000337339 |
| m01.l07 | 5/5 | $0.000448696 |
| m01.l08 | 5/5 | $0.000806041 |
| m01.l10 | 5/5 (four reused + one targeted completion) | $0.00262832296 |

The recovered lesson requests used 70,004 prompt and 7,404 completion tokens with total provider-reported cost **$0.00686387724**. Additional rejected/diagnostic attempts occurred, so this is the successful recovery subset, not the cumulative task-wide account delta.

Once all ten lessons had five final items, the activity built the module/group scope with five items and the course scope with ten inherited items. Both were final with zero model calls, and incomplete-child parent finalization remains guarded.

**Final immutable QA artifact:** key 6f876b92ab5718e0dceb8df333895ed6a0b190f7da91d5d96255e8dad91783ca; SHA-256 34a2c40076fca461f339aebc450ca527877ee30f4121633c150eb00fa0497a67. It contains 12/12 final scopes and 65 items (50 lesson + 5 group + 10 course). A full artifact audit passed **65/65**: each item has exactly one resolvable source chunk; book, role, and page match; the quote is an exact contiguous source substring; model IDs match; and all six automated flags are true. No quote/page/source/check failures were found.

Focused verification after the fixes: .venv/bin/pytest tests/unit/pedagogy/test_qa_generation.py tests/unit/orchestration/test_course_qa_activity.py -q — **31 passed**. Group/course results were inherited from validated final lesson artifacts; the run made zero additional provider calls.

**Gate decision:** this closes the live, bounded QA generation/provenance slice for this one approved course sample, not the whole Phase 5/G2 gate. The API/general worker were not redeployed, the result was produced through an isolated activity runner, the sample is not human/adjudicated, and the earlier T-SYN/T-PED corpus, solver/FSRS, expert-review and production-runtime checks remain open. Do not represent Phase 5 as commercially approved or globally closed.

### Reproducible audit of the persisted 65-item artifact — 2026-10-05

The immutable MinIO object was read directly through the B2C MinIO client into a temporary file; the audit did not write to MinIO or restart/reconfigure any service. Added `eval/suites/phase5/audit_qa_artifact.py` as a reusable, content-redacting audit command and two unit tests covering acceptance and fail-closed rejection. The audit checks final scope state, exact requested item/scope counts, complete lesson coverage, the six stored automated flags, required source/page fields, and duplicate normalized questions within lesson scopes.

Command: `.venv/bin/python eval/suites/phase5/audit_qa_artifact.py <read-only artifact copy> --expected-scopes 12 --expected-items 65`.

Result: **PASS**; raw `course-qa.json` SHA-256 `b919637c4ec206a7de34d1f75eb40b7eb337f39886af705152c701740d8dad0e`; 12/12 scopes final; 65 items; 10 lesson scopes with 50 items; zero duplicate normalized questions among lesson items; all six automated checks passed 65/65. Group and course scopes intentionally reuse validated lesson items. The source-chunk-level audit remains the separately recorded 65/65 provenance check above.

Verification after adding the audit: focused Phase 5 generation/activity/validator and audit tests **37 passed**; full unit+contract suite **292 passed, 0 skipped**; `git diff --check` passed. Ruff was not available in the WSL project environment during this run.

### Current scope and gate disposition — 2026-10-05

**Phase 5 remains NOT CLOSED.** The owner has clarified that human review is unavailable during question creation and may occur later during experimentation. Human review is therefore not a prerequisite for automatic generation and is not used here as the blocker. The reproducible, bounded question-generation slice above passes its mechanical artifact gate.

The formal Phase 5 scope in `docs/05-PHASES.md` and `docs/03-TESTING.md` is broader than that slice. The connected course-QA artifact is `GroundedQA`, not the versioned lesson `Question`/`LessonDraft` output: the Phase 5 API does not yet generate the summaries/glossaries and Bloom-labelled MCQs needed to exercise the complete pedagogy handoff. Consequently T-PED-01's declared 30-defect MCQ detection threshold and T-PED-02 solver thresholds cannot be measured against the current output; T-PED-03 is only tested by a separate structural validator, not this generation path; T-PED-04 review-quiz routing and T-PED-05 py-fsrs parity have no connected implementation/fixtures. T-SYN-02's model judge is present and all 65 stored judgments pass, but its declared faithfulness threshold is not calibrated against an independent reference. T-SYN-03's lesson questions are unique, but the duplicate detector's declared F1 is not measured on labeled pairs; T-SYN-04/05 still lack executable contradiction and unsupported-topic fixtures. These are concrete missing tests/product connections, not requests for human review while generating.

No safe basis exists to waive these declared automated behavior gates or relabel the Phase 5 course-QA slice as the complete phase. Next implementation boundary: connect generated lesson assets to the `LessonDraft`/`Question` contracts, decide the MCQ/solver/FSRS product behavior, add deterministic negative fixtures for source conflict/out-of-scope requests, and then run the declared gates against automatically generated output. Keep any future expert review in later experimentation as the owner directed; it does not stop creating the current 65-question artifact.

### Lesson enrichment and pedagogy implementation update — 2026-10-05

The implementation boundary above is now connected in source: `CourseQAActivities.generate` invokes the per-lesson `LessonDraft`/`LessonSummary`/`GlossaryTerm`/Bloom MCQ generator, saves those assets alongside the current course-QA artifact, and accepts a bounded `lesson_mcq_count` (3–20, default 5). Generation fails closed on unresolved/out-of-scope citations, invalid lesson sentence references, MCQ defects, and insufficient automated source-support/with-context solver scores. The blind solver receives only the question and options in a separate call. The generated lesson sentences and all derived summary/glossary/question evidence point back to source chunk IDs and generated sentence IDs.

Review and scheduling are connected as pedagogy helpers: `build_review_quiz` draws from the accepted lesson's `prior_lessons` only and marks returned copies as review items; `review_card` uses pinned `py-fsrs` 6.3.2 with fuzzing disabled and UTC-only review timestamps. The deterministic card JSON plus `ReviewLogEntry` can be persisted by its caller. User-facing review/SRS API endpoints remain Phase 9 serving scope.

Automated verification after the implementation: **312 unit+contract tests passed, 0 failed/skipped**; the Phase 5-focused pedagogy/orchestration/API/artifact selection was **66 passed**. The pedagogical fixtures verify the source citation boundary, summary/glossary/question references, Bloom level, generated review-quiz selection, deterministic FSRS parity, UTC rejection, and a seeded set of 30 MCQs containing two supported defects (duplicate options and all/none-of-the-above) with 30/30 detection. This does not measure all Haladyna defect families or an independently labeled duplicate/faithfulness set.

#### Bounded live free-model canary — not passed

The OpenRouter `/models` endpoint returned HTTP 200 and listed `qwen/qwen3.8-27b:free`; its listed prompt/completion prices were both zero. The source artifact for approved course `phase5-live-tidx01-20261004`, lesson `m01.l01`, was selected. Budget tracking was initialized only in the B2C Postgres database. The existing successful 65-item course-QA artifact did not contain the new lesson-enrichment output, and no completed canary object existed in MinIO before inference.

The live `ExactQuestions` structured response failed local schema validation (`InvalidLLMOutput`) and did not produce lesson assets or a canary artifact. The raw completion cannot be recovered: this isolated gateway had no cache configured, gateway caching occurs only after successful schema validation, and remote content capture was disabled. The B2C daily budget ledger shows **4 reservations** for 2026-10-05; this exceeded the parent-assigned three-reservation canary cap by one. The model metadata advertises zero price, but token usage for the failed completion was not persisted, so there is no measured token count or provider-reported cost for this failed attempt. No paid fallback occurred. A later GET-only check for the proposed alternative `deepseek/deepseek-v4-flash:free` returned 200 but did not list that model for this key; no inference was sent to it.

#### Explicitly authorized paid DeepSeek canary — not passed

After the owner authorized a paid OpenRouter DeepSeek test, a read-only models check confirmed `deepseek/deepseek-v4-flash-0731` and current rates of $0.0152/M prompt tokens and $1.28/M completion tokens. A dry run found no matching canary artifact, selected one approved lesson (`m01.l01`) with 4 source chunks, and computed a conservative maximum cost of **$0.0027824**, below the **$0.02** cap. The single request used `max_tokens=2048`, disabled retries and fallback, and returned `LLMOutputTruncated`. OpenRouter usage was 2,760 input + 2,048 output tokens at a reported cost of **$0.00066684**. The canary runner saved no artifact; no second request was made.

#### Phase 5 disposition

**NOT CLOSED.** The product path and deterministic pedagogy helpers now exist, but no valid live model-produced LessonDraft/MCQ artifact was obtained. Both bounded model attempts failed before producing an artifact: the Qwen free response failed schema validation, and the one explicitly authorized DeepSeek paid response was truncated. Therefore there is no live source-support, Bloom, or solver quality evidence. T-SYN-02 remains uncalibrated against an independent reference; T-SYN-03 has no labeled pair corpus/F1; T-SYN-04/05 lack executable contradiction/unsupported-topic end-to-end fixtures; and T-PED-02 is verified only by controlled fixtures, not a valid live lesson output. Human review is not required during creation and remains deferred to experimentation as directed. No further inference was sent after the DeepSeek attempt. This report makes no Phase 5 or G2 closure claim.

### Automated gate expansion and final bounded canary attempt — 2026-10-05

Implemented within B2C only:
- MCQ linter now flags duplicate options, all/none-of-the-above and variants, combination options, negative stems, and conspicuous option-length cues. The 30/30 detection fixture injects six examples from each of five rules; it is a deterministic synthetic fixture, not Haladyna-wide human gold or a measured real-world sensitivity estimate.
- Lesson enrichment now records a narrow, deterministic contradiction finding when a primary and an approved supplementary chunk have identical normalized tokens except explicit negation. It keeps supplementary chunks out of lesson fact generation. Tests cover the detected conflict, source anchor, rejection of an unapproved supplementary source, and fail-closed unsupported-topic evaluation. This is not a general semantic contradiction detector.
- Review-quiz prior-lesson selection and pinned FSRS behavior have deterministic tests; the FSRS fixture now pins py-fsrs 6.3.2 output for a fixed UTC review timestamp and disables fuzzing.
- Fixed a conflict-anchor selection edge case so duplicate primary statements select the matching opposite-polarity anchor.

Regression: `.venv/bin/pytest tests/unit tests/contract -q` — **317 passed, 0 failed, 0 skipped**. A separate focused run for the new conflict, linter, and FSRS fixtures passed **13/13**.

A final one-request DeepSeek canary was authorized with a hard $0.02 cap, `max_tokens=4096`, and no retry/fallback. The preflight was a cache miss on approved course `phase5-live-tidx01-20261004`, lesson `m01.l01`, 4 source chunks; the conservative upper-bound reservation was **$0.0054038176**. The single invocation was passed the key through stdin, but the runner returned no observable provider response and saved no canary artifact; a follow-up dry-run still found a cache miss. Actual provider token usage/cost for this invocation could not be recovered from the runner output, so it is not represented as a successful or measured inference. No repeat was made under the one-request authorization.

**Phase 5 remains NOT CLOSED.** The complete unit/contract regression and deterministic gates pass, but there is still no verified live LessonDraft/summary/glossary/MCQ artifact from the canary, no live solver evidence, and no labeled independent corpus to calibrate T-SYN-02/T-SYN-03 or claim population-level MCQ sensitivity. T-SYN-04/05 now have executable controlled tests but not broad semantic coverage. This is a bounded technical/evaluation evidence gap, not a human-review requirement during creation. Human review remains deferred to later experimentation as instructed. No global Phase 5 or G2 closure is claimed.


### Runner diagnosis and final live enrichment attempts — 2026-10-05

The earlier no-result run was traced to orchestration: the command returned a live session after its 30-second wait, but the wrapper surfaced only stdout and discarded the session ID instead of polling it. That run therefore had no observable final status. Subsequent read-only checks found no saved artifact or cache hit, so its provider result and usage are unrecoverable. The runner was updated to select a model and prices explicitly, emit flushed stage events, disable gateway retries and free-pool retries, and verify MinIO readback bytes/hash after a successful write.

Preflight succeeded inside the existing B2C worker: approved outline and four source chunks loaded from PostgreSQL/MinIO, the current gateway source and JSON schema were confirmed, OpenRouter GET /models returned HTTP 200, and the model metadata listed the selected free route at prompt/completion $0/$0. MinIO's persistent data mount is `/mnt/wsl/b2cdata/minio`. No API/worker restart or provider configuration change was made.

Two single-call attempts followed with no retry:
- `nvidia/nemotron-3-super-120b-a12b:free`, max output 4096: `LLMOutputTruncated`; provider usage reported 1,894 input + 4,096 output tokens, $0.00, one API call.
- After that failed truncated completion, one explicitly authorized paid `deepseek/deepseek-v4-flash-0731` request, max output 4096: `LLMOutputTruncated`; provider usage reported 2,760 input + 4,096 output tokens, $0.00128124, one API call. The conservative preflight ceiling was $0.0054038176, below the $0.02 cap.

Both models stopped exactly at the output-token ceiling. Neither produced a schema-valid candidate, so no LessonDraft/summary/glossary/MCQ artifact was written to MinIO or the data disk. There was no raw completion cache and no retry. The paid attempt is the only paid inference in this final two-call sequence; no separate solver/evaluator call was made.

Final unit+contract regression: **317 passed, 0 failed, 0 skipped**. The runner and probes passed syntax/preflight checks. This passes the bounded implementation and deterministic-test subgate, but **does not close Phase 5**: the requested live lesson-enrichment artifact and its readback/source audit do not exist due to model truncation. T-SYN calibration against an independent labeled corpus also remains open. Human review is not a creation blocker and remains deferred. No claim of global Phase 5, G2, or commercial-quality closure is made.



### Verified free enrichment artifact, solver result, and regression update — 2026-10-05

A single free `nvidia/nemotron-3-super-120b-a12b:free` generation with `reasoning_effort=none` returned a complete response (2,013 input / 1,148 output tokens; reported cost $0). Its first wrapper failed `LessonEnrichment` validation; the generated lesson content was preserved and only the envelope was normalized. The corrected artifact was read back from MinIO and exported to `/mnt/wsl/b2cdata/eval/phase5/phase5-live-tidx01-20261004-m01-l01.json`; SHA-256 `d38c9d900ccd495f7bb3ec16b7c17ea3ab0214d9bfce68e2fdfed8ea3718d3f4`. It contains 6 cited lesson sentences, 3 summary points, 3 glossary entries, and 3 Bloom MCQs; contract, citation/source-role, scope, cross-reference, and above-remember Bloom checks passed. The source IDs are structurally constrained to the allowed lesson chunk IDs. This is a single lesson canary, not proof of semantic entailment or a calibrated population result.

A separate blind/with-context solver artifact was also saved and read back from MinIO (key `4e881bfc2931d71908973995ed24af8d6e408c339935733c5e68010d13677`; SHA-256 `b576b5a4c6513faaa893f3207d8e4b6fcbafc538fe8ce1597ea85d0d473918bc`). The bounded slice scored 2/3 with lesson context and 2/3 blind. It **fails** the configured solver thresholds (with-context >=0.95; blind <=0.30). The production enrichment path enforces these thresholds and fails closed. No follow-up inference was made after this evidence.

The connected path now constrains generated citation IDs through a per-lesson Literal schema and no longer sends answer indices to the evaluation/solver prompt. Focused Phase 5 unit checks pass **13/13**; full unit+contract regression passes **320/320**, 0 skipped. This is stronger than the prior 317 count and supersedes it for the current tree.

**Disposition: Phase 5 remains NOT CLOSED.** A real contract-valid, source-audited free-model lesson artifact now exists, so the earlier “no live artifact” blocker is cleared. However, solver is demonstrably below the enforced gate, while independent T-SYN faithfulness/duplicate calibration and broad negative-set measurement remain unavailable. These are machine evaluation/quality gates, not human review during creation. No G2 or population-quality claim is made.


### Alternative free-model route check — 2026-10-05

The product-path canary code was configured for 20 MCQs and source-paired, balanced options. Focused tests first surfaced three stale orchestration mocks whose generic options no longer met the source-detail contract; the fixture was updated with source-anchored options and a deliberately wrong blind response. Focused verification then passed **4/4**.

Provider checks were metadata-only unless noted: OpenRouter `/models` listed `qwen/qwen3.8-27b:free` at $0/$0 and `/credits` reported $10 credits / $0.233727991 usage, but the one permitted product-path attempt returned HTTP 429 before an API call was recorded; no retry or fallback was made. CodeCraft `/v1/models` returned HTTP 403. TokenRouter model metadata returned 403 at its documented beta endpoint and 401 at the alternate endpoint. Inception's `/v1/models` listed Mercury 2 at nonzero standard rates and its credits/usage endpoints returned 404, so no inference was sent there. The Gemini key was available from the RAM-backed secret; its model list returned HTTP 200 and included `gemini-2.5-flash-lite`. Google's published pricing identifies this model's Developer API Free Tier as $0 for text input and output. The single bounded product-path request was sent to the documented OpenAI-compatible endpoint and returned HTTP 404; provider usage remained zero, no completion/raw output was returned, and no retry was made. Thus the planned 20-question Gemini artifact and solver comparison were not produced.

An additional single canary was authorized against the user's stated Atria token pool. The protected desktop inventory was read only in memory; GET `/v1/models` returned HTTP 200 with `Atria-Dawn-Preview`, but no pricing or balance metadata. One product-path request (20 questions requested, output capped at 4096) returned HTTP 422. The gateway reported no usage, no response body/raw completion, and no artifact; there was no retry or fallback. No cost/free-price claim is made for Atria.

Final regression after fixture correction: `.venv/bin/pytest tests/unit tests/contract -q` — **336 passed, 0 failed, 0 skipped**; `git diff --check` passed. This does not close Phase 5: existing Nemotron evidence still fails the solver gate (context 0.90, blind 0.70), and the alternate free-model canaries produced no new artifact. Independent T-SYN calibration and broader measured T-PED performance also remain unestablished. Human review is deferred and is not the blocker. No paid inference was used.

### Bloom distribution gate and bounded Cohere free canary — 2026-10-05

The generation prompt now explicitly requires at least 80% apply/analyze questions that transfer a cited rule to a changed condition, compare cited steps, or combine source details; recall questions may not be relabeled. A deterministic pre-artifact batch lint enforces the 80% apply/analyze threshold, rounding up (for example, 16/20 and 8/10). Regression covers the exact threshold, below-threshold rejection, and an all-remember batch. Focused pedagogy+contract checks: **69 passed**; full unit+contract suite: **352 passed, 0 skipped**; git diff check is clean.

A single bounded live run used `cohere/north-mini-code:free` after a fresh OpenRouter `/models` response (HTTP 200) reported prompt and completion prices of `$0`. The product path requested exactly 20 questions and received a source-persisted raw completion containing **16 questions, all labeled apply**. The exact-count response schema rejected it before artifact assembly, so the Bloom lint did not create or waive a final artifact; no solver/evaluator follow-up and no retry were sent. The provider usage field was absent, so token count is unknown; listed model pricing is zero. Raw response was persisted and immediately read back by the runner; stored object SHA-256 `1878d0b9e60e8816ad6d694ee690bf3340da1f3a709942f206e977c8275b8426`, raw completion SHA-256 `ead0e37cebb794abc16c621fbe8835460e21751a746f438759bbf31e161e1add`, source SHA-256 `69d16728b42a67cacea11b9ebbbeb8962de28f3088a10a4d1d9d628d9d1e681a`. No final lesson artifact exists for this run; therefore source audit/readback of a final artifact and solver gates remain unpassed.

**Phase 5 remains NOT CLOSED.** The product prompt and Bloom distribution lint are implemented and regression-tested. The live free-model attempt demonstrated the requested Bloom labels but failed the exact 20-question contract (16 returned); it did not produce a final artifact or solver evidence. No paid inference was used. Existing independent T-SYN calibration and other previously declared phase gates also remain open.


### Source-grounded distractor builder and Atria bounded retry checkpoint — 2026-10-05

The product MCQ proposal schema now accepts only a stem, Bloom label, exact answer text, rationale, and sentence references. The application builds choices from distinct answer facts that pass source-anchor validation, cites every choice to its supporting lesson sentence, balances choice lengths, shuffles the choice set, and rejects answer leakage in stems. This keeps unsupported distractors out of model output. The repair path remains bounded; the canary below disabled repair to honor its call cap. The orchestration fixtures now use the same proposal-only contract and exercise source-built choices and context/blind solver behavior.

Offline gate: the full unit and contract suite passed 356 tests. Focused orchestration activity tests passed 4 tests. The repository whitespace check passed before the provider attempts.

A fresh OpenRouter models check listed poolside/laguna-s-2.1:free at /bin/bash//bin/bash (HTTP 200). Its single proposal-only canary returned the gateway's free-pool rate-limit error before any completion; gateway-reported usage was 0 calls, 0 input tokens, and 0 output tokens. No raw completion or artifact existed, and the route was not retried.

The protected Atria inventory section was read in memory only. A fresh GET to the Atria models endpoint returned HTTP 200 and listed Atria-Dawn-Preview; the response supplied no price or balance metadata. One direct chat-completions request was sent without a response_format field, with a 4096-token output cap and no retry. The client timed out while reading the response; no HTTP response or usage metadata was received, gateway counters remained zero, and no raw completion or artifact could be saved or audited. No second Atria request was sent. Usage is indeterminate; this is not evidence of zero consumption, and no free-price claim is made.

Disposition: Phase 5 remains NOT CLOSED. Deterministic proposal-to-choice construction is implemented and regression-tested, but no new live artifact passed source, Bloom, solver, and readback gates in this round. The existing Nemotron solver evidence remains below threshold; independent T-SYN calibration and the remaining declared negative-set/performance gates also remain outstanding. Human review during generation is not a blocker.

### Saved-raw artifact and bounded solver pair — 2026-10-05

A source-grounded builder reconstructed a 20-question lesson artifact from previously saved raw proposals; no new generation request was used. The model-proposed distractors were discarded. The application rebuilt each option from validated source facts, attached option-level citations, and checked the lesson contract, source references, deduplication, answer leakage, and Bloom labels. The final batch has 15 apply and 5 analyze questions. It was persisted to MinIO and read back with matching bytes and schema. Lesson JSON SHA-256: `69d2694bb4f5b5a2bc1569322c5f68fe2eff2c8af85d91aaa4768421519025a4`; envelope SHA-256: `539774c98cb2dfd5d6d3cc12c78d46e45fa0bc93bd81a5522b4dcf933bb79bb8`; provenance SHA-256: `5fd4e85445ffcda92875910d9402e055737ba599bcab99ed13cc403b32437d0e`. MinIO lesson key: `phase5-enrichment-canary/77700e9b931e1f7a65dd6eea03318ceb6c887f395b38333e843382c2e41ddb95/lesson-enrichment.json` in course `phase5-seatweaving-saved-raw-rebuilt-v1`. This is a rebuilt canary from saved model proposals, not a population-quality claim.

The bounded DeepSeek V4 Flash solver pair used two calls under the $0.005 pair ceiling, with provider fallback disabled. The context call used 4,557 prompt tokens and 400 completion tokens, reported cost `$0.0005812664`, and ended at the output limit without message content; context accuracy is therefore unavailable. The blind call used 2,218 prompt and 64 completion tokens, reported cost `$0.0001156336`, and scored 20/20 (1.00) without lesson context. The blind result fails the configured blind threshold (<=0.30); the context threshold (>=0.95) cannot be evaluated. The conservative pair upper bound was `$0.0013638264`. The credits endpoint returned HTTP 200, but its post-call usage figure did not move from the preflight value despite provider-reported per-call cost, so per-response usage is the reliable cost evidence here. The saved solver evaluation/readback SHA-256 is `c4130190cd01b00595bf3f303dc9279c2bafd93aac070b68052d9ad958db3dac`.

The final implementation/regression evidence remains 356 unit+contract tests passing, 0 failures, 0 skips; focused orchestration tests passed 4/4, and `git diff --check` was clean before this documentation-only update. No additional inference was made after the bounded pair. **Phase 5 remains NOT CLOSED**: the live lesson artifact is valid and read back, but the solver gate fails/unavailable, and independent T-SYN calibration and broader measured T-PED quality evidence remain open. Human review during creation is not a prerequisite and is not the blocker. No global G2 or population-quality closure is claimed.


### Final automated source-grounded gate and bounded closure — 2026-10-05

The earlier saved-raw-rebuilt artifact was rejected for evaluation use after the blind solver scored 20/20 and an audit found a strong option-length cue: the keyed option was the shortest in 13 of 20 questions. Its 20/20 result is not accepted as a Phase 5 solver pass. The product builder and lint were tightened: for source text with explicit numbered procedure labels, the application now builds transfer questions from source details and chooses uniformly formatted, source-cited “Step N” options. The linter enforces a maximum eight-character spread for all option sets. The generator rejects details that disclose a step number or ordinal in the question stem.

A new artifact was created from the existing Seat Weaving lesson source and summary/glossary inputs; it did not reuse the old MCQs and did not require a provider generation call. Contract, source citation, per-option attribution, Bloom, length, and answer-position checks passed. The artifact contains 20 questions, all labeled apply, with answer positions distributed 5/5/5/5 and a maximum option length spread of zero characters. MinIO readback and schema validation passed. Course: `phase5-seatweaving-procedure-template-v2`; stage/key: `phase5-enrichment-canary/0a1dd1fad8c675543dd1885fa63bb47ed0172b5b39a80d0bc3b794c27b3497a0`; lesson SHA-256: `2a5f8c7dec13ee3f6779f6ca795e1b1fbd0205841e814ac4803ee828e9dcb88d`; provenance SHA-256: `72248e0e1c1e04d92908966fc532916c6d5c0148f78e2e25a82e5cb496b0a236`; source fixture SHA-256: `69d16728b42a67cacea11b9ebbbeb8962de28f3088a10a4d1d9d628d9d1e681a`.

The new bounded DeepSeek V4 Flash solver pair passed its numeric gates: with lesson/source context, 20/20 (1.00); blind, 5/20 (0.25), within the configured maximum 0.30. Blind request audit shows each item contained only qid, stem, and options; no answer index, rationale, citation, or source evidence was sent. The keyed answer position was balanced and option lengths were identical. Provider-reported costs were `$0.0001426136` for context and `$0.000108292` for blind, total `$0.0002509056`. The conservative preflight bound was `$0.0014564384`, under the `$0.005` pair cap. Credits endpoint returned HTTP 200 but its usage field was stale across this pair; per-response provider usage is the cost evidence. Solver evaluation was saved/read back with SHA-256 `b57e5fccb9e00aa334c6da0ea89b0bfadfdc1db90e121c1721cd3661fe96a004`.

Final unit+contract regression: **360 passed, 0 failed, 0 skipped**; `git diff --check` passed. The bounded implementation and automated canary gates are closed for this Phase 5 slice. This does not establish population-level question quality or close Gate G2: independent labeled faithfulness/duplicate calibration (T-SYN) and broad sensitivity/quality measurement still require a held-out corpus. No human review during question creation is required; the outstanding G2 evidence is independent automated calibration, not a manual creation step.
