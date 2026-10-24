# B2C project status

Last reviewed: 2026-10-05 (Africa/Cairo); live 677-page upload indexed successfully after outline-prefix repair; Phase 3 bounded research gate closed; Phase 4 research decision closed as a negative production recommendation; Phase 2 release gate remains open
Current phase: Phase 2 - Ingestion and outline
Status: In progress; release gate not passed
Scope boundary: B2C only. The separate ai company project is excluded.

## Current operational follow-up — translation timeout repair (2026-10-23)

- Book2 body resume workflow b2c-translate-be560ad8355b4153be57acfb0bb6392fc909ae2eabddcf5551ad46bdc8d30bde is RUNNING. Latest durable progress: batch 553/804, active unit book-70c88ae9b2ba9ed6f61e813c:366:22; 2,530/2,620 accepted checkpoints, 90 unresolved at this snapshot, and zero suspect saved segments.
- The 90 unresolved include not-yet-visited units. The active pass currently records 15 deferred unit failures (numeric token/marker validation and provider truncation); these are provisional until the final isolated retry/adaptive-recovery pass. Do not count them as final failures yet.
- Timeout recovery is deployed to the four workers and HY1–HY3. HY4 is on experimental image b2c-hy-translation:ocr-fragment-normalized-20261023; it is healthy. The experimental change reconstructs only lowercase OCR word splits whose spelling is confirmed in adjacent context, then uses the normal contextual translation prompt. A term-only prompt was rejected after a canary produced an incorrect Arabic gloss.
- Focused regression suites now pass 175 tests; ruff check --select F passes on changed translation/gateway files. A diagnostic worker image was built and isolated-import tested, but has not replaced any live worker. One canary of the normalized short fragment completed with finish_reason=stop, but its Arabic wording is not treated as gold or a semantic-quality pass. The experiment is limited to HY4 pending more evidence.
- No saved inference was replayed; the original 2,530 checkpoints remain intact. Do not claim 100% until Temporal completes and the authenticated units endpoint verifies 2,620/2,620, zero missing, and zero suspect output.
- See eval/reports/translation-timeout-recovery-2026-10-23.md for evidence and the per-unit failure trail.

## Executive summary
The B2C ingestion foundation supports PDF upload, extraction/OCR, outline approval, workflow tracking, and provenance. Phase 3’s bounded research gate is closed using 37 chunks and machine-evaluated, exact-source-checked query labels; these are not gold. Phase 4’s 37-chunk/50-query comparison and entity-resolution diagnostics are complete as a negative research decision: LightRAG is not adopted for production retrieval, T-KG-01 and T-KG-02 are below provisional quality thresholds, and T-KG-05 produced no supported cross-book qrels. Phase 4 acceptance is not passed. Phase 2 remains the current open release phase; no commercial-quality claim is made. No AI Company resources were touched.

## Verified evidence

| Area | Evidence | Status |
|---|---|---|
| Unit and contract suite | Latest run after Phase 4 telemetry: 189 passed, 0 skipped (`.venv/bin/pytest tests/unit tests/contract -q`); focused KG suite after the extraction prompt update: 42 passed | Pass for tests only; phase gates remain open |
| Dependency boundaries | uv run lint-imports - 6 kept, 0 broken | Pass |
| Worker runtime image | Local `b2c-worker:dev`: `sha256:b58a1309...`; built before the final supplementary handoff changes; current worker container remains on an older image | Final rebuild and rollout pending; no service restart |
| Arabic OCR smoke (PP-OCRv5) | Fixed line-order/layout reconstruction; clean synthetic CER 3.09%, degraded CER 5.15%; 20.95s / 7.09s in the rebuilt worker, 1.76 / 2.22 GiB peak RSS | Parser regression fixed; residual model errors and real gold corpus open |
| Datalab Surya 2 candidate | On the same 97-character fixture, clean and degraded CER 0%; at 4 CPU server cap, 200.83s / 201.44s, model server ~1.29 GiB plus client RSS ~0.44 / 0.46 GiB | Promising accuracy candidate, too slow for default on CPU; validation and licensing review open |
| Arabic/degraded OCR | One synthetic page comparison only; no representative, human-labeled golden evaluation | Open |
| API -> Temporal -> MinIO -> outline -> approval | Workflow COMPLETED in 3.72s; 37 pages, 42 blocks, 1 module, 1 lesson; approval artifact persisted; duplicate upload returned same workflow ID | Integration smoke pass; not a human acceptance or full E2E gate |
| Phase 3 approval -> Temporal index -> B2C MinIO/PostgreSQL | Disposable current-source worker on a unique Temporal task queue indexed one spine and one supplementary ParsedBook; exact lesson/source paths, roles, pages, and block IDs verified; exact test rows and objects removed | Real integration smoke passed; long-running API/worker deployment not performed |
| Live uploaded book -> outline approval -> Temporal index -> PostgreSQL | 677 pages, 776 blocks; fixed one unheaded leading page-header block omitted from lesson 1; approval returned HTTP 201; indexing completed with 397 unique persisted spine chunks and page provenance for pages 1-677 | Live single-book integration pass; broad book-quality gate remains open |
| Model files | BGE-M3 and reranker files present (integrity confirmation still needed); Hy-MT2 1.8B, Fasih-TTS-V1, and SILMA-TTS files downloaded; Surya 2 GGUF downloaded at pinned revision | Translation/TTS/RAG pipelines unbenchmarked; OCR compared only on one synthetic page |
| B2C data VHDX | F:\wsl-b2c\b2c-data.vhdx exists; mounted from /dev/sde as ext4 rw and marker/files verified. Scheduled Task B2C-Data-Disk-Mount registered at user logon with highest privileges; manual task run returned success and log verified marker. | Current session mounted rw; reboot persistence awaits restart verification |

## New OCR model routes (2026-10-04; images built, live provider call not run)

- Qwen3.8 27B Free is the configured free OpenRouter route; DeepSeek V4 Flash Vision Exp is a separate explicit paid route. Neither silently falls back to the other.
- Local RapidOCR remains default; Surya remains the local quality option. `B2C_OCR_ENGINE=openrouter` sends only sparse/scanned page images, one page per request, and preserves the physical page reference. Completed page calls are reused from the Postgres LLM cache after a quota stop.
- Qwen safety limits are 40 B2C requests per UTC day, 6 seconds minimum spacing, and at most two retry attempts for upstream shared-pool 429s. The app stops at quota instead of paying or switching models automatically.
- The Gemini overlay uses the existing RAM-backed secret and remains limited to Docling-detected table crops, including table pages in the OpenRouter OCR route.
- Bounded text batching is configured at 10 page segments or 24,000 characters per request; truncated output is split into smaller batches. Worker mock/Gemini tests: 27 passed; one-off smoke confirmed route configuration, quota settings, and secret-file presence without reading values. API image `sha256:ec6640a8...` imported successfully and registered `POST /v1/books`. No actual external provider request or container restart occurred; running services still use prior images.

## Phase 2 remaining acceptance work

1. After the next Windows restart, verify the task log and exact B2C mount marker. Manual rerun on 2026-10-04 succeeded while the current WSL session was already mounted; this is not reboot proof. Never format or initialize the VHDX.
2. Compare PP-OCRv5 and the higher-accuracy but CPU-slow Surya 2 candidate on a rights-cleared, human-labeled Arabic scan set using normalized CER/WER, page provenance, elapsed time, and peak memory.
3. Finish the licensed golden set for native PDFs, low-resolution scans, skew, blur, tables, multi-column reading order, and mixed digital/scanned pages; only include files whose item-level permissions are recorded.
4. Measure parser determinism/cache behavior, per-page provenance, outline span coverage on representative books, and multi-column/negative controls. Synthetic/unit no-TOC and chunk-boundary tests now pass.
5. Existing scan approval/cache/worker-restart evidence is one synthetic fixture. Add live invalid-upload API checks, transient retry against real MinIO, and live no-TOC inference through human approval; latest retry test used an in-memory store double.
6. Save exact commands, outputs, hashes, metrics, and gate decision in eval/reports/phase-2.md; do not mark PASS until the acceptance table is complete.


## Risks and blockers

- Arabic OCR: line-order handling is fixed; PP-OCRv5 scores 3.09% / 5.15% CER on one synthetic fixture. Surya 2 scored 0% on the same fixture but took about 200 seconds per page at four CPUs. No real, adjudicated pages have been scored.
- OCR generalization: synthetic smoke does not replace a held-out, human-labeled Arabic PDF corpus.
- Four ACO Arabic PDF files are present on the data disk; item-level rights evidence, verified hashes, and human gold transcripts must be checked before using them as evaluation evidence.
- Commercial readiness: The current test count is not a commercial-quality gate. Security, load/reliability, restore, rights review, usability/accessibility, and measured quality gates remain for later phases and must be scheduled before any commercial release.

## Progress against the target architecture

