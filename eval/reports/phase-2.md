# Phase 2 report — Ingestion and outline

Date: 2026-10-04 (Africa/Cairo)
Status: TEMPORARILY CLOSED BY OWNER (2026-10-04); the two owner-selected checks passed
Scope: B2C PDF ingestion, OCR fallback, deterministic outline, human review/approval
Excluded: the separate ai company project

## Summary

The ingestion foundation exists: digital-text parsing, page provenance, Docling OCR fallback, deterministic outline, upload/status endpoints, and approval route. A real defect was confirmed in Arabic OCR: the old implementation ran bidi reordering on flattened paragraphs, so separate OCR lines could reverse as a group; Docling layout cells could also omit recognized words. The parser now reuses Docling's configured Arabic PP-OCRv5 line reader, associates line boxes with page regions, sorts lines by visual position, and restores each Arabic line independently. The change is now baked into a reproducibly rebuilt `b2c-worker:dev` image and verified through the Arabic scan upload → API → Temporal → MinIO workflow.

Current synthetic diagnostic at Docling 2.131.0 / RapidOCR ONNX Runtime / Arabic PP-OCRv5, isolated container (8 GiB, 4 CPU): clean page CER 3/97 = 3.09%, degraded page CER 5/97 = 5.15%; peak RSS 1771 MiB and 2302 MiB; parse elapsed 20.61s and 6.87s respectively. Earlier output from the same code path before line-level reconstruction was 9/97 (9.28%) clean and 69/97 (71.13%) degraded. The line-order regression is fixed for this fixture; residual recognition errors remain, and neither result establishes real-book accuracy. A 300-DPI trial regressed clean CER to 30/97 = 30.93%, so it was reverted to the 216-DPI default. The 3% draft threshold remains unmet on both synthetic cases by the current PP-OCRv5 path and is uncalibrated; do not lower it to pass.

Datalab Surya 2 was then run against the exact same Arabic synthetic page, both clean and with the existing blur/rotation degradation. At pinned GGUF revision 6a3a4c30e5e74446d4f8b6afd05b2f2da970f470 (Surya OCR package 0.22.1, llama.cpp server image server-b11176), it returned all three lines and 0/97 character errors in both cases. With the inference server capped at four CPUs, elapsed time was 200.83s clean and 201.44s degraded; server memory peaked around 1.29 GiB and client RSS at 441–461 MiB. A less constrained exploratory run took 28.7s clean and 41.5s degraded. This is a promising candidate result on one synthetic fixture, not a real-book accuracy claim or a production decision. Compared with the existing PP-OCRv5 path on these same fixtures (20.95s/7.09s, CER 3.09%/5.15%), Surya removed the fixture's remaining recognition errors but was about 9.6x/28.4x slower under the four-CPU run. More representative, human-labeled pages are required before deciding whether to replace, supplement, or reject the existing recognizer. Surya weights use a modified OpenRAIL-M license; the model card permits research use and limits broad commercial use, so commercial deployment needs a separate license review ([official model card](https://huggingface.co/datalab-to/surya-ocr-2-gguf)).

## English OCR candidate comparison (exploratory; 2026-10-03)

Compared current PP-OCRv6 small English route with RapidOCR dedicated PP-OCRv5 English mobile recognizer, sharing detector/classifier and ONNX Runtime (4 intra-op, 1 inter-op threads). One generated 432-character English page, clean and degraded by downscaling, light blur, and 1.2-degree rotation; three runs after initialization. Both scored 0% normalized CER on both variants. Median seconds: V6 2.056 clean / 2.018 degraded; V5 EN 2.243 / 2.139. No meaningful quality or speed advantage appeared for V5 on this synthetic fixture. Keep V6 as provisional default; retain V5 EN as a candidate, not a final decision. Real human-labeled English scans remain necessary. Candidate model SHA-256 c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8 matches RapidOCR v3.9.2 manifest and is stored under /mnt/wsl/b2cdata/models/phase2-ocr-candidates/ppocrv5-en.

## Executed evidence

