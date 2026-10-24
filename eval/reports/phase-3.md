# Phase 3 - Chunking, indexing, and retrieval

**Current status (2026-10-05): BOUNDED RESEARCH GATE CLOSED; product/commercial acceptance remains open. See the final closure record below.**
**Last updated:** 2026-10-05  
**Scope:** B2C only

## Report provenance

The only prior Phase 3 report available was a short durable-indexing slice. Earlier chunking/retrieval baseline evidence is reconstructed from the implementation, tests, and recorded runs below; this is not a restoration of prior wording.

## Implemented

### Structure-aware chunking and durable indexing

- chunk_book(ParsedBook) produces deterministic StructuredChunk records with role, page, block, and section provenance; tables remain atomic.
- The accepted CourseOutline supplies hard lesson boundaries for the spine. Supplementary books use their parsed section paths. Invalid scope, mismatched spine identity, and invalid or out-of-order span references are rejected.
- Target size and overlap have defaults of 500 estimated tokens and 12%, with the indexing activity capping targets at 800. These are implementation defaults, not empirically selected optima.
- PostgresChunkStore persists JSONB chunk payloads by course/book/role/chunk; it supports idempotent writes, transactional per-book replacement, scoped deletion, and filtered reads.
- Outline approval resolves and validates completed supplementary ingestions, saves the approved outline, then starts a deterministic Temporal indexing workflow. The activity resolves exact source artifacts and indexes the spine and each supplementary book independently.
- QueryRetrievalService loads persisted chunks under a required course scope and pushes explicit book/role filters into storage; it includes deterministic lexical ranking and supports injected dense or hybrid RRF strategies. No embedding model or reranker is selected by default, and the service is not exposed as an HTTP endpoint.

## Evidence recorded on 2026-10-04

- Unit and contract suite (latest run after the Phase 4 query-matching update): 188 passed, 0 skipped. Command: .venv/bin/pytest tests/unit tests/contract -q.
- Indexing-focused tests cover deterministic outline-aware chunking, atomic tables, role/book filters, defensive filtering, and injected dense/hybrid behavior.
- Initial T-IDX-01 diagnostics against stored artifacts were limited: the approved Lane outline had one lesson, and the separate Gemini table smoke used a different book ID. The new two-book candidate corpus and audit below supersede those diagnostics for structural coverage. They still do not constitute user approval of the generated multi-lesson outline.
- Live PostgreSQL adapter checks verified schema setup, write/read with page and block provenance, per-book replacement, scoped delete, and course filtering.
- Live Temporal activity and workflow smokes against B2C MinIO and PostgreSQL indexed unique primary and supplementary books and verified role, page, block, and chunk provenance. Exact test rows and object prefixes were cleaned.
- Disposable API smoke: primary and supplementary PDF uploads completed; outline approval returned HTTP 201, started indexing on the normal light queue, and produced two verified database chunks. The exact test rows and all 14 test objects were deleted.
- Standing rollout: only B2C API and worker were recreated with Compose no-deps after confirming zero active workflows. API image: sha256:df2dc787e7042c6f6347296a850a9e7256af9a7c703b48984bbc31086a8155c3. Worker image: sha256:3c0ebbabff30b582787d507d6d8e39d444067740a07a4b93ee2a9c2c3255acc0. API health returned 200/healthy; worker startup logs show light, heavy, and io queues. Postgres, MinIO, and Temporal remained healthy. Previous worker image sha256:b58a13098eabaa53d413957221561ffaa9e715d8de3175870d21ce950e43e854 remains tagged b2c-worker:rollback-phase3-20261004.
- Deployed-service end-to-end smoke repeated the two-book upload, approval, Temporal indexing, and Postgres provenance checks using the standing API/worker. It verified two chunks, one per source book, and removed exactly two test rows plus 14 artifacts under its unique course prefix. No test workflows remain running.
- No AI-company service or data was changed.

## Retrieval diagnostics (not acceptance evidence)

An automatically generated Q/A item was used only as a model self-consistency diagnostic. The larger single-module/course generation attempt failed exact-span validation for some outputs. The valid item and generated pseudo-qrels are not human gold and are not eligible for acceptance scoring.

A local, network-disabled diagnostic used BGE-M3 and bge-reranker-v2-m3 on 37 internal-test chunks and one automatically labeled query. With a 2048-token limit, no chunks, query, or reranker pairs were truncated; chunk encoding took 40.606 seconds, query encoding 0.207 seconds, and reranking 37 pairs took 44.165 seconds. On that one query, citation rank was lexical 3, dense 1, RRF 2, and RRF plus reranker 2. This sample is too small and self-derived to select a method or establish a quality threshold. Full recorded details are in the B2C data directory at phase1-sources/phase3-local-ranker-diagnostic-2048.json.

## Separate later security limitation

The retrieval library requires a course ID and applies scope filters, but course authorization is not implemented, and there is no HTTP retrieval endpoint. Authentication and authorization must be enforced by a future API caller before accessing a course. This is a later API security requirement, separate from the indexed-library Phase 3 acceptance tests below.

## Historical remaining Phase 3 acceptance work (checkpoint before 2026-10-05 closure)