| Target component | Planned outcome | Current state | Release evidence still needed |
|---|---|---|---|
| Ingestion and outline | Rights-aware PDF ingestion, OCR, source-linked outline and human approval | Foundation implemented; digital-PDF and Arabic scan workflow smokes passed; PP-OCRv5 CER 3.09%/5.15%, Surya 2 CER 0%/0% on one synthetic page with much slower CPU throughput | Representative Arabic/English gold, mixed-page/layout/negative tests, scan upload and retry evidence |
| Chunking and RAG | Structure-aware chunks, hybrid retrieval and source filtering across spine/supplementary books | QueryRetrievalService is called from the course-QA worker; lexical is default, hybrid BGE-M3 RRF is opt-in through an isolated Compose overlay. Both preserve exact book/role/page provenance in QA output. | Validate the base Compose service graph, run a bounded live hybrid worker canary, then measure attribution/access scope and resource use. BGE reranking stays evaluator-only. |
| Graph and course authoring | Concept relations and grounded lesson/question generation | Direction and package skeleton documented; end-to-end implementation unverified | Graph store comparison, evidence-backed edges, citations/faithfulness, solver/linter results |
| Translation | Arabic translation with glossary and calibrated quality review | Hy-MT2 1.8B weights downloaded; no measured translation pipeline; model is text-to-text | Human reference set, term/number preservation, QE calibration and reviewer workflow |
| Speech and MP3 | Pronunciation-checked lesson audio with reproducible MP3 metadata and timestamps | Fasih-TTS-V1 full CATT text path evaluated on 3 paired seeds. CPU 4/4 threads averaged RTF 3.008 and tiny-ASR CER 3.77% (12/318); 28/4 averaged RTF 13.800 and CER 5.35% (17/318). Paired speedup median 3.45x. Fasih-specific 4/4 bootstrap helper is tested but not wired because no Fasih provider/deployable audio service exists; no production configuration changed. See eval/reports/phase-8.md. | Production provider and persistence, full MP3 package, calibrated ASR/alignment acceptance, listening review |
| Web/mobile serving | Versioned API and Arabic RTL learner experience | Android operator-test APK 0.3.2 now covers book selection/composition, output routing, stage status, and audio download. This is not yet the learner-facing course player. | Android 7/8 device E2E, multi-book course through MP3, accessibility, reconnection/resume, and learner playback |
| Security and operations | Tenant isolation, recovery, monitoring, safe upgrades and release provenance | Local Compose and partial telemetry; data-volume autostart task is registered; reboot verification remains | Threat model, backup/restore drill, SLO/alerts, SBOM/scans, rollback and deletion tests |

This matrix tracks progress against 01-ARCHITECTURE and the phase gates; a row marked implemented does not imply release readiness.

## Operator APK 0.5.1 staged update (2026-10-09)

The Android operator app now displays a selected PDF and explicit upload/OCR state with byte-based upload progress when the provider reports a file size; the selected book role is shown as either the primary book for a new/current course or a supplementary book, and library choices retain and summarize the composition. The optional lesson-enrichment and Midterm/Term/Final review-bank controls are present. They default off and their request fields are omitted unless enabled so the APK remains compatible with the currently running strict-schema API; the screen states that enabling them requires the staged backend rollout after the active translation finishes.

Debug APK 0.5.1 (versionCode 18, `armeabi-v7a` only) built successfully and was uploaded to Drive: https://drive.google.com/file/d/1H35VQzS5OMUKE3tqBcDL4WCFyImSMKHB/view?usp=drivesdk. SHA-256: `5b5fe7905eb2349fbd4136302d5645b711459ce76309ab1e7cd9ed178149158d`. Drive metadata confirms the file name, APK MIME type, and 106,708-byte size. Focused backend checks: 70 passed; Android Gradle `assembleDebug`: successful. Source inspection confirms upload-state updates and one-primary/multiple-supplementary selection rules; no emulator UI run was performed in this check.

The API image with review-bank/enrichment request support and a matching generation-worker image were built as staged candidates. Isolated, network-disabled imports confirmed the new request fields in both images. The running API and generation worker remain on their prior `:dev` images; no container was restarted and the active translation workflow was left untouched. Deployment and end-to-end use of the new optional controls remain pending the safe point after translation completes.

## Phase 6 optional retrieval experiment (2026-10-05)

The bounded Phase 6 comparison completed against the frozen 37-chunk corpus and matching 44-query set. The opt-in graph-retriever boundary remains disabled by default; the production retrieval path is unchanged. A persistent, conservative USD ledger replaced the prior token-only reservation that priced prompts at the completion ceiling and prematurely stopped reranker calls. The DeepSeek policy pins the exact model, caps output at 1,024 tokens, disables fallback, and enforces a $0.25 maximum cost.

Run 02 completed 44/44 rankings with 10 valid source IDs per query and 1.0 page/source attribution integrity. HippoRAG Recall/MRR/nDCG@10: 1.0000/0.7054/0.7791; graph indexing 203.77 s, mean query retrieval 0.714 s, peak RSS 2,783 MiB. Against the saved same-query Phase 3 results, HippoRAG matches RRF recall but trails RRF on MRR/nDCG (0.7929/0.8440), and trails RRF+BGE reranking (0.8605/0.8962); the latter has 11.126 s reported mean latency. The graph adds indexing/maintenance cost without a demonstrated ranking gain. One unanswerable query is insufficient to characterize abstention. Relevance labels are system-generated and not gold.

Decision: bounded research comparison closed **NO-GO for production adoption**. Phase 6's product/production acceptance gate is **NOT PASSED** because relevance labels are non-gold and no retrieval benefit was demonstrated. Focused Phase 6 runner/bridge and graph-retriever tests: 35 passed. Full details, hashes, cost ledger, command/artifact paths, and limitations are in `eval/reports/phase-6.md`; the complete replay used cached model responses and incurred no additional model requests.

## Project reporting standard

For every phase, maintain eval/reports/phase-N.md with scope, executed command/evidence, pass/fail/skip per test ID, measured metrics, version/configuration, deviations, risks, owner/next action, and a clear PASS/FAIL gate decision. Never infer a phase PASS from unit tests alone.


## OCR quality and correction stages (2026-10-03)

- Real old-book English scan corpus benchmark: PP-OCRv6 small scored 3.0573% micro CER over 10 pages (36.61s); PP-OCRv5 EN mobile scored 3.6671% (38.96s).
- Surya 2 with its official OCR prompt scored 2.067% on difficult page j017 and 0% on easy page j008, but required 168.83s and 126.91s respectively on a four-CPU cap. A modern printed prose page also completed with 0.36% diagnostic CER against its PDF text layer: 11 ordered HTML blocks, 2,385 completion tokens, 641s on the current CPU server. Short client timeouts/output limits caused earlier false no-result/incomplete runs. Surya is now a working explicit quality/slow-path candidate, not a production-approved universal default; human-adjudicated mixed-layout evidence remains required.
- Proposed evaluation separates text-level OCR-error correction from later language proofreading. Keep the OCR output immutable; save each candidate, edit diff, provenance, confidence, and model/version. Evaluate each stage independently and together on held-out books with CER/WER, over-correction and critical-token checks. No correction model is approved yet.
- Confidence-aware routing is not ready because current Docling blocks do not populate their optional confidence field. Phase 2 remains open.

## OCR model selection update (2026-10-03)

Current provisional shortlist: PP-OCRv6 is the fast English baseline; Surya 2 is the quality/slow path and now completes a modern printed page when run with its exact image-first prompt, >=4,096 output-token budget, and long timeout (about 10.7 minutes/page in the observed CPU run). Earlier short timeouts and a 2,048-token cap caused false no-result/incomplete runs. OvisOCR2 did not complete a bounded CPU smoke test. The new modern cohort is only exploratory because its references come from PDF text extraction, not manual transcription. Phase 2 quality gate remains open; do not report OCR selection as production-approved.


## Optional provider experiments (2026-10-03)

- B2C worker image now includes an opt-in Gemini 3.5 Flash-Lite table-crop path. A two-page app workflow smoke completed: page 1 table stayed a table at page 1 and matched the extracted 36-outcome reference; page 2 prose remained in its normal blocks. Remote access and the Gemini credential are enabled only by a temporary RAM-backed Compose override. One clean table is routing evidence only; Phase 2 stays open.
- OpenRouter free-model review: the agreed Qwen3.8 27B Free route eventually completed all 8 selected benchmark pages after seven transient shared-pool 429 responses were retried. It has exploratory weighted CER/WER 49.99%/64.51%; one hard English table was truncated at the test output cap. The explicit paid DeepSeek V4 Flash Vision Exp path completed the same 8 pages at 23.03%/38.89% and $0.01162 observed cost. Neither result is release-grade; Dots3-Note did not transcribe within its free output budget. New runtime route wiring is not yet built/integration-verified.
- A Kiosapi Compose overlay exists but was not activated: its supplied credential received 403. Do not call it verified or use the credential until the provider/account mismatch is resolved.

## Phase 6 isolated runtime readiness (2026-10-04)

- HippoRAG security remediation is contained to a hash-locked Python 3.12 sandbox; its offline installation, package consistency, imports, and local BGE-M3 initialization passed. Focused Phase 6 and graph adapter checks: 27 passed.
- A paired lexical ranking baseline is prepared for the exact 37-chunk corpus and 50 development queries. These query labels are automatic and non-gold. The DeepSeek V4 Flash 0731 OpenRouter canary and request-policy pin passed; Phase6 provider inference awaits Phase5 to release the shared paid endpoint.
- HippoRAG inference/indexing has not started because Phase 4 is actively using shared Qwen. Keep Phase 6 optional and out of production until a paired evaluation completes.

## Latest scan-path evidence (2026-10-04)