| Check | Result |
|---|---|
| Host unit + contract suite | Latest run: 94 passed, 0 skipped in 7.06s after installing required python-bidi (`.venv/bin/pytest tests/unit tests/contract -q -rs`). |
| B2C unit regression after outline/model-route additions | `.venv/bin/pytest tests/unit -q`: **95 passed, 0 skipped in 11.18s** after updating the retry-policy expectation for the new quota/truncation errors. | Pass |
| Latest unit + contract suite after Phase 3/4 additions | `.venv/bin/pytest tests/unit tests/contract -q`: **115 passed, 0 skipped in 6.90s**. | Pass |
| Docling/Arabic OCR parser unit tests | 3 passed in 0.52s in isolated worker runtime with source mounted read-only. |
| Full suite in rebuilt worker image | 79 passed (one pytest cache permission warning, no test failures). |
| Final-image synthetic Arabic OCR smoke | Clean 3.09% CER; degraded 5.15% CER; elapsed 20.95s / 7.09s, peak RSS 1759 / 2215 MiB. Same result as prebuild image diagnostic. |
| Arabic scan API → Temporal E2E | Internal synthetic scan uploaded successfully; workflow COMPLETED in 22.4s; one page, three OCR blocks, cache miss as expected; source and parsed artifacts stored. |
| Isolation | Snapshot comparison of containers outside Compose project `b2c` before/after worker rebuild/recreate showed no changes. |
| Import boundaries | Last verified 6 kept, 0 broken; rerun after final changes before gate. |
| Compose worker | `b2c-worker-1` is running with command `b2c-worker`; `docker top` confirms `/app/.venv/bin/python /app/.venv/bin/b2c-worker`. This fixes the earlier `sleep infinity` override that prevented task consumption. |
| Worker image | Rebuilt with locked dependencies; image id 6ce5b859c9329e9dd927eb29ca64c01b23f6ee60808c2ce21c284b174eb7c1dc; default command b2c-worker; running. |
| Arabic synthetic OCR smoke | 216-DPI: clean CER 3.09%, degraded CER 5.15%; 300-DPI trial: clean CER 30.93%, degraded CER 5.15%. High-DPI setting reverted. Diagnostic only; synthetic single page, no generalization claim. |
| Datalab Surya 2 candidate smoke | Pinned GGUF; 0% CER clean and degraded on the same 97-character synthetic page. At server limit 4 CPUs: 200.83s / 201.44s; server peak about 1.29 GiB, client RSS 441 / 461 MiB. Exploratory unconstrained run: 28.7s / 41.5s. Candidate only; no real-book gold set. |
| Prior compose-backed digital workflow | 37-page PDF upload returned 202, Temporal reached COMPLETED in 3.72s, MinIO retained source/parsed/outline/test-approval artifacts; duplicate upload preserved workflow ID. This did not test the current Arabic OCR change. |
| Data disk | `/mnt/wsl/b2cdata` was verified mounted read-write earlier in this session; Windows logon persistence remains unregistered/unverified. |

## Test coverage against the Phase 2 plan