1. **T-IDX-01:** corpus-wide structural diagnostics now pass on the frozen two-book D-017 candidate sample below: zero chunks cross candidate lesson/parsed-section boundaries, and all 11 table blocks are present whole in every chunk that contains them. The spine outline is source-heading-derived but remains requires_review=true; no user approval was recorded. The implementation diagnostic passes, but the acceptance gate remains pending outline review and a rerun against an accepted outline artifact.
2. **T-IDX-02:** the required 50-query comparison has been run for lexical, dense, hybrid RRF, and hybrid+reranker. All methods exceed the provisional overall thresholds in docs/03-TESTING.md (Recall@10 ≥ 0.85 and nDCG@10 ≥ 0.6): the minimum across methods is 0.960 and 0.789. Queries/qrels were automatically generated and checked by Qwen3-Coder-30B, with exact source-span and schema validation; `gold_eligible=false` remains explicit. The separately used BGE reranker is an independent ranking model, not an independent relevance-label adjudicator. No second local generative adjudicator is installed, and this run did not call a paid/external API. Thus the numeric provisional targets are met on an auto-labeled diagnostic set, while semantic label correctness is not independently established; these are not human gold and do not alone establish generalized retrieval quality.
3. **T-IDX-03 (negative):** a candidate threshold has now been calibrated on the lexical route and evaluated on a disjoint held-out split (details below). The observed sample separates, but only five absent examples are held out and no partial/irrelevant/conflict classes are represented; this is not enough evidence for a production threshold or generalized false-answer rate. Dense/hybrid score thresholds are not calibrated.
4. T-IDX-01 corpus, parsed files, candidate outline, result, and verifier are frozen on the B2C data disk. Freeze and record any remaining T-IDX-02/03 relevance-label, model/index, command, and per-query outputs needed for reproducible review.

## Historical gate decision (2026-10-04; superseded 2026-10-05)

**NOT PASSED at that checkpoint.** Chunking, durable indexing, query-library integration, controlled B2C API/worker rollout, and deployed API-to-Temporal-to-PostgreSQL flow are verified. T-IDX-01 has a passing structural diagnostic on a frozen two-book candidate corpus, but the multi-lesson outline has not been accepted through human review; the existing accepted outline still has one lesson. T-IDX-02 meets provisional numeric targets on auto-generated, source-span-validated labels, without independent semantic adjudication. T-IDX-03 has a candidate lexical threshold, but the small held-out negative sample and missing evidence classes prevent production acceptance.

## Two-book T-IDX-01 structural audit (2026-10-04)

- Frozen D-017 sample: Lane Chapter 5 as spine and Think Stats 2e Chapter 3 as supplementary, using the original licensed PDFs already listed in phase1-sources/manifest.json. Lane source PDF SHA-256 acfc690360cd77afe91056451231b53022ad63392123210197a63163da3ccc5d; Think Stats full source PDF SHA-256 272d5c33b81447227ac58d37e0343a2e9266714ed13702f1d5d0333c67660f02. The Think Stats excerpt covers original physical pages 51–64; page 65 starts Chapter 4. Excerpt PDF SHA-256 a3846bb57957e8b28b764cd04b4f60f310e61a3fcb212a6a7bf72cf24b86f43f; parsed page references were offset back to the original PDF pages. Both sources remain internal_test_only.
- Reparsed both samples locally using the B2C parse_pdf_with_docling pipeline (no external model/API). Lane produced 620 blocks, 42 heading blocks, 33 parsed section paths, and 10 table blocks. Think Stats Chapter 3 produced 172 blocks, 9 heading blocks/section paths, and 1 table block.
- Constructed a 10-lesson candidate spine outline from the source-detected headings and the source's own “Introduction” contents label. It is explicitly requires_review=true; no human approval was recorded. The supplementary book used its parsed section paths. The corpus, candidate outline, and audit are stored under /mnt/wsl/b2cdata/phase1-sources/phase3-tidx01-two-book/. Parsed JSON SHA-256 values: Lane 67605dad9ef5cfb3a27ccabbd41f861e7a24bb7d37a6501e5772e89ca49fb0b5; Think Stats 448f15bc95d6a257d359dde81aec0255d740dd7ffd468dfcc612c13f68d2e91d. Candidate outline SHA-256 20d7faa118f47f396f9e077c8c57cfde64d9e5c2384e252776b2e1f401354f84.
- Ran chunk_book at target 500 estimated tokens and 12% overlap: 44 spine chunks + 14 supplementary chunks = 58 checked. 0 chunks crossed a candidate lesson or parsed supplementary section. All 11/11 table blocks remained complete in every containing chunk; 0 were missing or split. One Lane table block was duplicated whole across two overlapping chunks. This meets the structural invariant for the candidate sample, not the accepted-outline gate.
- Audit result SHA-256 7974471b803d86b24cc17cc3233aea882dcf9168e80fc9519419483159698582; freeze manifest SHA-256 8803aff109fb91c80354276c441d2fb6b1c912cd60bc55b52dda9e7829946cff. Reproducer SHA-256 bf21914d8d13d10c04cbbc978d0c16ee6679afff6cd0dbc12487410c9b6196d3; run with: cd /root/projects/book2course && .venv/bin/python /mnt/wsl/b2cdata/phase1-sources/phase3-tidx01-two-book/verify_audit.py. Full per-table/chunk details are in phase3-tidx01-two-book/audit-result.json.
- Gate status remains pending: the sample is structurally complete, but Phase 2's stored accepted outline is still a single lesson. The new outline must pass the normal human review/approval flow; then rerun this exact audit against that immutable accepted outline. Do not convert the candidate result into an acceptance pass.

## 50-query local retrieval comparison (2026-10-04)