The Surya adapter completed a live B2C scan upload through API, Temporal, worker, MinIO, outline, and persisted approval using an internal synthetic one-page Arabic fixture with a PDF outline bookmark. Parsed output had 3 OCR blocks plus the page-1 bookmark heading; all OCR blocks retained page 1 and bounding boxes. Identical re-upload reused the same workflow and returned cache hits for parse and outline without a new Surya request. `force_reprocess=true` created a distinct run, missed both caches, and reproduced the same parsed-content SHA-256. Restarting only the B2C worker left the completed result and approval artifact readable. Worker runtime was restored to `rapidocr` default after the test.

A no-heading scan correctly persisted OCR then failed at the existing non-retryable `OutlineNeedsReview` gate. This is the known no-structure outline gap, not an OCR transport failure. The positive fixture uses a synthetic PDF bookmark and does not constitute human OCR accuracy evidence. Transient activity retry, human review, representative gold pages, remaining API checks, and data-disk reboot verification remain open.


## Phase 3 closure update (2026-10-04)

Phase 3 is not yet closed. T-IDX-01 and T-IDX-03 pass as bounded fixed-corpus checks; T-IDX-02 still requires an independent semantic judge run. The 10-lesson, two-book sample passed deterministic structural review with a machine identity and evidence hash; re-chunking produced 58 chunks, 0 boundary crossings, and 11/11 intact tables. The initial 50-query comparison exceeded provisional Recall@10/nDCG@10 targets on machine-generated, exact-source-validated labels (`gold_eligible=false`), but is not sufficient to close T-IDX-02. A separate system attestation confirms all 50 source citations while leaving semantic query/relevance judgments unadjudicated. A frozen lexical threshold yielded 0 false answers among 100 held-out absent/irrelevant controls and 3/14 supported false abstentions. The threshold is optional and lexical-only in the query service; it is not a universal/default score or production guarantee. Commercial quality work and Phase 2 remain open. Detailed evidence and the pending judge runner: `eval/reports/phase-3.md`; source artifacts remain on the B2C data VHDX.


## Phase 5 isolated live QA evidence (2026-10-05)

A one-off isolated B2C runner exercised the actual CourseQAActivities.generate path on the approved two-book course, using deepseek/deepseek-v4-flash-0731 through OpenRouter. The accepted outline and indexed PostgreSQL/MinIO artifacts were read from B2C services. The API and persistent worker were not changed or restarted; their default provider remains unchanged, and the key was mounted read-only from outside the image.

The final immutable artifact is generate-course-qa/6f876b92ab5718e0dceb8df333895ed6a0b190f7da91d5d96255e8dad91783ca (SHA-256 34a2c40076fca461f339aebc450ca527877ee30f4121633c150eb00fa0497a67). It contains 10/10 final lesson scopes ×5 items, the module group at 5 items, and the course at 10 items: 12/12 final scopes and 65 items total. An independent artifact audit passed 65/65 source checks: citation IDs resolve, book/role/page provenance matches, each quote is an exact source substring, model IDs match, and all six stored checks are true. Group/course inherited from complete lesson scopes, so that rebuild used zero provider requests.

The seven recovered lessons used 70,004 prompt and 7,404 completion tokens at provider-reported cost $0.00686387724; rejected diagnostic attempts were additional. During this work, lesson QA was restricted to its accepted spine span because the outline currently has no lesson-specific supplementary mapping. Failed scopes now preserve verified partial items for recovery. The focused pedagogy/activity suite passed 31 tests.

This is a full live generation/provenance result for one approved course through an isolated activity runner. It does not prove broad educational quality, expert acceptance, production rollout, or closure of all T-SYN/T-PED and G2 gates. Phase 5 remains not globally passed; see eval/reports/phase-5.md for evidence and remaining limits.

## Phase 6 DeepSeek comparative run staged (2026-10-05)

Phase 4 released its first Mercury evaluation at 44/50 auto-judged questions; reported Mercury usage was 1,619,080 tokens, with 1,989,089 conservatively accounted against its 2,000,000 cap. Its 44-query retrieval diagnostic on the frozen 37-chunk corpus reported lexical R@10/MRR/nDCG 0.9767/0.6875/0.7589, dense 0.9535/0.8036/0.8398, hybrid 1.0000/0.7929/0.8440, and hybrid+reranker 1.0000/0.8605/0.8962. These are automatic-label diagnostics, not human-gold results.

A separate Phase 6 DeepSeek V4 Flash policy is now staged at `eval/suites/phase6/openrouter-deepseek-v4-flash-0731-capped.json` (SHA-256 `b6ffccbd22ccbeb99652a20f1c877ce63861da79fc72c12ba4916a6516fafe51`). It pins `deepseek/deepseek-v4-flash-0731`, OpenRouter, `reasoning_effort=none`, 2,048 max output tokens, price sorting, disabled fallbacks, and ceilings of $0.0152/M prompt and $1.28/M completion tokens. The persistent token ledger is limited to 190,000 tokens, including the recorded 1,139-token DeepSeek canary. Charging every token at the larger completion ceiling gives a conservative maximum of $0.2432, below the Phase 6 $0.25 cap. A request reservation is charged before each call; over-limit requests stop before network access. Provider-reported cost is recorded when returned; missing cost remains `null`.

The run is prepared on the same frozen 37-chunk corpus and Phase 4's 44-query system-judged set. Dataset SHA-256 is `ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65`; 6 of the original 50 questions remain unjudged. The baseline for these 44 queries was filtered from the frozen 50-query lexical ranking and rescored; its R@10/MRR/nDCG exactly reproduce Phase 4's lexical result. A Phase 6-only comparator accepts the system-evaluated provenance while keeping `gold_eligible=false`; self-comparison validates the 44-query/corpus linkage. A second baseline adapts Phase 4 LightRAG's captured citation order for the same 44 IDs (880 citation slots, all within the frozen corpus); no Phase 4 data/service was changed.

A dedicated container `b2c-phase6-hipporag-deepseek` is ready on the isolated `b2c-phase6-openrouter-egress` network, limited to 16 CPUs/48 GiB. Source, corpus, BGE-M3, and repository mounts are read-only; the OpenRouter secret is mounted read-only at `/run/secrets/openrouter_api_key` and is read only when the launcher executes. The container has not sent a Phase 6 provider request. Python 3.12.14, HippoRAG 2.0.0a5, OpenAI 2.54.0, Torch 2.14.1+cpu, Transformers 5.18.0, `pip check`, and OpenRouter DNS all passed. Focused tests: **42 passed**; `git diff --check` clean.

The launcher and its SHA-256 (`66f87aadb1b6e4900dd3f9f0d651995e64c3bbb1d25391c0c5771008900aa7d0`) are outside the repository at `/mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-scratch/launch-phase6-deepseek.py`. It will run indexing and retrieval, then compare against both lexical and Phase 4 LightRAG citation rankings at k=1/3/5/10. At the latest checkpoint, Phase 4 had started a separate OpenRouter DeepSeek run, so Phase 6 inference remains held to keep the shared account within the unified $2 ceiling. The comparative run and its retrieval metrics are **not yet complete**; Phase 6 is not passed or closed.

### Paired Phase 4 citation replay (offline sanity check)

Using the 44 matching query IDs, I adapted Phase 4 LightRAG's captured citation order into the evaluator's ranking schema; all 880 citation slots map to the frozen corpus. Replaying those citations against the system-judged qrels reproduces Phase 4's dense metrics exactly: R@10 0.9535, MRR 0.8036, nDCG 0.8398. Against lexical, the replay delta is +0.1161 MRR and +0.0809 nDCG at k=10, while recall@10 is -0.0233. This confirms the same-query/corpus adapter, not a new live LightRAG run; labels remain automatic/non-gold and the single unanswerable query gives a non-generalizable false-positive rate of 1/1.


## Phase 3 bounded research-gate closure (2026-10-05)

The fixed two-book Phase 3 research gate is closed. The frozen 50-question corpus received 50 source-grounded machine judgments (49 supported, one absent); full offline comparison validated the exact source/provenance across all rows and measured lexical, BGE-M3 dense, RRF hybrid, and RRF+BGE reranker. On 49 answerable questions, all methods exceeded the provisional Recall@10 >= 0.85 and nDCG@10 >= 0.60 targets; reranked RRF scored 0.9898 / 0.8947, with 0.9796 complete-evidence@10 and 11.595 s mean query latency. The bounded lexical abstention result remains 0/100 held-out false answers with 3/14 supported false abstentions.

The course-QA application path now calls QueryRetrievalService within the already-approved lesson/group/course chunk scopes. Runtime mode and top-k are included in workflow/cache identity and the result records them. Lexical is the default; optional BGE-M3 hybrid RRF is wired through docker/compose.retrieval-hybrid.yml with an internal-only embedding service and secret-file auth. The two-book software fixture verifies both source roles reach retrieval and QA citations retain the correct book, role, and page. Focused checks: 84 passed. No live hybrid service was started: Compose validation is currently blocked by the shared base compose error api depends on undefined service minio. The current service has no BGE reranker; reranking remains evaluator-only because measured mean CPU latency is 11.595 s/query and runtime tail latency/memory remain unmeasured. See eval/reports/phase-3.md.