| Test ID | Current evidence | Gate status |
|---|---|---|
| T-ING-01 page/block references | Digital coverage plus live one-page Surya scan path: 3 OCR blocks persisted with page-1 bounding boxes; outline heading and lesson spans also point to page 1. Real-book/mixed-page coverage remains unmeasured. | Partial |
| T-ING-02 headings/tables/layout goldens | No licensed, human-labeled representative golden corpus or scores. | Not run |
| T-ING-03 OCR accuracy | Synthetic clean 3.09%, degraded 5.15%; 3% draft target unmet. ACO scan PDFs contain no embedded text, but their pages have not yet been transcribed/labeled, so no real-scan CER can be reported. | Fail / incomplete |
| T-ING-04 multi-column negative case | No labeled oracle/negative fixture. | Not run |
| T-ING-05 deterministic/idempotent output | Same scan re-upload reused the workflow and both stage artifacts; force reprocess missed both caches and reproduced the same parsed-content SHA-256. Verified on one synthetic scan only. | Partial |
| T-ING-06 upload validation and workflow start | Live API check after recreating only b2c-api-1 from b2c-api:dev (image sha256:ec6640a8cd154c5efd07ad38701ccda6de26516af5dfc2598c3817539bfc4edb): declared text/plain returned 415; application/pdf with bad signature returned 415; 209,715,201-byte PDF against 209,715,200-byte cap returned 413. API health returned 200 and existing port 127.0.0.1:8100 was retained. MinIO inventory before/after was identical (92 objects, 3,886,377 bytes, SHA-256 920acb0244c4ca331b2fad6d1eab35c568dc0a4dad9ace5895e380ef87914821). API log showed only the three reject responses; rejection returns precede artifact persistence and Temporal-client access. Focused API unit suite: 12 passed; spies confirm zero artifact puts and zero workflow starts for these invalid uploads. No Temporal history query was performed. | Pass for requested live validation cases; no artifact side effect observed; workflow side effect excluded by code path and unit spies |
| T-ING-07 status and approval lifecycle | Prior live scan workflow completed through approval; duplicate upload/cache, force reprocess, and worker-restart persistence passed. Real Temporal retry test ran on existing B2C Temporal with a unique workflow/task queue and the real B2C MinIO ArtifactStore. Attempt 1 received an injected transient ConnectionError before MinIO download; Temporal retried and attempt 2 succeeded. Result: pages=1, blocks=2, cache_hit=false; parsed-book.json was read from MinIO (844 bytes, SHA-256 6e331329bf28416a7773050125af2c8060cafa9be0e274dcff2e94023dcc8aa2). Four exact test objects were removed; bucket inventory returned to its initial hash. The unique completed Temporal workflow history remains, as requested; no existing history or DB rows were deleted. | Pass for requested transient retry check |
| T-OUT-01 bookmark/heading outline | Synthetic scanned PDF bookmark produced a one-module/one-lesson outline with valid block/page spans, persisted through approval. Representative corpus coverage remains open. | Partial |
| T-OUT-02 no-structure fallback | Temporal activity calls the configured gateway in bounded structured batches, validates exact contiguous block partitioning, labels lessons `llm_inferred`, and forces `requires_review=true`. Remote inference is disabled unless `B2C_OUTLINE_REMOTE_EVAL_DATA=1`; local inference needs no opt-in. Nine focused unit tests pass. New worker image built and one-off configuration/mock smoke passed; live no-TOC model call and human approval remain unverified. | Code/image smoke pass; live inference/approval pending |
| T-OUT-03 complete spans/coverage | Builder rejects missing endpoints, reversed/overlapping/non-consecutive spans and coverage below 98% of non-empty body characters (headings/page furniture excluded). Six focused unit tests pass; representative-book coverage is not yet measured. | Partial |

## Open defects and work

1. Compare PP-OCRv5 with the promising but CPU-slow Datalab Surya 2 candidate on stratified, human-labeled real-book pages; include both recognition quality and throughput/memory. Do not promote either based on this synthetic page alone, and do not relax the draft threshold.
2. Create a rights-cleared Arabic/English textbook gold set with source hashes, page references, human transcription, scan-condition tags, and documented permission. The four ACO PDFs are scans with zero extracted text on all 968 pages in total; they are candidates for visual review, not ground truth.
3. The worker image is reproducibly built and a synthetic scan passed through B2C API/Temporal/MinIO/outline/approval with page provenance and cache/restart checks. Run representative real scans after the labeled corpus is prepared. The separate ai company project was not changed.
4. Add labeled digital, low-resolution, skewed, blurred, rotated, mixed-page, table, and multi-column cases, including negative controls that the evaluator must reject.
5. Owner-selected live checks now pass: MIME mismatch, bad PDF signature, over-limit upload, and Temporal retry through real MinIO. Exact evidence and scope are recorded in the 2026-10-04 retest section. The separately tracked corrupt/password-protected cases, live no-TOC inference and human approval, and representative-book coverage remain as previously recorded.
6. Verify data-disk auto-mount at reboot before relying on it for production-like runs.

## Persistent source and artifact reuse (D-023)