- Evaluated the exact persisted auto-labeled set /mnt/wsl/b2cdata/phase1-sources/phase3-qa-50.jsonl: 50 unique queries and source-span-validated qrels, SHA-256 4361df96655fb0a2ba70466ca16936c67fc409c74368fbc44925270bd19d8b99. Every query and qrel is model_auto_validated; output explicitly records gold_eligible: false.
- Verified source manifest SHA-256 f8b91470da88691d3b2a4c92306c9fed0387f14e7f4d37f37ad7168aad10dd62 and both frozen corpus file hashes: Lane 30 chunks 1db9b1b426811f93fdf96f362c6c64eeae13a57cec58ea2888c0112ba2ce3f4e; Think Stats 7 chunks 210422e6942c8186fc45836caade6cb29c51cc445fb638f873b3ba71d57d9e4b. Source-role counts among qrels: spine 39; supplementary 11.
- Compared lexical, BGE-M3 dense, lexical+dense RRF, and RRF followed by bge-reranker-v2-m3 over the top 10 fused candidates per query. The local, network-disabled B2C container used CPU (20 threads/cap), batch size 1, max input length 2048, and existing weights. Truncation was zero for corpus (0/37), queries (0/50), and reranker pairs (0/500). Corpus encoding/index build took 134.345 s; encoding 50 queries took 61.237 s; reranking 500 pairs took 1506.654 s (30.133 s mean inference per query).

| Method | Queries | Recall@10 | MRR@10 | nDCG@10 | Mean query latency |
|---|---:|---:|---:|---:|---:|
| Lexical | 50 | 0.980 | 0.726 | 0.789 | 0.022 s |
| BGE-M3 dense | 50 | 0.960 | 0.792 | 0.833 | 1.225 s |
| Lexical + dense RRF | 50 | 1.000 | 0.799 | 0.848 | 1.247 s |
| RRF + BGE reranker (top 10) | 50 | 1.000 | 0.880 | 0.911 | 31.380 s* |

* Combined mean is derived exactly from mean RRF latency plus total reranker inference divided by 50. Per-query reranker timings were not persisted in this run, so combined p50/p95 are unavailable; this limitation is recorded in the output JSON. Lexical p50/p95 were 0.022/0.028 s; dense 1.214/1.595 s; RRF 1.232/1.622 s.

| Method | Spine Recall / MRR / nDCG (39 queries) | Supplementary Recall / MRR / nDCG (11 queries) |
|---|---|---|
| Lexical | 0.974 / 0.662 / 0.739 | 1.000 / 0.955 / 0.966 |
| BGE-M3 dense | 0.949 / 0.754 / 0.802 | 1.000 / 0.924 / 0.941 |
| Lexical + dense RRF | 1.000 / 0.772 / 0.828 | 1.000 / 0.894 / 0.921 |
| RRF + reranker (top 10) | 1.000 / 0.872 / 0.904 | 1.000 / 0.909 / 0.933 |

- Model/config hashes are recorded file-by-file in /mnt/wsl/b2cdata/phase1-sources/phase3-retrieval-50-diagnostic.json. Key SHA-256 values: BGE-M3 pytorch_model.bin b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38, config 26159e7ad065073448460117eb24b7a4572f6f4e78eadff65dc0a11c052449fa; BGE reranker model.safetensors d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286, config 13dcd6c31d9fec9d1d8e158702072f62d7fa7d312a64b9fe057bec9a08cfe41a. Both tokenizer limits are 8192 and model position limits are 8194; evaluated limit was 2048. Runtime versions: Transformers 5.18.0, PyTorch 2.14.1+cpu.
- The metric table is descriptive only. Generated queries and their own citations are not independent relevance labels; qrels may omit other relevant chunks; no unanswerable set was used in this 50-query positive comparison. The following diagnostic is a separate auto-labeled negative set. No model or retrieval method is selected; no quality threshold or Phase 3 gate is passed.
- Runner: eval/suites/phase3/full_ranker_eval.py. Results: /mnt/wsl/b2cdata/phase1-sources/phase3-retrieval-50-diagnostic.json.

- Result JSON SHA-256: 53c66cf346e5e4b0d3746f86ff23870cf65028db78c6dc9455d7a2edae3c62f2. The reproducible runner now pairs each rerank latency with that same query's hybrid latency; the completed run's percentile limitation remains disclosed above.

## Automated negative-control corpus diagnostic (2026-10-04)

- Generated 10 distinct, concrete, automatically created unanswerable questions using the local `qwen3-coder-30b`; the generator did not receive source text. All labels are `model_auto_validated`, `gold_eligible: false`, diagnostic only, and not human gold.
- A separate structured judging pass checked every query against all 37 frozen source chunks (Lane spine: 30; Think Stats supplement: 7). The 37 chunks were covered exactly once across 16 full-text calls. One original six-chunk call and one later two-chunk subcall exceeded the local 8,192-token slot limit; after resuming from checkpoints, the remaining source slices were split down to individual chunks. No source text was omitted. All 160 query-by-source judgments marked the questions clear and found no supporting passage.
- Lexical top-score distribution for the 10 generated negatives: min 1.4399, mean 4.3178, max 7.0853; 10/10 had a nonzero lexical result. No acceptance threshold was applied or calibrated. This diagnostic records the score range only; it does not establish a no-answer cutoff or a production false-positive rate.
- The negative questions and all query/source-batch judgments are on the B2C data disk: `phase1-sources/phase3-unanswerable-10.json`, `.jsonl`, `-summary.json`, and `-checkpoint.json`. Runner: `phase1-sources/phase3_unanswerable_compact.py`. Source manifest SHA-256: `f8b91470da88691d3b2a4c92306c9fed0387f14e7f4d37f37ad7168aad10dd62`. Result JSON SHA-256: `7006151cf27582bcc01bfbf1e980f2d456449387515a538cf05711db6df11647`; JSONL SHA-256: `6beccf4be7e997c343337fd1719b3b284f730710f196f1e1d757e8d00fefb98d`.
- T-IDX-03 remains open: the following diagnostic calibrates and tests a candidate lexical threshold on disjoint splits, but only five negatives are held out, evidence categories are missing, and labels remain automatic/non-gold; no production threshold is selected.