Judgments are machine-evaluated and `gold_eligible=false`: 44 Mercury-2 labels and six DeepSeek V4 Flash 0731 labels. No human-gold, commercial-quality, generalized-retrieval, or production-release claim follows. Human curriculum review, representative human-gold evaluation, broad no-answer/conflict coverage, API authorization, scale/reliability/security, and dense/hybrid threshold calibration remain open. Phase 2 remains the current open acceptance phase. Details and hashes are in `eval/reports/phase-3.md`; frozen artifacts remain on the B2C data disk.

## Phase 6 bounded research decision (2026-10-05)

The corrected cost ledger removed the false quota stop, and a uniform HippoRAG run completed rankings for all 44 frozen queries over the same 37 chunks. At k=10, HippoRAG Recall/MRR/nDCG were 1.0000/0.7054/0.7791; RRF hybrid scored 1.0000/0.7929/0.8440, and RRF+BGE reranking scored 1.0000/0.8605/0.8962. Attribution integrity was 1.0; graph index time was 203.77 s, mean query time 0.714 s, peak RSS 2.78 GiB. Labels remain automatic/non-gold and the one no-answer query remains a false positive for all systems. Bounded research decision closed NO-GO for production adoption; product/production acceptance not passed. Full evidence, hashes, cost accounting, and limitations are in eval/reports/phase-6.md.

The isolated HippoRAG run indexed 37 frozen chunks and emitted rankings for 44/44 automatic queries (index 193.68 s; mean retrieval 2.112 s/query; peak RSS 2.78 GiB). At k=10, R@10/MRR/nDCG were 0.9767/0.7209/0.7844, compared with lexical 0.9767/0.6875/0.7589 and Phase 4 LightRAG replay 0.9535/0.8036/0.8398. Some reranker requests were stopped by the conservative token ledger and HippoRAG internally fell back to dense passage retrieval; these are mixed-path diagnostic rankings, not a clean paired adoption test. All metrics remain machine-evaluated/non-gold, and the sole unanswerable case was a false positive for every system. Phase 6 is **not passed and not adopted**. Provider cost was about $0.021414 including the prior canary; the isolated container is idle and the shared endpoint is released. See `eval/reports/phase-6.md` and data-disk artifacts for details.



### Phase 6 refresh — LightRAG verification (2026-10-05)

Re-scored the exact 44-query Phase 6 subset offline from the saved, completed Phase 4 LightRAG retrieval-only run (50/50 successful requests, verified source-artifact SHA and exact top-20 citation match); zero new inference requests were made. Same-44 diagnostic metrics: LightRAG R@10/MRR/nDCG 0.9535/0.8036/0.8398; lexical 0.9767/0.6875/0.7589; saved hybrid RRF 1.0000/0.7929/0.8440; saved RRF+BGE 1.0000/0.8605/0.8962; HippoRAG 1.0000/0.7054/0.7791. The current LightRAG service is not live-benchmark ready: read-only status showed 2/37 stored chunks processed and 35 failed (`User cancelled`). No query or reindex was sent. These are automatic diagnostic qrels, not a product quality gate. Decision remains NO-GO for graph production adoption; full evidence is in `eval/reports/phase-6.md`.

## Phase 4 final disposition (2026-10-05)

The bounded Phase 4 research evaluation is closed with a **NO-GO for production graph retrieval**; the acceptance gate did not pass. LightRAG mix on the frozen 37-chunk/50-query diagnostic scored Recall/MRR/nDCG@10 0.960/0.7918/0.8326 at 2.683 s mean, while hybrid RRF scored 1.000/0.7986/0.8484 at 1.247 s mean and RRF+BGE reranker scored 1.000/0.8800/0.9107 at 31.38 s mean. The automatic entity agreement F1 was 0.3119; T-KG-02 ER F1 was 0.7273 against threshold 0.90; T-KG-05 yielded zero accepted supplementary qrels from 39 primary questions and the one-pair generator abstained. T-KG-03, T-KG-04, and T-KG-06 passed isolated checks. All labels are machine-generated diagnostics, not gold. Keep the production retrieval path on RAG. Full evidence and provider cost accounting ($0.0831219966 recorded total, within the Phase 4 $0.25 cap) are in eval/reports/phase-4.md and eval/reports/phase4/.

## Phase 5 current disposition (2026-10-05)

The approved two-book sample has a reproducibly audited final course-QA artifact: 12/12 scopes and 65/65 items pass all six stored automated checks; 50 lesson questions are unique. A new read-only artifact auditor is at `eval/suites/phase5/audit_qa_artifact.py`; it and the Phase 5 suite pass, and all unit/contract tests pass (292). This closes the bounded generation/provenance sample only. **Phase 5 remains open** because the broader plan's lesson-summary/glossary/MCQ contract path, declared linter/solver/review-quiz/FSRS gates, and calibrated faithfulness/negative fixtures are not connected or proven. Human review is not a requirement during question generation and is deferred to later experimentation as directed. See `eval/reports/phase-5.md`.

### Phase 5 implementation update (2026-10-05)

The lesson-enrichment path is now wired through the course-QA activity and artifact, with deterministic review-quiz/FSRS helpers; full unit+contract regression is 312 passed. A bounded OpenRouter Qwen free canary failed local `ExactQuestions` schema validation and saved no artifact; its raw response was not cached/logged. The B2C daily budget ledger shows 4 reservations, one above the assigned three-request cap. The proposed DeepSeek free alternative was not listed for this key and received no inference. One explicitly authorized DeepSeek paid canary then used 2,760 input + 2,048 output tokens and reported $0.00066684 cost, but hit the 2,048-token output cap and saved no artifact; no retry/fallback followed. Phase 5 remains **NOT CLOSED** because the live enrichment/solver path has no passing model artifact, semantic support/duplicate calibration remains incomplete, and contradiction/out-of-scope end-to-end negative gates remain unrun. Human review stays deferred and is not a generation blocker. See the latest section in `eval/reports/phase-5.md`.

## Phase 7 translation/QE checkpoint (2026-10-05)

Translation pipeline unit checks pass (8/8). FLORES-200 diagnostic: Qwen free completed 3/3 rows at $0, chrF++ 39.562/BLEU 7.718 with exact numeric-digit retention 2/3; a later 30-row request received HTTP 429 and stopped without retry. One explicitly authorized DeepSeek V4 Flash 30-row sample completed in 34.529 s (2,044 tokens; provider cost $0.000518432), scored chrF++ 56.355/BLEU 29.460, and preserved numeric digits 30/30. The reproducible project invariant audit passed all 30 with zero violations, but fail-closed QE routed all 30 to human review. These are small automatic-reference diagnostics, not release evidence or a model-ranking claim. No permanent provider/runtime setting changed. Phase 7 remains **NO-GO**: approved bilingual termbase, runtime translation-activity/artifact integration, calibrated QE, and persistent review routing are incomplete. Full artifact hashes, quotas, and gate evidence are in `eval/reports/phase-7.md`.

### Phase 5 evidence update (2026-10-05)

The 65-item/twelve-scope QA artifact remains audited. Lesson enrichment, source-conflict negative controls, MCQ linter fixtures, review quiz, and FSRS have connected implementations with deterministic tests; unit+contract regression passes 317/317. The bounded DeepSeek enrichment canary produced no observable response or artifact, and actual usage could not be confirmed; it was not retried. **Phase 5 remains NOT CLOSED**: no verified live lesson-enrichment/solver artifact exists, and the calibrated T-SYN gates lack an independent labeled set. Human review is deferred to later experimentation and is not a generation blocker. See `eval/reports/phase-5.md`.


### Phase 4 free-route attempt update (2026-10-05)

Read-only OpenRouter discovery found 22 models explicitly priced at $0/$0; the selected Qwen free model returned HTTP 200 for one frozen-chunk canary with provider fallback disabled and provider-reported cost $0 (1,328 input / 512 output tokens). It hit the output cap and the local harness failed to parse/inspect the response, so extraction quality is unverified and no bulk retry followed. The account key is not marked free-tier; remaining account limit was not treated as a free quota. Phase 4 remains closed as a bounded research decision NO-GO for production graph adoption; the historical paired benchmark remains the quality evidence. See eval/reports/phase-4.md.



### Phase 4 free-Qwen canary follow-up (2026-10-05)

The single final parser-fixed canary returned a complete JSON response from the explicitly $0/$0 Qwen model (3,087 input / 762 output tokens, reported cost $0): 9 entities and 6 relations. Exact contiguous source quotes passed for only 5/9 entities and 2/6 relations. The source-grounding gate failed, so no 37-document reindex or production wiring was started. Sanitized output/hash are on the B2C data disk; the full data and a repeated same-44 offline scoring replay are in eval/reports/phase-4.md. The replay reproduces the saved LightRAG vs lexical result and does not use the one-chunk canary as retrieval rankings. Phase 4 stays research-closed NO-GO for production.



### Phase 5 bounded implementation checkpoint (2026-10-05)

The connected enrichment/pedagogy implementation and deterministic unit/contract gate pass **317/317**. The final live attempts were one free and one explicitly authorized paid request, each capped at 4096 output tokens with no retries. Both returned `LLMOutputTruncated`; the paid provider reported $0.00128124 and the conservative request ceiling was $0.0054038176, within the $0.02 cap. No lesson-enrichment artifact was saved or audited. **Phase 5 remains open** for a verified live LessonDraft artifact/source audit and independent T-SYN calibration; human review is not required during creation. Details and evidence are in `eval/reports/phase-5.md`.