Source PDFs persist in MinIO and parse/outline use deterministic keys/get_or_compute. On the synthetic scan, duplicate upload returned the same workflow ID, both stages reported cache_hit=true, and the Surya request count did not increase. A forced rerun generated new stage keys while reproducing identical parsed bytes. Reuse across courses is not established. RAG, translation, and speech artifact/cache behavior is not implemented or verified. D-023 remains a cross-phase requirement; do not claim it is complete.

## Decision

TEMPORARILY CLOSED BY OWNER (2026-10-04). The two selected closure checks passed: the live B2C API rejects wrong MIME (415), invalid PDF signature (415), and a 200 MiB + 1 byte upload (413) without changing MinIO inventory; a real Temporal workflow retried a one-time download failure and succeeded on its second attempt using the real MinIO ArtifactStore. This is an owner-directed temporary closure decision, not a claim that every historical Phase 2 evaluation target passed. Do not reopen or add further Phase 2 closure items unless the owner explicitly asks.


## Real English scan benchmark and post-OCR correction design (2026-10-03)

A real scanned 1917 book page set with aligned transcriptions was evaluated from the persistent research-only corpus at `/mnt/wsl/b2cdata/eval-datasets/ocr/english-oldbooks-research-only/`. The upstream corpus is suitable for internal study; its item-level scan-image redistribution rights are not established, so do not publish the images. Results use lowercased NFC text, with whitespace ignored, and Levenshtein character error rate (CER).

| Candidate | Pages | Micro CER | Time | Notes |
|---|---:|---:|---:|---|
| PP-OCRv6 small English | 10 | 3.0573% | 36.61s total; 3.808s median/page | Current fast baseline |
| PP-OCRv5 English mobile | 10 | 3.6671% | 38.96s total; 3.988s median/page | Slower and less accurate on this corpus |
| Surya 2 GGUF, official HTML OCR prompt | 2 selected pages | 1.2543% micro CER (29/2,312 chars) | 295.74s total; 147.87s/page | 4 CPU cap; selected one difficult and one easy page; exploratory only |

Surya page-level comparison: on difficult page `j017`, CER was 2.067% (29/1,403 edits) in 168.83s versus PP-OCRv6 8.624% (121 edits) in 3.937s. On easy page `j008`, Surya scored 0% (0/909) in 126.91s versus PP-OCRv6 0.110% (1 edit) in 3.128s. This suggests Surya could be a slow, optional fallback for selected difficult pages, not a whole-book default. Two pages are insufficient for a model decision. A first ad-hoc prompt returned layout-only JSON; the valid OCR run used Datalab's documented training-time prompt, `OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000).` The temporary benchmark container has been removed. Source: [Surya 2 model card and usage](https://huggingface.co/datalab-to/surya-ocr-2-gguf).

### Proposed two-stage correction evaluation (D-024)

1. **OCR correction (text-level):** correct likely recognition errors while preserving the author's actual words, spelling, numbers, and order. Prefer an OCR-specific sequence-to-sequence corrector trained/evaluated on aligned OCR→ground-truth book text (including synthetic corruptions calibrated to observed errors). When available, align independent OCR hypotheses and use confidence/consensus as evidence. Never replace the immutable raw OCR output.
2. **Language proofreading (text-level):** a separate, later candidate pass for punctuation and clear grammatical errors. Preserve historical spelling, terminology, names, quantities, quotations, and meaning. Treat generative corrections as proposals until measured; a fluent rewrite is not proof of OCR correctness.