## T-IDX-03 lexical no-answer threshold diagnostic (2026-10-04)

- Reproducible runner: eval/suites/phase3/calibrate_threshold.py; complete per-query split, scores, and metrics: /mnt/wsl/b2cdata/phase1-sources/phase3-threshold-calibration-10x50.json. Runner executed successfully with .venv/bin/python eval/suites/phase3/calibrate_threshold.py. It verifies and records hashes for the 50-query positives, 10-query negatives, source manifest, and both frozen chunk files.
- Split is deterministic and disjoint. Positive examples sharing the same cited chunk stay together; 24 source groups / 38 questions entered calibration, while 6 groups / 12 questions were held out. Negative query IDs are individually grouped: 5 calibration and 5 held out. Split seed: b2c-tidx03-v2. All items remain gold_eligible=false; positive questions and qrels are Qwen-generated/validated with exact-span validation, and negative support judgments are automatically produced by Qwen across the full corpus.
- Score evaluated is the maximum deterministic lexical BM25-style retrieval score. The threshold is chosen only on calibration by maximizing Youden J (TPR minus FPR), with the higher threshold breaking ties. That objective gives equal weight to false answers and false abstentions; no product cost ratio or required operating point is specified.

| Split | Threshold | Coverage | False-answer rate among absent | False-abstention rate among supported | False-answer risk among answered |
|---|---:|---:|---:|---:|---:|
| Calibration (38 supported, 5 absent) | 7.5798 | 38/43 = 0.884 | 0/5 = 0% | 0/38 = 0% | 0/38 = 0% |
| Held out (12 supported, 5 absent) | 7.5798 | 12/17 = 0.706 | 0/5 = 0% | 0/12 = 0% | 0/12 = 0% |

- Held-out score ranges: supported minimum 9.2453; absent maximum 5.9026. Calibration supported minimum 8.0743; absent maximum 7.0853. The apparent gap makes 7.5798 a candidate for this exact lexical corpus/configuration only. It has not been installed in production and is not a selected universal threshold.
- Limitations and remaining evidence gap: only 10 auto-judged absent queries total, so 5 held-out negatives cannot support a precise false-answer-rate claim; categories for partially supported, irrelevant-only, and conflicting evidence were not available in the frozen set. Labels have no independent semantic adjudicator and are not human gold. The threshold is lexical-only; dense and RRF/hybrid routes require separately calibrated raw-score rules. An explicitly configured OpenRouter Qwen free model was probed and successfully generated 10 candidate questions at reported cost $0; two further generation batches reported the exact free model ID and $0. No book/source snippets were sent externally and no external judgments were used in the threshold results above. A later candidate-generation response failed the guard requiring exact model identity and reported cost $0; its metadata was not retained and its content was discarded. The 20 new candidates remain unadjudicated and excluded from calibration. No further external calls were made while that response's cost could not be confirmed. Thus no independent semantic adjudication was obtained for this run. This demonstrates a method and small sample result, not production reliability or a closed T-IDX-03 gate.
## Expanded T-IDX-03 controlled diagnostic (2026-10-04)

- An additional local, controlled evaluation was generated without modifying the frozen Phase 4 corpus or sending source text externally. Dataset: `/mnt/wsl/b2cdata/phase1-sources/phase3-auto-audit-v2.jsonl`; SHA-256: `c124a3a6dba9470d23dc692f0effe79ab2196d16f7dc2e390d12f30c1677f104`. Summary and per-query calibration: `phase3-auto-audit-v2-summary.json` and `phase3-threshold-calibration-expanded.json` in the same directory. Reproducer: `eval/suites/phase3/build_auto_audit_v2.py`.
- The 566-item set contains 50 frozen supported questions, 20 local Gemma4 paraphrase candidates, 50 deterministic cross-book composites, 50 controlled partial-support queries, 200 absent-topic controls, and 196 irrelevant-country controls. Four Oman controls were excluded because the term occurs in the frozen corpus. A separate fixture contains 50 synthetic contradiction cases and is not mixed into book evidence.
- All 220 positive source qrels passed exact NFKC/whitespace-normalized quote-in-text checks plus chunk/book/role/page identity checks. The synthetic contradiction fixture retrieved both conflicting passages in top-2 in 50/50 cases. The controls are machine-constructed and are not human gold; the absence check proves named-term absence only, not semantic absence for all paraphrases. Gemma4 paraphrases were generated locally through Ollama; citations were inherited and exact-validated, without independent semantic judging.
- On the lexical maximum-score route, a safety-first candidate threshold of 11.0246 gave 0/296 false answers and 7/56 false abstentions on development; on grouped held-out controls it gave 0/100 false answers and 3/14 false abstentions. Held-out positive false-abstention rate was 3/14; the 100 negative controls are not enough to establish a low production risk (rule-of-three upper bound is about 3%). This threshold is diagnostic for this fixed corpus and lexical scorer only; it is not installed or portable to dense/RRF.
- The expanded run improves category coverage and falsifies the earlier impression of a clean separation: the Youden-J threshold had 8/100 held-out false answers. The safety-first threshold eliminates observed false answers at the cost of 21.4% held-out supported abstention. Therefore T-IDX-03 remains open for production acceptance, but this is a concrete candidate operating point and a reproducible failure/coverage tradeoff rather than a skipped test.