### Phase 7 termbase implementation update (2026-10-05)

A data-backed, opt-in AGROVOC EN→AR resolver is now connected to the translation pipeline primitive. It requires explicit agriculture-domain confirmation, a unique preferred mapping and bilingual definitions with contextual word overlap; ambiguous/out-of-domain candidates are withheld. The actual AGROVOC concept “soil” resolved to “تربة” in the pipeline integration test and was injected only when the term appeared in the chunk. Out-of-domain, mismatched-definition and missing-definition cases were rejected. Focused tests: 11 passed. The licensed dataset and rights/hash manifest remain on the B2C data disk; runtime defaults and production services were not changed. This is a domain-specific candidate integration, not a general approved termbase or Phase 7 closure. G3 remains open pending representative glossary evaluation and ArtifactStore provenance persistence. See eval/reports/phase-7.md.



### Phase 5 live artifact and solver gate update (2026-10-05)

A free Nemotron lesson-enrichment canary is now saved and read back from MinIO/data disk with SHA-256 `d38c9d900ccd495f7bb3ec16b7c17ea3ab0214d9bfce68e2fdfed8ea3718d3f4`; contract/source/citation/scope/Bloom checks passed for the one-lesson sample. A separate solver artifact scored 2/3 with context and 2/3 blind, below the enforced >=0.95 / <=0.30 gate, so the connected generation path correctly remains fail-closed. Citation IDs are now schema-constrained to allowed lesson chunks, and answer indices were removed from the evaluation prompt. Full unit+contract regression: 320 passed, 0 skipped. **Phase 5 is not closed** pending a passing solver run and independent T-SYN calibration; human review remains deferred and is not a creation prerequisite. See `eval/reports/phase-5.md`.

### Phase 5 alternative free-model check (2026-10-05)

Alternate 20-question product-path canaries did not yield a completion. OpenRouter Qwen 3.8 Free was listed at $0/$0, but its only attempt returned HTTP 429 and stopped without retry. CodeCraft model metadata returned 403; TokenRouter metadata returned 403/401. Gemini 2.5 Flash-Lite is listed by the Gemini API key and Google documents a $0 Developer API Free Tier for text; its one request returned HTTP 404 with zero usage. Atria's protected inventory key was validated by GET `/v1/models` (200, `Atria-Dawn-Preview`, no price/balance metadata); one canary returned HTTP 422 with no reported usage or completion. No inference retries/fallbacks were sent. The canary fixture was tightened to use source-anchored MCQ options; full unit+contract suite passes **336**, with zero skips, and `git diff --check` is clean. The saved Nemotron solver evidence remains below threshold; **Phase 5 remains open**. See `eval/reports/phase-5.md` for route checks and exact limits.


### Phase 7 general terminology update (2026-10-05)

A reusable general EN↔AR candidate base has been downloaded and connected to the translation pipeline primitive: 709 bilingual Wikidata concepts (structured CC0 data, 2026-09-30 snapshot) plus the existing AGROVOC agriculture-only layer (CC BY IGO 3.0). The resolver checks exact labels, bilingual definitions, contextual overlap and ambiguity; it rejects unresolved conflicts and injects accepted terms only when they occur in the chunk. The probability example resolves through Wikidata while soil resolves through AGROVOC only when agriculture is confirmed. Focused translation tests pass 14/14. No production runtime/default was changed and no model inference was used. G3 and Phase 7 remain NOT PASSED: this is a bounded candidate seed, without representative book-glossary coverage/precision evidence or ArtifactStore provenance persistence. Full hashes, licenses and limitations are in `eval/reports/phase-7.md` and the B2C data-disk termbase manifest.\n\n### Phase 5 Bloom gate and free canary update (2026-10-05)

The product prompt now requires application/analysis transfer tasks with exact citations, and a pre-artifact lint enforces at least 80% apply/analyze (rounded up). Full unit+contract regression passes **352/352**, no skips. One Cohere North Mini Code free canary (catalog $0/$0) returned 16 of 20 required questions; all 16 were labeled apply. The schema rejected the short batch before artifact creation; raw response was stored/read back and hashed, with provider token usage unavailable. No solver call, retry, paid fallback, or final artifact. **Phase 5 remains NOT CLOSED** pending a complete verified live lesson artifact/solver result and remaining declared calibration gates. See eval/reports/phase-5.md.


### Phase 5 proposal-builder and provider checkpoint (2026-10-05)

Question generation now has an application-owned distractor builder: the model proposes cited answer facts, while code validates answer anchors and constructs balanced, shuffled choices with per-option citations. Offline unit and contract regression: 356 passed; focused course-QA activity tests: 4 passed. The one Laguna S free canary was rate-limited before completion. Atria model discovery returned HTTP 200 but no pricing or balance fields; one direct chat-completions request without a response_format field timed out without a response or usage metadata. Neither route produced a raw completion or lesson artifact; neither was retried. Phase 5 remains open pending a passing live artifact with source, Bloom, context-solver, blind-solver gates and readback, plus independent T-SYN and remaining planned quality evidence. Human review remains deferred and is not a generation blocker.

### Phase 5 saved-raw artifact and solver checkpoint (2026-10-05)

A 20-question Seat Weaving canary was rebuilt offline from saved raw proposals with source-grounded options and per-option citations, saved to MinIO, then read back with matching schema/bytes. It contains 15 apply and 5 analyze items. Lesson SHA-256: `69d2694bb4f5b5a2bc1569322c5f68fe2eff2c8af85d91aaa4768421519025a4`. In the bounded DeepSeek solver pair, the context response was truncated (4,557 input / 400 output tokens; reported `$0.0005812664`, no scorable answer). Blind scored 20/20 (2,218 input / 64 output tokens; reported `$0.0001156336`), above the allowed blind threshold of 0.30; therefore the solver gate fails and context quality is unknown. Provider credits usage did not reflect the per-response cost change. Regression evidence: 356 unit+contract tests passed, no skips; focused orchestration checks 4/4. **Phase 5 remains open** pending a passing solver gate and independent T-SYN/broader T-PED calibration. Human review during generation is not required. See `eval/reports/phase-5.md`.


### Phase 5 bounded implementation closure — 2026-10-05

Phase 5's current automated implementation slice passed: the source-grounded numbered-procedure builder creates uniformly formatted, individually cited choices; the max option-length spread is eight characters; lints reject longer disparities. The saved/read-back Seat Weaving v2 artifact has 20 apply questions, balanced answer positions (5 each), and zero option-length spread. With-context solver scored 20/20 and blind scored 5/20 (25%); blind input contained only question IDs, stems, and options. Reported inference cost was $0.0002509056 total, below the $0.005 preflight ceiling. Unit+contract regression: 360 passed, no skips; whitespace check clean.

**Disposition:** close the bounded Phase 5 implementation slice. Gate G2 remains not passed: no independent held-out labels exist yet for T-SYN faithfulness/duplicate calibration or broad T-PED quality claims. The 20-item source-specific canary is not a population-level guarantee. Human review during creation remains deferred and is not a blocker. Evidence and artifact hashes are in `eval/reports/phase-5.md`.

## Owner decision update — 2026-10-05 (app-build scope)
The owner has closed the current build/preparation checkpoints for **Phase 5 (bounded enrichment implementation), Phase 6 (optional HippoRAG research decision: do not adopt), and Phase 7 (terminology preparation)**. This is a scope decision to proceed with app construction and defer broad quality evaluation to app trials; it does not rewrite failed/unrun gates as passed. Phase 5 G2 independent corpus calibration and Phase 7 G3/G4 end-to-end translation/QE/provenance acceptance remain deferred, not passed.

**Audio model selected:** Fasih-TTS-V1 for B2C app integration, provisional CPU threads 4/4 based on three paired full-CATT runs (median 3.45x speedup; automatic CER 3.77% vs 5.35% at 28/4). No Fasih provider is integrated in the app yet; Phase 8 release/audio-quality gates remain open for app trial.

Phase 2 remains the current release acceptance phase. Phase 9 and 10 preparation is being pursued in parallel by separate agents; neither workstream can imply Phase 2 or release acceptance.

### Reboot recovery and automatic B2C start — 2026-10-05
After the reboot, the existing VHDX was verified mounted and data directories were intact. Added and verified a delayed logon task that waits for the exact mount marker and Docker before starting only the default `b2c` Compose project, preventing the prior disk/mount race. Default stack HTTP checks: API, Temporal UI, Grafana and Langfuse returned 200; Prometheus reported healthy. Grafana provisioning was aligned to the existing persistent Prometheus datasource UID. Evidence: `eval/reports/reboot-recovery-2026-10-05.md`. Optional LightRAG remains a separate research profile and is not in default autostart.

### Phase 9 serving preparation recovery — 2026-10-05
Fixed invalid PostgreSQL SRS advisory-lock keys and tightened byte-range parsing; added learner isolation/release/audio/quiz checks. Combined API regression is 381 passed, zero skipped. Phase 9 remains preparation, not release-passed: production auth, release/audio integration, client/APK and device E2E remain. Import-boundary lint retains one existing pedagogy→synthesis violation. Evidence: `eval/reports/phase-9.md`.
The delayed logon task now also restarts B2C cAdvisor after Docker is ready and verifies that Prometheus exposes B2C-only container memory data (15 series at verification); the scheduled run completed with result 0.