Each stage must keep raw input, candidate output, edit/diff, model/version/settings, source page provenance, and confidence/review status as separate versioned artifacts. Calibrate page/line risk thresholds on a representative validation split and hold out entire titles for final evaluation. Report CER and WER before/after each stage, over-correction (correct text damaged), insertions/deletions, numbers/proper-noun preservation, latency, and review volume. Test both low-error and high-error pages; accept a stage only if it improves held-out accuracy without material regressions. Generic LLM prompting is not yet approved as either stage: a 2025 COLING evaluation found fine-tuned sequence-to-sequence models generally outperformed generative models on its historical Dutch corpus, while noting that findings are domain-specific ([paper](https://aclanthology.org/2025.coling-main.690/)). OCR-D's documented workflow aligns multiple OCR hypotheses before optional statistical/language/word-model post-correction and evaluates output against ground truth ([workflow](https://ocr-d.de/en/workflows), [QA metrics](https://ocr-d.de/en/spec/ocrd_eval.html)). Production document services likewise recommend calibrated confidence thresholds and routing uncertain items to human review ([AWS](https://docs.aws.amazon.com/textract/latest/dg/textract-best-practices.html), [Google Document AI](https://docs.cloud.google.com/document-ai/docs/handle-response)).

**Status:** architecture agreed for evaluation, but no text-correction candidate has passed comparison yet. Docling `Block.confidence` is currently optional and not populated by the PDF parser, so confidence-based selective routing needs explicit implementation. The correction-stage gate remains open; Phase 2 remains FAIL / continue.

## Modern printed-book OCR decision check (2026-10-03)

The evaluation scope is ordinary printed books. `Seat Weaving` (1917) remains an aged-print stress cohort only; it is not representative of typical current uploads. A small exploratory cohort was prepared from two contemporary-style printed books already in the B2C source cache: six clean 216-DPI PDF rasterizations plus two synthetic downsample/blur/skew variants. The references are extracted from source PDF text layers, not manually transcribed, so CER is diagnostic and can be distorted by formula/table reading order.

The current B2C Docling/RapidOCR parser was exercised on an image-only eight-page PDF through `parse_pdf_with_docling` (PP-OCRv6 detector and recognizer; 4 CPU cap). It processed all 8 pages in 78.22 seconds (about 9.78 seconds/page including one-time parser startup). Whitespace-insensitive, lowercased NFC CER against the extracted references was 22.57% micro on the six clean pages and 2.14% on the two synthetic degraded variants; the latter unexpectedly scored better, confirming that this small, non-human reference set is not a release gate. Per-page clean CER: Lane pages 2/7/18 = 4.49%/23.47%/77.03%; Think Stats pages 52/56/60 = 4.50%/21.65%/6.34%. The severe math/table/figure cases show PP-OCRv6 alone is not a reliable one-model solution for all page types. Raw outputs and page-level measurements are retained in `/mnt/wsl/b2cdata/eval-datasets/ocr/modern-printed-books-test/ppocrv6-production-path-exploratory.json`.

**Candidate decision:** keep PP-OCRv6 as the fast English baseline for ordinary prose. Keep Surya 2 as the quality-mode candidate: prior measured English old-print pages, the Arabic synthetic comparison, and the modern-print follow-up below all returned OCR, with lower diagnostic error but high CPU latency. The previous modern-print request was cancelled by a 10-second client timeout; a later result was cut at the 2,048-token output cap. Those were harness limits, not model failures. OvisOCR2 loaded on CPU but did not finish one modern page within the bounded multi-minute smoke run; it is not a viable fast CPU choice in the current runtime. No text-correction candidate passed regression checks (OCRonos worsened raw text; T5 grammar correction damaged perfect text).

This remains a **provisional two-mode shortlist**, not a claim that the OCR acceptance gate passed: PP-OCRv6 for speed; Surya 2 for quality when long page latency is acceptable. Do not promote either to a universal default based on these eight pages. Surya has now returned a complete modern prose page, but the references are PDF text-layer extractions and layout/table/generalization plus human-adjudicated mixed-layout checks remain open. Phase 2 remains open because no candidate yet passes a human-adjudicated mixed-layout gold set and correction regression gate.




### Surya 2 modern-print client-path fix (2026-10-04)

Reused the existing healthy `b2c-surya-modern-retry` llama.cpp server and the `lane_p002.png` page. Inspected the pinned Surya 2 client source (`surya-ocr==0.22.1`): its trained full-page contract is the exact prompt `OCR this image to HTML. Each block is a div with data-label and data-bbox (x0 y0 x1 y1, normalized 0-1000).`, with the image part before the text part. The client must allow long CPU generation; prior 10-second request cancellations and a 2,048-token cap caused the apparent no-result / incomplete-result failures. No model or worker image rebuild was required.

With image-first ordering, the exact prompt, 4,096 max output tokens, temperature 0, top_p 0.1, and a 3,600-second client timeout, Surya returned 11 ordered HTML blocks with text, headings, list structure, math markup, and page number. The response ended naturally (`finish_reason=stop`) at 2,385 completion tokens after 641.15 seconds. Peak observed server memory was approximately 1.74 GiB; server CPU used about 27 cores at peak. Against the page's extracted PDF text layer, normalized whitespace-insensitive NFC CER was 0.36% (8 edit operations over 2,230 reference characters). This is diagnostic only because the reference is not human-adjudicated. The raw request result is retained at `/mnt/wsl/b2cdata/eval-datasets/ocr/modern-printed-books-test/surya-lane-p002-result-final.json`.

**Operational finding:** Surya works on the modern printed page when the client follows its exact prompt/image order and does not apply a short timeout or low generation cap. It is very slow on CPU (about 10.7 minutes for this page), but returns usable text. Treat Surya as an explicit quality/slow path; use at least a 30-minute request timeout and a 4,096-token output budget for this page class, with cancellation surfaced to the caller rather than silently reported as an OCR failure. Phase 2 remains open pending human review, broader page/layout validation, remaining deferred API checks, and reboot validation.


## Scan API-to-approval/cache/restart E2E (2026-10-04)

Scope: one-page internal synthetic Arabic scan with a PDF outline bookmark, uploaded to the live B2C API; Surya selected temporarily on the B2C worker. No ai-company service was touched.

- Built and deployed worker image `b2c-worker:dev` (`sha256:be7638624662dc19943bcd5bc62d20b59b399e39f0592fc3fed7fb8af7cb484b`). Runtime was returned to the configured `rapidocr` default after the test.
- A first scan fixture without a structural heading reached OCR and persisted its ParsedBook, then correctly stopped at the existing non-retryable `OutlineNeedsReview` guard. This confirms T-OUT-02 remains an open product path; it is not OCR failure.
- A valid fixture with a synthetic PDF outline completed API upload -> Temporal -> Surya OCR -> MinIO ParsedBook -> deterministic outline. Workflow: `b2c-ingest-2636bb859272379ddd7a6373f1dc130356fe610aff1e2670d7d3d4fb77b03eeb`. Source PDF SHA-256: `d9b46d73c6a5f8bd8cd977a60751402f55772395e5774179329ab5fa1a379047`. Workflow result: 1 page, 4 blocks, 3 OCR blocks, 1 module, 1 lesson.
- Read the persisted ParsedBook back from MinIO: all 3 OCR blocks reference physical page 1 and include page bounding boxes; the PDF-outline heading references page 1 without OCR. The lesson span covers page 1 and points to known block IDs.
- Posted the draft outline to the approval endpoint; the approval artifact was saved and then read back from MinIO. Reviewer label was `phase2-e2e-automated-check`; this is an automated lifecycle check, not a human quality review. Approval artifact SHA-256: `93ed5d4292d0d8033d3518b54434d4a74b57f5ca01244a43e30647d4b98426df`.
- Re-uploaded identical bytes and metadata: API returned the same workflow ID. Temporal returned the completed result with `cache_hit=true` for both parsing and outline; the Surya completion count did not increase.
- Re-uploaded with `force_reprocess=true`: a distinct workflow completed, both stages had `cache_hit=false`, and the parsed content SHA-256 remained identical (`ca01708793f53d6c29d7449c2f422ef7fd173e501dc24db743b23db73bd16491`). This demonstrates explicit cache bypass without output drift on this fixture.
- Restarted only `b2c-worker-1`; it rejoined the queue, the completed workflow stayed readable, and the ParsedBook plus approval artifact remained readable from MinIO.
- After verification, the worker was recreated in `rapidocr` mode and the temporary Surya server attachment was removed from `b2c_b2c_internal`. API health stayed OK.
- Host unit/contract suite after making `python-bidi` a required dependency: **94 passed, 0 skipped**.

Gate interpretation at this earlier checkpoint was that transient retry evidence remained missing. The 2026-10-04 live retest below supersedes that point: Temporal retried a transient download failure against real MinIO and returned a readable parsed artifact. The existing overall Phase 2 decision and other recorded criteria were not changed by this targeted retest.

## Optional table-reader smoke and free-API reconnaissance (2026-10-03)

**Scope:** implementation smoke only; not a Phase 2 acceptance gate. Built the B2C worker image once to include the Gemini table adapter. The first app run correctly surfaced two runtime issues: the temporary key file was unreadable by UID 10001, then the worker's internal-only network could not resolve the public Gemini endpoint. Fixed the RAM-only file ownership and attached only the B2C worker to the existing edge network through a temporary Compose override. No other project was changed.

**Evidence:** submitted a two-page image-only PDF through POST /v1/books and observed COMPLETED, page_count=2, block_count=18. The application detected one table on page 1, sent only that crop to Gemini 3.5 Flash-Lite, and stored a table block at page 1 in its original position; table cell values matched the cached extracted reference for the 36-outcome table. Page 2's plain prose remained ordinary paragraph/heading/list blocks under the normal local extraction path. The successful Gemini request used 1,123 prompt and 423 output tokens. This confirms routing and placement for one clean English table page only; no quality claim for other layouts/languages.

**OpenRouter check:** live /models catalog returned 22 entries with zero per-token prices at check time; music-generation models with per-song fees are excluded from the free shortlist. Both deepseek/deepseek-v4-flash:free and deepseek/deepseek-v4.1-flash:free returned HTTP 404 (unavailable for free). The current catalog has paid DeepSeek V4.1 Flash with image input; do not silently route to it. Image smoke attempts for Qwen3.8-27B and Gemma4 26B/31B returned upstream shared-pool HTTP 429. Dots3-Note Preview returned HTTP 200 at zero reported cost but exhausted a 1,500-token output budget in reasoning without any transcription text (finish reason: length). No OpenRouter free vision model passed this smoke; candidate shortlist for later retry is Qwen3.8-27B, Gemma4 26B/31B, Dots3-Note, and Nemotron 3 Nano Omni. Free availability and shared-pool limits are volatile.

**Provider setup:** added docker/compose.kiosapi.yml and host-file/model placeholders to .env.example; Compose config validation passed. The supplied Kios credential was not persisted and its model-list request returned HTTP 403, so runtime use is unverified. Gateway unit suite: 26 passed. Phase 2 remains OPEN: a one-page clean table smoke does not replace a manually adjudicated mixed-layout gold set or the remaining OCR/correction acceptance gates.


## 2026-10-04 — OpenRouter candidate routes and quota guard (image built; no live provider request)

- Recorded the tested vision candidates: Qwen3.8 27B Free and DeepSeek V4 Flash Vision Exp. Kept local RapidOCR as the default and Surya as the local quality option. Gemini remains confined to Docling-detected table crops.
- Added opt-in `B2C_OCR_ENGINE=openrouter`; native PDF text stays local, scanned pages are rendered and sent one page per vision request with physical-page provenance. Each page request uses the LLM Postgres cache, so completed pages are reused when a long job resumes.
- Added a durable B2C free-model guard: 40 reserved requests per UTC day and at most 10 requests/minute (6-second spacing), counting failed attempts. Upstream shared-pool 429 gets at most two bounded retries; account quota/rate limits stop without paid fallback. This app counter cannot see other clients using the same OpenRouter account.
- Added explicit Qwen-free and paid-DeepSeek Compose overlays. Gemini's existing RAM-backed Docker secret is wired by an opt-in table-only overlay; no key is copied into `.env` or the repository.
- Added bounded text batching by up to 10 page segments/24,000 characters and automatic binary split if the response hits the output-token ceiling. Per-batch gateway cache allows completed batches to be reused.
- Vision sample comparison on the same 8 pages completed: Qwen 8/8 successful after upstream shared-pool 429 retries, weighted CER 49.99% / WER 64.51%, $0 reported; DeepSeek 8/8, CER 23.03% / WER 38.89%, $0.01162 reported. Qwen's hard English table hit the 3,500-token cap and is truncated; KITAB hard-table scores are poor for both. These metrics are exploratory only and are not a human-gold release gate.
- Worker image `sha256:b92b6702...` and API image `sha256:ec6640a8...` were built from the updated tree. One-off worker smoke confirmed Qwen free model configuration, the 40-request daily safety cap, 6-second interval, batch limits, and presence of RAM/host secret files without reading secret values. Mock gateway/Gemini suite: 27 passed. API image app import and `POST /v1/books` route registration smoke passed. No running B2C service was restarted and no live request was sent to Qwen or Gemini; deployed behavior remains unverified. The raw model sample is in `/mnt/wsl/b2cdata/eval-datasets/ocr/reference-benchmarks/openrouter-sample-results.jsonl`; the concise report is in the active Codex workspace at `work/MODEL-SAMPLE-RESULTS.md`.


### Phase 2 closure audit received (2026-10-04; refreshed after subsequent work)

The audit baseline listed missing upload validation, retry, and no-TOC implementation. Since then, API validation tests/image smoke and the 2026-10-04 live endpoint checks passed; the Temporal transient-retry check passed with the real MinIO ArtifactStore. The gateway-backed no-TOC implementation plus coverage guard passed unit tests. The audit's other findings and the previously recorded overall Phase 2 decision are unchanged by this targeted retest.


### Outline no-TOC fallback and coverage guard (2026-10-04)

The outline package exposes an explicit inference-provider seam (infer_sections). Each provider proposal must identify a title and exact start/end block IDs; the package resolves those IDs against the immutable ParsedBook, derives page provenance from the covered blocks, marks each lesson llm_inferred, and forces requires_review=true. Unknown/reversed spans, overlap, non-consecutive spans, empty output, or less than 98% character-weighted coverage of non-empty body blocks are rejected. Page headers/footers and headings are not counted as body text.

The Temporal activity now invokes the configured LLM gateway through bounded structured batches, validates an exact partition of source block IDs, preserves page references, and forces human review. Remote inference remains opt-in for evaluation data only. No live model call or human approval has yet verified the entire no-TOC path. The new worker image contains the implementation, but current running services were not restarted.

Executed command: .venv/bin/pytest tests/unit/ingest/test_pdf_book.py -q — **6 passed**. The test exercises the no-provider stop, mocked inferred output and required review flag, provenance, full coverage, and rejection below the 98% threshold. No corpus-level acceptance claim is made.

## Data disk auto-mount task recheck (2026-10-04)

Manually started the registered `B2C-Data-Disk-Mount` scheduled task. Windows reports `LastTaskResult=0`; the task log confirms the existing mount at `/mnt/wsl/b2cdata` from `/dev/sde` (`ext4`, `rw`) and verifies `.b2c-data-disk`. This proves the task is repeatable while the current WSL session is running. It does **not** prove Windows/WSL reboot persistence; a real restart remains required for that gate. No disk was formatted or initialized.

## 2026-10-04 - Owner-selected live checks

The API-only recreation used the existing built image and preserved 127.0.0.1:8100. No worker, Temporal, MinIO, or non-B2C service was restarted. Invalid-upload responses were observed directly at POST /v1/books. The artifacts bucket inventory was unchanged before/after those requests; the focused API tests additionally verified no ArtifactStore put and no Temporal workflow start for rejection paths.

The transient-retry workflow used a unique task queue and workflow ID on the existing healthy B2C Temporal service, plus the real B2C MinIO ArtifactStore. Its first activity attempt failed with the one-shot injected transport exception; Temporal ran attempt 2, which downloaded from MinIO, parsed one page, wrote the parsed artifact, and returned successfully. The parsed artifact was then read from MinIO and its SHA-256 recorded above. The four temporary MinIO objects were deleted. The unique completed Temporal workflow history was retained; no existing workflow history or database rows were deleted. Final MinIO inventory again matched the baseline (92 objects, 3,886,377 bytes; SHA-256 920acb0244c4ca331b2fad6d1eab35c568dc0a4dad9ace5895e380ef87914821).