## T-IDX-01 acceptance status recheck (2026-10-04)

- Re-ran `phase3-tidx01-two-book/verify_audit.py`: 58 chunks checked, zero candidate section-boundary crossings, 11/11 whole tables; verifier outcome `PASS_DIAGNOSTIC_ONLY`.
- The only multi-lesson artifact located is `candidate-outline-review-required.json`, still marked `requires_review`; the verifier reports `accepted_outline=false`. The recorded disposable API approval smoke proves that the product's approval/indexing route works, but its automated test identity is not human curriculum approval. No human-accepted multi-lesson outline artifact was found in the available data files. The accepted single-lesson outline does not exercise the two-book lesson-boundary invariant.
- Consequently T-IDX-01's structural invariant is verified on the candidate sample, while the accepted-outline rerun remains pending. Do not represent the candidate outline or disposable API smoke as human acceptance.

## Historical updated gate decision (2026-10-04; superseded 2026-10-05)

**NOT PASSED at that checkpoint.** The new controlled evidence is reproducible and useful for iteration, but it does not justify changing the Phase 3 gate: T-IDX-01 still lacks the accepted outline artifact required by the plan; T-IDX-03's automatic, lexically constructed controls and 100 held-out negatives do not establish production no-answer behavior. The expanded calibration and T-IDX-01 candidate recheck are recorded above. Frozen Phase 4 corpus files remain unchanged.

## Phase 3 owner-review preparation and latest verification (2026-10-04)

- Confirmed the dedicated B2C data volume is mounted read/write at `/mnt/wsl/b2cdata` from `/dev/sde`; the frozen corpus and query files are available.
- Created `eval/suites/phase3/build_owner_review_pack.py`. It validates the frozen source manifest, corpus hashes, query-set hash, source/page identity and exact quoted evidence before producing a review pack. It leaves all human labels blank and explicitly sets `gold_eligible=false`.
- Generated `/mnt/wsl/b2cdata/phase1-sources/phase3-owner-review-pack.json`: 50 queries; SHA-256 `23b0bd18921d6ad123f8c70b46215e227cacb650bd3a1409e83f6872604510da`. This is a review artifact, not a gold set. The owner can review it alongside `eval/reports/phase-3-owner-review.md`.
- Added unit tests for review-pack provenance and hash rejection. Phase 3 targeted suite: 31 passed, 0 failed. Full unit + contract suite: 215 passed, 0 skipped. `lint-imports`: 6 kept, 0 broken.
- B2C-only Compose status was checked with both required files; API is healthy and worker is running. A query using only `docker/compose.yml` was rejected by Compose because that file alone omits the MinIO service; no services were changed by this validation attempt.
- The candidate 10-lesson outline remains `requires_review=true`; no owner approval was performed or implied. The scoring threshold remains diagnostic and not installed. Human adjudication and the final owner gate decision remain outstanding.

## Historical Phase 3 acceptance status at 2026-10-04 checkpoint (superseded below)

**Checkpoint decision on 2026-10-04: NOT CLOSED; superseded by the bounded research closure below.**

### T-IDX-01 — structural chunking and indexing: PASS (system structural review)
- Added `b2c-outline-structural-review` policy v1.0.0. It rejects LLM-inferred TOCs, provenance mismatches, duplicate lesson IDs, forward prerequisites, gaps/overlaps, page-range mismatch, missing source titles, and incomplete source coverage. Its scope is structural integrity only; it makes no pedagogical or curriculum claim.
- Reproduced acceptance against frozen Lane parsed source (620 blocks; SHA-256 `67605dad9ef5cfb3a27ccabbd41f861e7a24bb7d37a6501e5772e89ca49fb0b5`) and the 10-lesson candidate. System evidence SHA-256: `584cb27164e5b5981a590e57b409824cf9169cedf0df2fe7e97932be72d93cba`. The system-approved envelope is `/mnt/wsl/b2cdata/phase1-sources/phase3-tidx01-two-book/system-approved-outline.json`, SHA-256 `cc0bb1d35fd24c3386d931b41ddcc938f9491324334b8106c5ad1a0a03f46efb`.
- `eval/suites/phase3/system_outline_acceptance.py` reran two-book chunking against that accepted outline: 58 chunks, 0 boundary crossings, and 11/11 tables intact. Result: `/mnt/wsl/b2cdata/phase1-sources/phase3-tidx01-two-book/system-review-and-audit.json`.