### Phase 10 operational checkpoint — 2026-10-05
The B2C-only Prometheus/Grafana path is live; four configured scrape targets and two local alert rules were verified. The dashboard uses the existing persistent datasource UID, and cAdvisor now exports 15 B2C memory series after a post-start restart. A second-volume C: checkpoint restored to an isolated PostgreSQL container and temporary MinIO bucket: 11 tables/519 rows, 348 object hashes, and 168 ArtifactRefs read successfully. Phase 10 acceptance remains open for notification delivery, encrypted off-host 3-2-1/retention, whole-system restore, broader failure/load/rollback drills and D-017 app evidence. See `eval/reports/phase-10.md`.


## Android remote API access status (2026-10-05)
The B2C API now enforces a private bearer credential for every route except GET /health; the token is stored outside the repository and mounted read-only. The operator APK has the current HTTPS Tailscale hostname and token embedded at build time. Tailscale Funnel is active at https://desktop-3khe91s.tail7385b1.ts.net and proxies to the loopback API on port 8100; the HTTPS health endpoint returned ok. The API remains loopback-only; port 8100 is not exposed directly.

### Android operator APK 0.2.0 — 2026-10-08
Built the refreshed RTL operator interface with larger rounded controls, clear dropdown selectors, five live stage cards, 10-second automatic refresh, server-side workflow-ID recovery from the per-book progress endpoint, and one-button sequential execution to the selected output. Selecting a new PDF clears the old book's local workflow IDs so the run cannot silently reuse the previous book. APK package: com.book2course.operator, version 2 / 0.2.0, minimum Android API 24 (Android 7), armeabi-v7a only. Build passed with ./gradlew assembleDebug; aapt confirmed package/version/min SDK and the APK contains only the 32-bit ARM library. SHA-256: 858ee5ba8bb378015c923cb21cc920bc14289dd29e884351447813819bb5261c. Uploaded and read back in Drive: https://drive.google.com/file/d/1WtyD2A9zYVftaxztQ_11wX5yjUK_7JKP/view?usp=drivesdk. No Android device/emulator was attached during this build, so installation and on-device flow remain unverified; Phase 9 is not closed.

### Live generation recovery and comprehensive review — 2026-10-08
The one-hour whole-book QA timeout and unbounded default retry loop were diagnosed. Deployed a separate external-generation worker using DeepSeek via OpenRouter and routed new API QA/translation requests there. Existing QA/translation workflow IDs were retained on migration; source-validated QA recovery restored9scopes/45questions with original model provenance. QA is running; translation is queued. This is not a final output completion claim. Unit+contract regression419passed; additional failed-content API regression passes. Evidence: eval/reports/2026-10-08-external-generation-recovery.md.
Comprehensive review and proposed hardening sequence are saved in eval/reports/2026-10-08-comprehensive-review.md and docs/proposals/2026-10-08-hardening-plan.md. Earlier historical phase/embedded-token notes above remain historical; APK0.3.2 source uses protected token storage and removed the embedded credential.

### Scheduled external QA and H0 repair check — 2026-10-08
At the five-hour check QA was active on OpenRouter/DeepSeek (attempt1), 236 accepted questions in45 successful scopes,11 failed scopes,56/102 processed. New independent QA checkpoints increase; the original45 remain. Failure diagnostics identify token truncation, unknown chunk IDs and inexact quotes. OpenRouter key-meter usage rose $0.01133 since the last scheduled snapshot; use this key-level reading for cost, not lower app cache cost accumulation. One unseen actual lesson passed all5 QAs with reasoning disabled (52.46s, cost reported $0.0007231), supporting a controlled future setting only. Config is prepared but not deployed so the active run is unaffected.
R15 fix and tests are in source: normalized adjacent OCR headings merge only without intervening substantive body blocks. Full unit/contract422 passed, Compose config validates. A legacy idle B2C worker was restarted after verifying no running workflow depended on it, stopping orphaned local requests; external worker remained running. Translation remains queued behind QA. Detailed UTC counts, failure causes, pricing-meter readings, timing and deployment dispositions: eval/reports/2026-10-08-external-generation-recovery.md.

### 2026-10-08 Android runtime environment update

Isolated Android8.1/API27 emulator is booted under Windows SDK with WHPX and dedicated ADB5038; reusable launcher/UI smoke tooling is available. A real startup crash from duplicate label attachment was found and fixed. Serialized install/launch/five-screen navigation/background-resume/100-event crash smoke passed; screenshots/XML/logs are under `eval/android/2026-10-08-api27-pass`. Default ARMv7 0.4.0 APK rebuilt and signature/ABI verified. This is UI runtime evidence only; authenticated pipeline acceptance remains separate. See `eval/reports/phase-9.md` and `android/operator-app/TEST-ENVIRONMENT.md`.
## Model preparation Settings (2026-10-08, source only)
Per-stage lifecycle/catalog API and general APK Settings are staged with 34 focused/API regression tests passed and Java compilation successful. Existing live jobs/services/APK unchanged. Execution-profile wiring to Temporal activities remains incomplete and explicitly disabled; source deployment is not approved by these checks. See docs/proposals/2026-10-08-model-lifecycle.md.



### Translation auto-recovery update — 2026-10-08
The EN→AR translation numeric-token failure was traced and hardened: digit scripts normalize consistently, model requests get a corrective batch retry, failing batches are deferred while the rest progresses, then retried, and unresolved batch failures remain checkpoint-resumable. A separate updated external-generation worker is running; existing workers and current jobs were left intact. Live progress at 19:34 UTC: book text 12/776 OCR segments; course attachment 144/888 segments. Translation/audio remain in progress. Details and usage-meter limits: `eval/reports/phase-7.md`.

## Translation track counters update (2026-10-08)

The Android operator APK and source now track book-body and course-appendix translation independently, including completion, active work, deferred units, failed units, current unit, and model. The translation engine retries deferred units after processing later units and keeps each successful unit checkpointed. Python compilation and the 32-bit Android APK build passed; updated API and generation-worker images were built but not deployed to preserve live translation workflows. The latest observed appendix workflow was RUNNING at 388/888; the previously tracked book-body workflow is FAILED with only wrapper type `ActivityError` available from its status route. Local-model concurrency remains unmeasured; keep the two independent workflows serialized at the local inference endpoint until throughput and memory are measured. See `eval/reports/phase-7.md`.

### Body translation recovery (2026-10-08)

The book body had not launched from the Android orchestration because it waited for appendix translation completion. The backend also silently failed to persist the separate `book_translation` stage due to a missing route mapping. The route mapping is fixed in source and the API image is rebuilt but not deployed. The saved parsed book is intact (677 pages/776 blocks); body translation resumed from its saved checkpoint and is now RUNNING at 16/776. Appendix translation remains RUNNING at 784/888. Both workflow IDs are now recorded independently in the book-progress registry. See the Phase 7 report for evidence.

The follow-up APK 0.4.5 (32-bit, versionCode 11) starts the body and appendix translations together and persists/reuses each workflow ID. It is available on Drive. Latest observed progress: body 24/776 and appendix 852/888; both workflows RUNNING. Current provider is external DeepSeek. API image contains the missing `book_translation` route mapping but remains staged to avoid service restart during active work.

### OCR/index display recovery — 2026-10-08

The live book-main OCR and index status endpoints return 503 because their older Temporal histories are unavailable. A read-only check of the staged recovery implementation verified the exact registered ingestion through its committed approved-outline envelope: 677 pages and 776 text blocks, with the draft outline intact. The registered index identity matches the approved outline and default parameters; all 397 persisted PostgreSQL chunks match deterministic reconstruction from that same approved source. Legacy indexing has no immutable snapshot, so recovery explicitly describes the persisted index rather than claiming historical workflow execution evidence.

Source fixes recover extraction from approved source references, recover exact registered indexes from snapshots or fully matched legacy chunks, reuse status recovery for chunk display, and report recovered book-stage states. Terminal ingestion/index status no longer reads unrelated translation checkpoints. New index registrations persist parameters for future recovery. Python compilation passed; no tests or inference were run and no service/workflow was restarted. API deployment is required before the mobile display can use these changes.

### Translation numeric-token failure — 2026-10-08

The course appendix workflow failed after the model repeatedly dropped a numeric token in a generated question segment. 880/888 translated segments remain available in immutable checkpoints; 8 are not checkpointed. Book-body translation remains an independent RUNNING workflow. Source now protects numeric tokens with immutable placeholders and bumps the translation fingerprint to v4, allowing compatible v3 checkpoint reuse on resume. The updated worker image built successfully as b2c-generation-worker:numeric-protection-candidate; no worker restart or resume has occurred.


### APK library and progress display update — 2026-10-08

APK 0.4.6 (versionCode 12, Android 7+, armeabi-v7a only) built successfully and was uploaded to Drive: https://drive.google.com/file/d/1lhDOUyxM3Pj1aNrslIHPYqb5vO_Nmlnt/view?usp=drivesdk. SHA-256: 78c238646f742907c818c13302867ec45e8b84e552f2b476227a7ce3ed372c74. The library is now searchable, shows book cards and selected-state persistence; QA status shows successful/failed/total scopes, saved question count and lesson coverage. OCR and result windows are larger, RAG chunks have a readable view, and the raw completion-state bug is corrected. Java compile and APK assembly passed; no device smoke was run. OCR/RAG API recovery code remains staged pending safe deployment after active translation work.