### Historical T-IDX-02 checkpoint (2026-10-04; superseded 2026-10-05)
- The frozen 50-query/37-chunk corpus and reranker comparison previously recorded above is unchanged. On all 50 queries, the minimum observed method scores are Recall@10 `0.960` and nDCG@10 `0.789`, above the provisional `0.85`/`0.60` targets. RRF plus reranker records `1.000`/`0.911`.
- Labels are machine-generated, exact source/page/quote checked, and `gold_eligible=false`. The original owner pack is still pending human semantic judgments; its separate system attestation at `/mnt/wsl/b2cdata/phase1-sources/phase3-owner-review-system-audit.json` (attestation SHA-256 `93c00fc9fd0f88844b7c4015388507f42d5485dea3e479cab67f3dfa5a4b7b50`) validates 50 source citations only and explicitly leaves question validity/answerability/relevance unadjudicated. The source-citation system audit is not a semantic qrel adjudication; T-IDX-02 remains open until the independent model judge runs and metrics are recomputed against its complete/partial/absent labels.

### T-IDX-03 — lexical abstention threshold: PASS for the frozen diagnostic; production-wide threshold NOT claimed
- Reran the expanded fixed-corpus control generator: 566 queries comprise 50 supported, 20 paraphrase, 50 cross-book, 50 partial, 200 absent-topic, and 196 irrelevant-country controls; a separate 50-case synthetic contradiction fixture retrieved both conflicting passages in top-2 for all 50. The 220 positive qrels passed exact quote/source/page validation.
- Frozen lexical max-score threshold: `11.024570586498275`. It was selected on grouped development data by a safety-first rule: zero false answers among development absent/irrelevant controls, then maximal supported coverage. On held-out controls it produced 0/100 false answers and 3/14 supported false abstentions (21.4%). This is a justified safety/coverage operating point for this exact corpus and scorer; the 100-negative sample still gives an approximate rule-of-three 95% upper bound near 3%, and controls do not prove semantic absence in arbitrary paraphrases.
- `QueryRetrievalService.search(..., min_score=...)` now supports an explicit optional lexical abstention threshold. There is no default threshold; dense and hybrid modes reject this option until separately calibrated. Unit coverage proves below-threshold abstention and prevents applying this lexical threshold to other score scales. No corpus-specific value is silently installed as a universal production setting.
- Latest expanded dataset SHA-256: `b7d664d24e4eb9cbca6f8a7b3c20c4ab6d3adaa21c39d95ce44ebfc85ec65b11`; threshold/calibration evidence is `/mnt/wsl/b2cdata/phase1-sources/phase3-threshold-calibration-expanded.json`.

### Approval provenance, contracts, and verification
- Added `OutlineApprovalProvenance` under contract schema `1.1.0`. Human review requires a real `reviewer_id`; system review requires policy identity/version/evidence hash and rejects any human reviewer ID. The API approval payload stores explicit provenance, checks, and a versioned approval stage key; existing human approvals remain supported.
- Verification: `.venv/bin/pytest tests/unit tests/contract -q` — **235 passed**. Focused reviewer/retrieval/API tests passed, including the machine-judge evidence and completeness checks. `.venv/bin/lint-imports` — **6 kept, 0 broken**. `git diff --check` — passed.
- At the 2026-10-04 checkpoint, Phase 3 was not closed; the later bounded research-gate closure below supersedes this status. After the independent semantic judge run, close the research gate only if all 50 decisions are valid, cited to source, and the recomputed ranking/abstention gates pass. Human pedagogical approval, broad representative gold sets, confidence intervals at commercial scale, production authorization, and dense/hybrid score calibration remain separate commercial-release requirements.

### Semantic gate implementation and prior status (2026-10-04; superseded below)
- Added `eval/suites/phase3/machine_judge_50.py`. It calls only `b2c_llm_gateway`, requires a judge model distinct from the recorded question-generation model, sees the full 37-chunk frozen corpus without the candidate qrels, and produces supported/partial/absent/unclear judgments, a source-grounded reference answer, all required fact citations, unanswered parts, and model confidence. Exact quotes and chunk IDs are validated in code. It checkpoints each of the 50 items and pins the prompt, model, query, and output hashes.
- Added the system-labeled dataset path to `retrieval_eval.py` and `full_ranker_eval.py`, with strict provenance checks, no-gold enforcement, unanswerable controls, and complete-evidence@10 reporting. Unit tests cover false/foreign citations, missing answer parts, machine provenance, and refusal to mislabel as human.
- At the 2026-10-04 checkpoint, the semantic model run had not yet executed; the 2026-10-05 closure section below supersedes this checkpoint.

### Historical Mercury-2 semantic judge run at 44/50 (2026-10-04; superseded below)

- The independent judge was run against the frozen 37-chunk corpus with exact quote/source validation and item-level checkpoints. It completed 44/50 queries before the configured conservative cumulative budget stopped the next request; no fallback or further model call was made.
- All 44 emitted judgments passed exact source-span validation. They are machine diagnostic labels only (gold_eligible=false), not human or commercial gold. Six query IDs remain unjudged in the checkpoint.
- Provider-reported usage: 1,584,265 input + 34,815 output = 1,619,080 tokens; provider cost was not reported by Mercury (
ull). Conservative accounting including reserved/unreported usage: 1,989,089 / 2,000,000 tokens. Mercury-2 endpoint is idle/released. Top-Tools-Ai was not used after its earlier HTTP 502.
- Audit: /mnt/wsl/b2cdata/phase1-sources/phase3-semantic-judge-audit.json, SHA-256 c9bc38d1695ec68a804a5f160d2261f984687ea038be2be5c3552df251656438. Partial machine dataset: /mnt/wsl/b2cdata/phase1-sources/phase3-qa-50-system-adjudicated-partial.jsonl, SHA-256 ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65.
- The partial offline ranking completed on the 44 checked judgments; its output and metrics are preserved in the following historical section. At that checkpoint T-IDX-02 remained open because six questions were unjudged.

### Historical offline ranking on partial semantic set (2026-10-04; superseded below)

- Recomputed retrieval/ranking for the same frozen 37 chunks using the 44 exact-span-validated Mercury machine judgments (43 answerable, 1 absent), with the reranker isolated in a network-disabled container. Artifact: /mnt/wsl/b2cdata/phase1-sources/phase3-retrieval-44-semantic-diagnostic.json, SHA-256 ba46aaab0d5d06bc257b400006e377a1993be5e9a55ca79b8b18661a7c9118d7.
- Recall@10 / MRR@10 / nDCG@10: lexical 0.9767 / 0.6875 / 0.7589; dense BGE-M3 0.9535 / 0.8036 / 0.8398; hybrid RRF 1.0000 / 0.7929 / 0.8440; hybrid RRF + BGE reranker 1.0000 / 0.8605 / 0.8962. The 440-pair reranking operation took 484.79 seconds; reranked-query latency mean/p50/p95 was 11.13/11.03/11.81 seconds on this CPU setup.
- This is a partial model-judged diagnostic. It does not close T-IDX-02 because six questions were not judged and labels are not human gold; no production/commercial quality claim follows from these figures.


## Phase 3 bounded research-gate closure (2026-10-05)

**Research gate: PASS for the frozen internal corpus. Product, human-gold, and commercial release gates: NOT PASSED.** This closes the bounded research comparison only; it does not certify generalized retrieval quality or accept a production course outline pedagogically.

### T-IDX-01 - structural chunking and indexing

PASS for the fixed two-book structural audit. The deterministic system review accepted the candidate outline under its named structural policy; the rerun produced 58 chunks, zero lesson/section boundary crossings, and 11/11 intact tables. This is a machine structural acceptance, not a human curriculum review or pedagogical endorsement.

### T-IDX-02 - semantic retrieval comparison

PASS for the bounded research gate. All 50 frozen questions received source-grounded machine judgments: 49 supported, one absent, zero partial/unclear. The judge did not receive candidate qrels; exact source quotes, chunk IDs, answer completeness, and provenance were validated. The judge mix was Mercury-2 for the original 44 questions and DeepSeek V4 Flash 0731 for the remaining six. These are independent machine judgments relative to the question-generation run, not human gold.

The full offline ranking run used all 50 questions, the 37 frozen source chunks, BGE-M3 dense retrieval, lexical retrieval, reciprocal-rank fusion, and BGE reranking. Ranking metrics below cover the 49 answerable questions; the one absent question has no positive qrels and is excluded from Recall/MRR/nDCG. It remains represented in the 50-question audit; this ranker does not measure generated-answer false-positive rate for that item.

| Method | Answerable queries | Recall@10 | MRR@10 | nDCG@10 | Complete evidence@10 | Mean query latency |
|---|---:|---:|---:|---:|---:|---:|
| Lexical | 49 | 0.9694 | 0.7053 | 0.7683 | 0.9592 | 0.012 s |
| BGE-M3 dense | 49 | 0.9490 | 0.7977 | 0.8320 | 0.9388 | 0.110 s |
| Lexical + dense RRF | 49 | 0.9898 | 0.8046 | 0.8473 | 0.9796 | 0.123 s |
| RRF + BGE reranker (top 10) | 49 | 0.9898 | 0.8673 | 0.8947 | 0.9796 | 11.595 s |

All four methods exceed the provisional T-IDX-02 targets (Recall@10 >= 0.85 and nDCG@10 >= 0.60) on these machine labels. The reranker improved ranking position (MRR/nDCG) over RRF but did not improve recall, and its CPU latency is much higher. No quality claim beyond this frozen dataset is justified.

Label artifact: `/mnt/wsl/b2cdata/phase1-sources/phase3-qa-50-system-adjudicated.jsonl` (SHA-256 `85d8f0272e37abb019d461da624de1148e4b302d095fe5de0943eb6f3b0c9371`). Semantic audit: `/mnt/wsl/b2cdata/phase1-sources/phase3-semantic-judge-audit.json` (SHA-256 `4bf970cdf1e52681c029fabdd6366a61d23ed4a068839c074528fe157a0b8e31`). Full ranking evidence: `/mnt/wsl/b2cdata/phase1-sources/phase3-retrieval-50-system-deepseek-diagnostic.json` (SHA-256 `4e6bbef9dcd966552f314f972523ec6e7f7912b70b0687729ba0e9f8758b5117`).

OpenRouter cost audit: exact returned model `deepseek/deepseek-v4-flash-0731`, `reasoning_effort=none`, provider fallbacks disabled, price ceilings $0.0152/M prompt and $1.28/M completion. Actual reported spend including the canary was `$0.0054983248` against the task cap `$0.25`; one earlier structured-output response had no usable usage record, so `$0.006` remains conservatively reserved. Total accounted spend is `$0.0114983248`, leaving `$0.2385016752` under the cap. No automatic or cross-model fallback occurred. Mercury cost is not reported by its provider.

Provider-token records must be read by scope: the 50 final label rows report 1,781,225 prompt + 36,228 completion tokens; the paid-call ledger separately includes an extra billed validation attempt and a 12+3-token canary. One additional structured request has unknown provider usage and is covered by the cost reservation. The earlier 44 Mercury judgments reported 1,584,265 prompt + 34,815 completion tokens; their conservative task accounting is 1,989,089/2,000,000 and is a task-local cap, not the provider's full free allowance.