### Translation retry, unit preview and correction versions — 2026-10-08 (source only)

The API persists each original translation input for future runs and can recover older exact inputs from the Temporal workflow-start event. Resume requests must match the original course, source ArtifactRef, source kind, languages and token limit. Terminal status counters reconcile successful segments against source-scoped immutable batch checkpoints; confirmed failed IDs and unsaved/retry segments are distinct. The units endpoint exposes each original source segment, citations, translated text and saved state.

Operator corrections are allowed only for terminal workflows, including completed ones. Each edit appends a unique immutable revision; numeric preservation is checked. Explicit resumed runs snapshot revision IDs into their workflow identity, seed corrections before generation and include correction provenance in a new final artifact/cache identity. Existing checkpoints and output artifacts are not mutated. Active workflows cannot be edited.

Android source adds per-track resume controls using only the server-validated original request, selectable unit preview, and terminal-only edit/save controls. Successful, confirmed failed, and unsaved/retry counts are displayed separately. Python compileall, API/orchestration imports, and compileDebugJavaWithJavac passed. No inference, tests, service deployment/restart, live endpoint verification, or APK assembly occurred in this change. The active body translation and failed appendix workflow remain untouched; these features require a coordinated API/worker rollout and a subsequent APK build before mobile use.

The targeted-operation gap is implemented in source: isolated single-unit retranslation verifies the terminal parent, exact original request, source artifact and selected source-text hash, translates exactly one unit on the generation queue, and stores an immutable candidate independently of checkpoints. Candidate review/edit is optional; normal translation has no human gate. Explicit acceptance writes a unique immutable override for later full resume. The operator can compare source/current/candidate translations, save further append-only candidate edits, reopen a revision and explicitly accept it. Candidates remain reviewable after workflow-history retention expires. Python compile/import and Java compile passed; no inference, tests, service restart/deployment or new APK assembly occurred. Live appendix/body work remains untouched.

Revision selection is reversible: units expose the original machine checkpoint and all immutable accepted revisions; explicit selection appends a new selection revision. The operator can preview/select an older accepted version or revert to the original machine text without deleting/overwriting any checkpoint or revision. Selection is applied by a later explicit resume; it does not gate or mutate active automatic work.

## Model selector and local Gemma4 preparation (2026-10-09)

The installed, previously tested Gemma4 E4B Q4_K_M is catalogued as a limited experimental option. The APK settings screen shows evaluation status and disables making a model the default unless a task worker is connected. A separate opt-in Compose route reuses the existing Gemma4 store read-only and sends work to an isolated Temporal queue without external credentials. The base deployment keeps this route disabled. RAG runtime remains lexical; tested BGE/EmbeddingGemma comparisons and LightRAG/HippoRAG experiments are not wired into the deployed worker. Focused API tests (12/12), source compilation, and Compose configuration validation passed. No service, active workflow, or APK distribution was changed; Gemma4's actual container inference and worker acceptance remain pending a safe deployment window. Evidence: `eval/reports/gemma4-readiness-2026-10-09.md`.

Follow-up verification found that isolated retranslation was overwriting the completed translation's saved task queue, which could route a Gemma candidate to the external DeepSeek worker. The override was removed so the candidate inherits the exact saved original model/provider/queue. The APK settings no longer present external key replacement as a working feature: adding an arbitrary provider/model still requires worker-side dynamic routing and secure catalog wiring. The model list hides `not_tested` entries; RapidOCR/PP-OCRv6, Surya 2, and Fasih are marked limited tests with their restrictions stated from saved evaluation reports. Python compilation, model lifecycle tests (12/12), Compose config validation, and Java compileDebugJavaWithJavac passed. No inference, API call, service restart, active-workflow change, APK assembly, or upload was performed.


## Owner-requested next-book behavior (2026-10-09)
See D-027 and docs/proposals/2026-10-09-course-content-boundaries.md: skip only explicit publisher/editor front matter when assembling lessons, keep headings as labels rather than standalone content, and prevent title/name questions. Source preparation must not modify the active book or restart its workers; activate after its translation and audio finish, before the next book.


## Reboot connection and model recovery (2026-10-09)
Disk mount, B2C core services, three deployed model services, authenticated remote library, and Tailscale Funnel were verified after restart. Startup now recovers model containers whose bind mounts failed before disk attachment. The dead book-body activity was requeued without deleting checkpoints; a checkpoint-tail listing defect was fixed and deployed only to the generation worker after 25 focused tests passed. Appendix is complete at 888/888; body continues under the same workflow/run. See `eval/reports/reboot-recovery-2026-10-09.md` for limitations and runtime evidence.

Owner follow-up required verifying restart recovery before inference: generation was temporarily paused; real-book routing audit confirmed 614 accepted saved units are never resubmitted and only 162 missing/suspect units are selected, with zero external inference/artifact writes in the probe. Monotonic restored counters and a B2C-only lost-worker boot recovery hook were implemented, tested, and deployed. Final thin worker image changes only translation checkpoint loading/progress code. Runtime continuation counts are appended to the reboot report.

Public APK connection verified on persistent Funnel HTTPS port 8443 at 2026-10-09T12:13:44.032421+00:00; public port 443 remains stalled. See reboot recovery report for tests.

## Translation partial-result rollout