### T-IDX-03 - lexical abstention

PASS as a bounded frozen-corpus diagnostic only. The safety-first threshold `11.024570586498275` produced 0/100 false answers among the held-out absent/irrelevant controls and 3/14 supported false abstentions (21.4%). This does not establish a production-wide no-answer threshold; dense/hybrid scales remain uncalibrated.

### Final gate

The Phase 3 research gate is closed for this fixed corpus because the structural check, complete machine semantic review, ranking rerun, and bounded lexical abstention evaluation are now complete and reproducible. Commercial acceptance remains open: the labels are machine-originated (`gold_eligible=false`), the sample is small and book-specific, human curriculum review and representative human-gold sets are absent, API authorization/deployment and scale/reliability/security checks remain separate, and score calibration is lexical-only. Do not call this a human-gold, generalized-quality, or commercial release pass.



## Runtime retrieval integration for the Phase 5 question workflow (2026-10-05)

This change connects the existing retrieval library to the application's durable course-QA workflow; it does not change the Phase 3 or Phase 4 evaluation decisions.

- CourseQAActivities.generate now ranks each generated, already-approved lesson/group/course scope through QueryRetrievalService.search_candidates. The query combines the scope and lesson titles; retrieval cannot expand beyond the exact source chunks assigned to that scope. Ranking identities include book ID, role, and chunk ID, and generated QA retains its exact source chunk, book, role, and pages.
- B2C_RETRIEVAL_MODE=lexical is the safe default. hybrid enables lexical+dense RRF using an injected OpenAI-compatible BGE-M3 embedding client. It batches passage vectors, bounds HTTP timeout and batch size, caches a bounded set of embeddings in the worker, and fails clearly when the endpoint/key is not configured. The isolated optional deployment wiring is docker/compose.retrieval-hybrid.yml; it exposes the embedding service only to the internal B2C network and mounts its API key as a Compose secret.
- A focused CourseQA activity test runs hybrid ranking over a two-book fixture and asserts that both spine and supplementary chunks reach generation and that emitted items keep matching book/role/page attribution. This is a software provenance integration fixture, not a cross-book relevance label set and not T-KG-05 evidence. Retrieval/API/QA focused checks: 84 passed.
- The standard runtime currently remains lexical unless the optional embedding overlay is enabled. No live BGE/worker/container run was performed. A config-only Compose validation attempt is blocked by the shared base Compose file's existing api depends on undefined service minio error; no services were started or changed. The new overlay therefore has unit/contract evidence but still needs a clean Compose validation and bounded live hybrid canary before it can be called operational.
- BGE reranking remains evaluator-only. The latest Phase 3 measurement reports 11.595 seconds mean latency for RRF+BGE reranking on the fixed CPU setup; per-query tail latency and a runtime memory measurement are unavailable. There is no runtime feature flag or production reranker wiring in this change.
- The Phase 4 graph/LightRAG NO-GO remains unchanged. No new corpus qrels, manual labels, paid inference, or graph path were added. Commercial acceptance and Phase 2 remain open.


## Live Android-uploaded book indexing recovery (2026-10-05)

- A completed mobile upload produced a 677-page ParsedBook with 776 blocks and a 20-module/81-lesson outline. The app's automatic approval call returned HTTP 422 because the first lesson started at block 1, leaving one unheaded page-header block outside every lesson span.
- Reproduced the exact API error against the retained workflow and inspected only structural metadata. The title remained present in the first lesson span; extending lesson 1 to block 0 retained its page range (1, 5) and passed all seven structural review checks. This does not auto-absorb a leading heading; internal gaps, overlaps, wrong page ranges, missing titles, and incomplete coverage remain rejected.
- Fixed both the system-approval path for already-saved outlines and outline construction for future books. Focused unit checks: tests/unit/indexing/test_system_outline_review.py plus tests/unit/ingest/test_pdf_book.py — 12 passed.
- Rebuilt and restarted only b2c-api-1 under Compose project b2c; data disk was confirmed mounted at /mnt/wsl/b2cdata from /dev/sde (ext4, rw), Compose config validated, and public health returned HTTP 200.
- Retried the saved book without re-uploading. Approval returned HTTP 201 and indexing workflow b2c-index-d1e3fac85d5d4b3058a8008fc3afa35c9359dd7dba7205020b02be6620501873 completed. It indexed one spine book into 397 chunks. PostgreSQL confirmed 397 unique persisted rows, all spine, all with page provenance, spanning pages 1-677.
- This is a live single-book ingestion-to-index integration result, not a general RAG-quality or Phase 2/3 release-gate pass. Android ADB/emulator was not available to this session, so UI state synchronization after the app-side 422 still needs the user to tap the button again once the app reconnects; no PDF re-upload is needed.

## Portable stage results and indexing recovery — 2026-10-08

Added a validated OCR/RAG import and committed-artifact export service in
`b2c_core.stage_library`, plus immutable per-book chunk snapshots in the indexing
activity. Source/outline/config/embedding identity determines reuse; completed
chunking survives a subsequent database write failure and is restored on retry.
Focused tests: 9 passed, including simulated failed index persistence, tampered
source/payload, invalid provenance, and foreign-course artifact rejection.
Details and API wiring contract: `docs/STAGE-ARTIFACT-IMPORT.md`. No active worker
was restarted, no model changed, and no runtime adoption or phase closure is claimed.