At 2026-10-09T12:22:38.207240+00:00 the fixed generation-worker and API thin images were deployed after confirming zero running B2C translation/QA workflows. Baseline runtime SHA256 values matched the isolated staged manifest; originals retained under :before-partial-results-20261009. Only generation.py, orchestration translation.py and API translation routes changed. A focused independent rerun passed 16 tests (the agent's full focused set passed 39). Source and numeric ordered equality/completeness checks remain strict. Exact raw number responses can pass without placeholder markers; mixed/wrong numbers still fail. Exhausted units return a durable partial artifact after the remaining units are processed rather than failing the whole completed batch traversal.

Post-rollout health is OK, completed appendix remains 888/888, body has 627 validated saved /776, 5 suspect and149 retry units. The original FAILED workflow exposes a valid resume_request. No new translation was queued: the owner will trigger the existing APK button "استئناف المقاطع غير المحفوظة" under book-body translation to validate the UI path. APK changes/failover build are deferred by owner. Known subsequent UI gap: this APK currently enables retry only for FAILED/other failed terminal workflows or manually corrected COMPLETED workflows; a newly persisted partial COMPLETED result needs explicit partial-aware UI handling in the next APK. No claim of that future UI support yet.

## Explicit translation retry cache isolation — 2026-10-09

APK-triggered resume 465f7ce35249e8171ab56fabb0b50ecad8bc536649c5a6674c5841f76a303f5e committed a partial result:629/776 accepted,147 outstanding. All627 previously valid units remain present; two additional units were saved. Active zero at result_committed is terminal, not a stuck activity.

The gateway caches schema-valid output before translation numeric/completeness checks. The previous translation prompt version was shared across workflows, allowing explicit retries to replay rejected responses. A read-only diagnostic on six failed source units reconstructed v4 request keys:15 cache responses were returned, showing7 numeric-placeholder failures,5 numeric-sequence mismatches and3 content-omission flags (response-level counts, not a corpus-wide error distribution; some responses had multiple items). No external inference or artifact writes occurred; temporary skip placeholders were in-memory only and never adopted.

Explicit workflow request namespaces now use phase7-translation-v5:workflow-{workflow_id}. A new owner-triggered resume obtains fresh responses for remaining units while saved valid checkpoints still avoid requests; same-workflow restart keeps its own cache.16 focused tests passed, including real activity calls proving disjoint request versions between failed/partial parent and explicit child, only missing IDs submitted and distinct partial/complete artifacts. A thin worker image retry-cache-scope-20261009 was built; publish only after verifying zero running generation workflows. No new translation workflow is started automatically; owner requested UI-button testing. The current APK's handling of COMPLETED partial results still needs its separately deferred partial-aware retry-button change.


## Complete root review of translation failures —2026-10-09
Root personally compared all154 original units in the historical failure union. Findings include heading/body omission, batch collateral rejection, source-number tokenization, genuine numeric corruption, injected markup and missing/duplicated table cells, including short fragments below the old completeness floor. The original request scope of one older cache record is unknown and is labeled as such. No inference or source-artifact mutation was performed for the review. Fixes/testing/publication remain active; no live completion claim. See `eval/reports/translation-all-unit-root-review-2026-10-09.md`.

## APK/live-workflow audit — 2026-10-09
API 27 emulator smoke and offline Android instrumentation pass. Local APK source adds direct per-book monitoring, separate outline/RAG statuses, and tested-only model filtering; it is not deployed or uploaded. Live read-only checks found the new PDF under the same `book-main` / “كتاب التجربة” identity as an older PDF, so the library hides/distinguishes neither version and progress stages can mix. Body translation is terminal partial at 763/776 valid, with 13 units requiring retry; appendix is 888/888. A separate MP3 workflow appeared during audit and was left untouched; its inputs combine older QA/outline with a newer ingest and have no book-translation artifact. Do not restart/deploy or mutate the active MP3 run. Full findings and remaining button/service gaps: `eval/reports/2026-10-09-apk-live-workflow-audit.md`.

## Book-body translation completed after validator repairs — 2026-10-10

Individual remaining-unit inspection exposed false numeric rejections from URL/newline tokenization, independently protected values merging into decimals, and normal prose incorrectly classified as a rating table. Corrected source passed108 focused translation/checkpoint tests and deployed only to the idle generation worker; new-book light-queue ingestion was unaffected. Resume with force_regenerate=false completed776/776 at2026-10-09T22:52:46UTC, zero failed/missing/retry/suspect units. Readback validated all776 unique source/output IDs and no structure/completeness issues. Final body artifact SHA256 b058582310584fb588fa6beb50de6cc64fbffbe7826ddd1e568b0283276e6c76; workflow b2c-translate-3c6dfd79c73a383973a0e2d88c2849d4cc1e333727d597dff1268bdcb88b055e. Source revalidation additionally repaired p79's previously missed omission of note labels9/10 without deleting prior checkpoints. No audio started by this corrective task. Evidence: eval/reports/translation-final-unit-validator-review-2026-10-10.md.

## APK0.5.5 launch correction —2026-10-10

Live API27 reproduction proved APK0.5.4 crashed at startup because buildStagesScreen inserted stageBookLabel twice (label() already inserted it). Changed this field to text() with one explicit addView and scanned all label/addView patterns. APK0.5.5/versionCode22 built, signature/minSdk checked, and exact-deliverable emulator installation, startup, five-tab navigation and background/resume passed with no AndroidRuntime exception. This is startup/navigation evidence, not complete backend-button testing. Previous APKs preserved; no processing worker changed. Evidence: eval/reports/apk-0.5.4-launch-crash-2026-10-10.md and eval/android/2026-10-10-api27-055-launch-fix/. Deliverable SHA256:105401ed4eb1659dd68f14cd45e5277f0d94b204170f68258fcc0e2ff5e4c0cc.

## Audio CATT overflow recovery (2026-10-13)
Fasih input overflow (3055 vs1024 positional tokens) fixed by sentence-first900-char contexts after number expansion, rejoined before synthesis. Isolated Fasih service updated behind scheduled-audio guard; health200/healthy.14focused tests passed. Current/historical audio APIs returned200; source-bound historical MP3 download verified. APK update-list can display historical saved tracks when current list is empty; ARMv7 debug APK built, device UI not validated. Evidence: eval/reports/audio-catt-capacity-recovery-2026-10-13.md.

## Live terminology validation and checkpoint counters — 2026-10-13
HY/API runtime accepts explicit Arabic inflections/definite equivalents of selected terms and contracted لل prefixes, while wrong intelligence senses الحكمة/الذكاء remain rejected. 112 focused translation/checkpoint/control tests plus15 API projection/control tests passed. Every currently deferred appendix unit inspected individually (17 IDs,34 raw responses): all17 recovered to durable checkpoints, Appendix1 113→131/888 with zero failed/suspect at verification. Original saved text baselines hash-equal. Same four workflow IDs resumed; Book2 body STARTED41/2620, Body1 queued76/776, Appendix1 queued131/888, Appendix2 publication queued644/644. API now reconciles stale queued progress to source-scoped immutable checkpoints; Body1 75/76 discrepancy fixed. Book2 anecdote13:2 has a genuine omitted editorial closing bracket; deferred alone while traversal continues. No guessed punctuation repair. No Docker-paused worker remains. Evidence: eval/reports/live-translation-terminology-audit-2026-10-13.md and associated raw/rollout JSON files.


## Android progress/audio compatibility — 2026-10-14
Source repairs resolve legacy QA status recovery without query parameters, restore source-bound GET course-audio listing, and route Android read actions through its independent monitor executor during active pipeline waits.10focused API tests passed. No deployment or device-verification claim; translations and saved outputs untouched. Evidence: `eval/reports/android-progress-audio-compatibility-2026-10-14.md`.


## API compatibility rollout and Android operator APK (2026-10-14)

Deployed the rebuilt B2C API image on the D runtime by replacing only b2c-api-1 with the exact existing Compose file set/profile. API health is healthy. Focused compatibility tests passed (10). Live authenticated smoke checks returned HTTP 200 for the book-scoped saved-audio listing and legacy QA recovery (PARTIAL, recovered from saved artifact); OpenAPI reports GET+POST for the legacy course audio route. The HY translation worker retained its container ID and start time. APK 0.5.7 (versionCode 24, minSdk 24/Android 7, armeabi-v7a only) built successfully; details and SHA-256 are in eval/reports/android-progress-audio-compatibility-2026-10-14.md. Emulator/device instrumentation was not run because adb is not installed in this WSL environment. The broad unfiltered legacy audio listing was slow (>60 s) during smoke testing; the APK's book-scoped route succeeded in 27.32 s, so this remains a performance follow-up.


Follow-up: API GET read timeout was raised to 120 seconds (health probe remains 4 seconds); APK rebuilt as 0.5.8/versionCode 25 for ARMv7/Android 7+, and uploaded to Drive. SHA-256 and verified Drive URL are recorded in the compatibility report.


## Restored-UI APK 0.5.11 delivery — 2026-10-14

VersionCode 28, minSdk 24, ARMv7. Signature verified; emulator AllControlsInstrumentation passed 695 fixture/control events with no live requests. Uploaded and metadata-verified in Drive: https://drive.google.com/file/d/1fKkMgRgttS_6ky89DBB3zQaczHEHHy7q/view?usp=drivesdk. SHA-256 and scope/evidence are in `eval/reports/android-progress-audio-compatibility-2026-10-14.md`. Translation remains active (Book1 body 227/776, appendix 403/888; Book2 body 118/2620, appendix 644/644) with 5 and 10 deferred units still awaiting their final retry pass.


## Deferred translation audit and safe recovery rollout"��y��y� 2026-10-16

Read-only audit and systemic recovery changes are recorded in `eval/reports/deferred-unit-audit-2026-10-16.md`. Book 1 body was 510/776 with 266 unresolved segment IDs; every persisted legacy failure had the same `LLMUnavailable` signature (240 page groups), indicating provider/unavailable-response failure rather than 266 independent language errors. Book 2 body was 575/2620 with 32 deferred page groups / 34 individual unsaved segments. Exact historical validator findings exist for 11 units (bracket, numeric ordering/count, and table-marker omissions); the legacy worker did not persist per-segment causes for the other 23, so its global last-error code cannot be assigned to them individually. The new runtime records per-unit failure category, validator/provider reason, and attempts, and scopes correction feedback to the affected unit.

The recovery source also retries transient network and HTTP 408/425/500/502/503/504 errors with bounded backoff, supplies same-page context for short OCR fragments, and retains validated older checkpoints across prompt-version changes so saved segments are not resubmitted. Regression coverage verifies checkpoint adoption on the same workflow ID. Focused translation, resume, orchestration, and gateway tests: 144 passed. Compose config validated; worker image `b2c-hy-worker:deferred-recovery-20261016` built and its orchestration-source hash matched the checkout.

Safe rollout is partial by design while live work is active: Book 2 acknowledged PAUSED at `translation-batch:225`; only idle workers 3 and 4 were replaced with the new image. Book 1 still has one active Temporal Activity and a durable pause request; workers 1 and 2 remain untouched until that Activity reaches a safe boundary. No deferred retry has yet been run by the new image, so runtime efficacy is not claimed. Next: wait for Book 1's pause acknowledgement, replace workers 1 and 2, then resume the same workflow IDs and verify progress never drops and each reattempt writes a per-segment cause. Do not create duplicate translations or rerun already checkpointed segments.


## Live deferred translation status follow-up - 2026-10-16

Book 2 body remains active at workflow b2c-translate-34a38983393ed2772c77236afccdd6cfc39725197b65fc978bb153931b92e440: 621/2620 saved, batch 242/804, one active segment, 33 unresolved validator failures after two model attempts. Failures are primarily numeric placeholder order/count/format issues; the model endpoint returned HTTP 200. The current workflow preserves them for its generic deferred recovery pass; no duplicate run was launched. API progress now counts unresolved segment failures rather than the transient deferred-batch list. Focused tests 4/4; only b2c-api-1 was rebuilt, and health is healthy. Do not report the 33 units repaired until checkpoints confirm their acceptance.


## Independent strict translation/gate repair — 2026-10-16

Source-only review inspected all 34 currently unresolved Book 2 source/response pairs at 646/2620 saved. Confirmed HY adapter dropped worker context, placed retry data after the source (actual outputs translated that data), advertised absent marker templates, and received inaccurate missing-number/table/bracket evidence. General fixes now enforce source-last prompts with bounded context, source-specific marker inventories, missing-marker anchors, per-kind bracket validation, protected table evidence, historical-range number handling, and normalized marker digits. Prompt namespace is `phase7-translation-v7-source-boundary` to avoid stale cached replies while preserving same-workflow validated checkpoint adoption; terminal summaries no longer inflate attempt counts. Focused regression run: 164 passed, including 17 new strict-contract tests. No live inference, checkpoint mutation, worker restart, or deployment was performed. Safe rollout and residual risks: `eval/reports/astra-translation-gate-review-2026-10-16.md`. Runtime efficacy remains to be measured after coordinated worker and HY-adapter deployment.
