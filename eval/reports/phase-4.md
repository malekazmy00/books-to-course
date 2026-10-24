# Phase 4 preparation — Knowledge Graph comparison

Date: 2026-10-04 (Africa/Cairo)
Status: OPEN — the full same-corpus retrieval comparison is now measured, but Phase 4 acceptance is withheld because the remaining KG quality/cross-book/security gates are not all met. No backend is selected.
Scope: compare Phase 3 retrieval without a graph against graph-assisted retrieval on the same labeled corpus.

## Dependency and guardrail

Phase 4 depends on a repeatable Phase 3 corpus, chunk IDs, source/page provenance, and a labeled query set. Preparation can proceed now, but graph results must not be accepted until the Phase 3 baseline and its exact corpus/configuration are frozen. A graph is supplementary: it must preserve primary-book/supplementary-book roles and cite source chunks/pages. This preparation has not enabled a graph database, LightRAG service, or external model route.

> **Status clarification (2026-10-04):** The initial preparation note below predates the LightRAG trial and persistent-service setup. Its statements that no LightRAG service had been started are historical, not the current runtime state. The temporary trial described near the end was removed after its bounded pilot; a separate persistent service was started later and is verified in the final section. Neither change closes the Phase 4 gate.

## Candidate comparison set

1. **Control:** Phase 3 retrieval without a graph.
2. **LightRAG with PostgreSQL storage:** opt-in isolated candidate; verify current storage behavior, workspace isolation, deletion, API authentication, dependency/license, and resource cost.
3. **PostgreSQL adjacency/recursive SQL:** minimal project-owned typed edges and evidence references, as the low-complexity graph control.
4. **Apache AGE:** optional PostgreSQL extension candidate, contingent on compatibility with the pinned PostgreSQL image and repeatable install/restore. AGE is a PostgreSQL graph extension with Cypher/SQL interoperability ([official overview](https://age.apache.org/overview/), [official download matrix](https://age.apache.org/download/)).
5. **Neo4j Community:** optional isolated graph-store candidate only if PostgreSQL options fail a measured requirement; verify edition capabilities, deployment, and licensing before selection ([official Community Edition](https://neo4j.com/product/community-edition/)).

No candidate is preferred here. LightRAG documents API-key/JWT configuration and warns that a network-bound unauthenticated server exposes document/query/delete operations; every trial must remain on an internal B2C network with authentication enabled ([official LightRAG API implementation/security notice](https://github.com/HKUDS/LightRAG/blob/main/lightrag/api/utils_api.py)).

## Pre-registered evaluation

Run every candidate against the same frozen corpus, chunk IDs, query set, retrieval limits, and model-free retrieval settings first. Evaluate generation separately so retrieval changes are not confused with LLM variance.

| Test | Required evidence |
|---|---|
| T-KG-01 | Entity precision/recall against a manually adjudicated entity set; report by entity type and source role. Draft thresholds in `docs/03-TESTING.md` remain provisional. |
| T-KG-02 | F1 on 50 labeled entity-resolution pairs, including same-name/different-concept negatives. |
| T-KG-03 | Add a third book and prove records/citations from the first two remain unchanged. |
| T-KG-04 | Delete one book and verify no remaining graph edge, retrieval result, or citation refers to it. |
| T-KG-05 | Run all labeled cross-book questions; keep primary source distinct from supplementary evidence. |
| T-KG-06 | Unauthenticated request returns 401; service is reachable only on the internal B2C network. |

Also record ingestion wall time, query p50/p95, peak RSS, storage growth, restart/restore behavior, and exact hashes/configuration. Report per-query failures and source-role metrics; do not hide them in an aggregate.

## Current work and remaining dependency

- Phase 3 baseline development and tests are proceeding in parallel.
- Decision D-011 already treats LightRAG as provisional and requires comparison; this report does not change that decision.
- Not yet available: frozen Phase 3 index, adjudicated 50-pair entity-resolution set, and cross-book retrieval labels. Prepare them from rights-cleared sources and get review before interpreting scores.
- Gate status: **not run**. No graph software has been installed or started.

## Phase 4 implementation round — evidence-backed concept relation adapter (2026-10-04)

Scope: backend-neutral Concept Layer scaffold only. No database, graph engine, LLM, or runtime service was selected or activated.

### Implemented
- Added `packages/kg/src/b2c_kg/concepts.py`: typed page-aware evidence (`chunk_id`, `book_id`, source role, physical page numbers), a store protocol, and deterministic in-memory adapter.
- Relation writes require a 1:1 mapping between each `ConceptEdge.evidence_chunk_id` and page-aware source evidence. Repeated writes merge evidence idempotently; conflicting relation metadata is rejected.
- Reads are scoped by `scope_id`. Deleting a source book removes only its evidence and removes the relation only when no other book supports it.
- Added `tests/unit/kg/test_concepts.py` with negative controls for invalid page references, incomplete/unrelated evidence, and conflicting edge metadata.

### Executed evidence
Command: `.venv/bin/pytest tests/unit/kg/test_concepts.py -q`
Result: `9 passed in 0.20s`.

1. Create/retrieve preserves source chunk, page, and source role — PASS.
2. Repeated writes merge supporting chunks idempotently — PASS.
3. Scope isolation and source-book deletion — PASS.
4. Deleting one source retains an edge supported by another book — PASS.
5. Empty chunk ID, non-positive page, and duplicate page are rejected — PASS (3 parameterized cases).
6. Missing/unrelated page evidence is rejected — PASS.
7. Conflicting relation confidence/classifier is rejected — PASS.

### Not evaluated / still open
- No representative Phase 3 corpus, frozen retrieval baseline, or labeled graph questions were available for T-KG-01..05; quality/value comparison and all KG acceptance thresholds remain unmeasured.
- At this point, no candidate had been selected. The in-memory adapter was only a contract/testing scaffold; later preparation rounds below add a PostgreSQL candidate, without selecting it or claiming production readiness.
- No API/auth/network test for a graph service was relevant or run (T-KG-06 remains open until a candidate service exists).
- Phase 4 gate: **not run / not passed**. Next evidence dependency is a frozen Phase 3 corpus plus reviewed entity, entity-resolution, and cross-book labels.


## Phase 4 preparation round - durable PostgreSQL adjacency adapter (2026-10-04)

Status: candidate implementation only; not selected and not a Phase 4 gate result.

- Added a PostgreSQL implementation of the existing ConceptRelationStore protocol alongside the in-memory backend. It stores relation metadata and evidence citations in separate tables, keyed by scope and relation; each evidence row retains chunk ID, book ID, role, and page numbers.
- Repeated writes merge evidence by chunk ID idempotently. A conflicting confidence/classifier or conflicting source metadata for an existing chunk is rejected. Queries are restricted by scope. Deleting a book removes only its evidence in that scope, returns the number of affected relations, and retains relations still supported by other books.
- Schema setup is explicit and repeatable through ensure_schema; connections and their lifecycle are caller-injected. No graph package, AGE/Neo4j/LightRAG, Compose file, or running service was changed.
- Executed .venv/bin/pytest tests/unit/kg -q: 21 passed. The new PostgreSQL adapter tests exercise idempotent evidence merge, provenance reads, scope isolation, book deletion with retained cross-book evidence, conflict rejection, invalid input, and empty scope validation through a deterministic SQL-state test double. A live smoke then ran in a disposable container against the existing B2C PostgreSQL without restarting any service: scope isolation, repeated idempotent evidence write, deletion of one supporting book while retaining the second source, and exact cleanup all passed. The two temporary scopes were phase4-pg-smoke-ef5cae20c5 and phase4-pg-smoke-ef5cae20c5-other; their rows were removed and both scopes read back empty.

This adapter is a low-complexity comparison candidate only. It does not establish graph value or production suitability. Phase 4 still requires the same frozen corpus and labeled queries across RAG-only and graph candidates, measured entity/link quality, update/delete and isolation evidence, operational/license review, and an owner decision.


## Phase 4 preparation round - deterministic concept graph retrieval candidate (2026-10-04)

Status: implementation/test evidence only; not selected and not compared for retrieval value.

- Added ConceptGraphRetriever on the existing ConceptRelationStore protocol. Its scope is fixed at construction and every relation read is restricted to that scope. It matches complete, case-sensitive entity IDs in the query and expands at most one relation hop with deterministic scores (direct 1.0, expanded 0.5); no LLM, embedding, or fuzzy matching runs in the query path.
- Results are resolved only against the supplied source chunks, keyed by book/chunk/role. Stale chunk IDs and citations whose pages are absent from the source chunk are omitted. Optional explicit allowed_book_ids and allowed_roles filters apply to direct and expanded evidence; these are retrieval filters, not authorization controls. Returned objects retain chunk ID, book, role, pages, and block IDs.
- Tests cover direct matches, one-hop expansion, unknown and embedded IDs, same entity IDs in separate scopes, book/role filters, provenance, and validation. Focused command .venv/bin/pytest tests/unit/kg -q: 27 passed.
- Full unit+contract command .venv/bin/pytest tests/unit tests/contract -q: 173 passed, 0 skipped. Phase 3 report suite count is updated to this latest full run; no Phase 3 acceptance gate is implied by the count.

No frozen 50-query corpus or labeled graph comparison was run. This candidate does not establish a retrieval-quality benefit or a backend selection. Phase 4 remains open pending its same-corpus comparison, labeled evaluation, operational/security review for any service candidate, and owner decision.


## Live Postgres concept-graph retrieval smoke (2026-10-04)

- Ran `eval/suites/phase4/postgres_graph_retrieval_smoke.py` in a disposable B2C worker-image container on the existing internal Docker network. No project services were restarted.
- Used a unique temporary scope with two evidence-backed relations: AlphaConcept -> BetaConcept cited the exact primary/spine chunk and page 7; BetaConcept -> GammaConcept cited the exact supplementary chunk and page 22.
- `ConceptGraphRetriever` returned the direct citation at score 1.0 and its one-hop citation at 0.5, resolving each result to the exact supplied chunk with matching book, role, and page provenance. The spine-only role filter returned only the direct edge; the supplementary-book filter returned only the one-hop edge. A retriever configured with a second empty scope returned no results.
- Cleanup deleted the two uniquely scoped book evidence sets; the smoke asserted both the temporary scope and comparison scope read back empty. Result: **PASS** for this deterministic operational/provenance smoke.
- This smoke demonstrates adapter execution and scoped citation resolution only. It does not measure retrieval quality, prove a graph quality benefit, select a graph backend, or close Phase 4.


## Phase 4 preparation round - source-grounded concept extraction (2026-10-04)

Status: reusable extraction candidate and unit evidence only; no quality evaluation, selection, service integration, or gate pass.

- Added ConceptExtractionService with strict Pydantic output schemas for typed entity mentions and typed relations. The model and ConceptRelationStore are injected; no provider, endpoint, or service is hard-coded.
- Each call receives one StructuredChunk. Every entity and relation quote must be a literal substring of that exact chunk; entity names must also occur in their entity quote, and both relation endpoint names must occur in the relation quote after NFKC/case/whitespace normalization. Relation endpoints must additionally match entities returned for the same chunk. The response schema accepts no model-supplied chunk, book, role, or page fields.
- Entity IDs are deterministic from type plus normalized name. Book ID, role, chunk ID, and sorted unique physical pages are derived only from StructuredChunk. ConceptEdge requires a scalar confidence, so this candidate records a documented neutral 0.5 unscored sentinel; it is not a model confidence or quality measure.
- Added put_many to the relation-store protocol. The in-memory implementation stages and validates all changes before committing; the PostgreSQL implementation writes one batch within a single connection transaction. The service validates every chunk response before calling the batch write, so a malformed quote or dangling endpoint cannot cause partial writes.
- Focused command .venv/bin/pytest tests/unit/kg -q: 39 passed. Tests cover exact entity/relation quote checks, invalid endpoints, no writes after a later bad chunk, derived provenance, scope isolation, repeated extraction idempotency, in-memory batch rollback on conflict, and one-transaction PostgreSQL batch behavior through a SQL-state test double.
- Full unit+contract command .venv/bin/pytest tests/unit tests/contract -q: 185 passed, 0 skipped. No dependency was added and no running service or API was changed.

This establishes implementation behavior only. Model extraction accuracy, relation quality, and same-corpus Phase 4 comparison remain unmeasured; no manual gold data was created.


## Phase 4 preparation round - natural-language concept-name matching (2026-10-04)

- Updated ConceptGraphRetriever to match either the full typed entity ID or the canonical name suffix from IDs such as concept:probability. Name matching applies NFKC, case-folding, whitespace collapse, and hyphen/underscore-to-space normalization, then requires complete Unicode token/phrase boundaries. It rejects partial names and embedded malformed identifiers. Retrieval remains exact lexical matching with one-hop expansion; no fuzzy, embedding, or LLM path was added.
- Tests include natural-language questions, the full typed ID, partial-name collisions, malformed embedded IDs, and existing scope, book/role filtering, and provenance checks.
- Focused command .venv/bin/pytest tests/unit/kg -q: 42 passed. Full unit+contract command .venv/bin/pytest tests/unit tests/contract -q: 188 passed, 0 skipped. The Phase 3 report's latest suite count is updated accordingly.
- No production retrieval integration, same-corpus comparison, backend selection, or quality claim.

## Same-corpus graph candidate diagnostic and extraction feasibility (2026-10-04)

Status: executable diagnostic completed; Phase 4 remains open. No extracted graph facts were retained or scored.

### Frozen comparison

- Corpus: the existing frozen 37 chunks (30 Lane spine + 7 Think Stats supplementary), with the Phase 3 source manifest and corpus file hashes recorded in `/mnt/wsl/b2cdata/phase1-sources/phase4-current-graph-empty-diagnostic.json`.
- Queries: the same 50 Phase 3 pseudo-qrels, validated against the 37 source chunks. These are `model_auto_validated`; `gold_eligible=false`. They are suitable only for self-consistency diagnostics, not acceptance claims.
- Control: current project lexical BM25-style retrieval.
- Candidate: current `ConceptGraphRetriever`, with an empty in-memory `InMemoryConceptRelationStore` because extraction did not pass exact evidence validation. No database or service writes were made.

| Retriever | Recall@10 | MRR@10 | nDCG@10 | Interpretation |
|---|---:|---:|---:|---|
| Lexical RAG | 0.980 | 0.726 | 0.789 | Same 50 auto-labeled pseudo-qrels; diagnostic only |
| Empty ConceptGraphRetriever | 0.000 | 0.000 | 0.000 | Empty graph returns no candidates; this is not a graph-quality score |

The graph candidate's mean query time was approximately 0.000023 seconds over 50 questions, which reflects an empty in-memory lookup only. It says nothing about populated graph latency. The output includes the full metrics at k=1/3/5/10, exact query/corpus hashes, and cleanup statement. The result file SHA256 is `e2c78b6d22b23ccf267d2cad7163305c9ad96a04979b88ed52d7d438effd77f3`.

### Local extraction pilot

The local endpoint was the existing `qwen3-coder-30b` service on the B2C network. The first single-chunk pilot took 94.88 seconds and failed because an entity quote was not an exact substring. I tightened the versioned extraction prompt to explicitly prohibit Unicode normalization/paraphrase/OCR correction, set short output bounds, and ran a second single-chunk pilot with `max_tokens=512`. It took 63.99 seconds and failed because a relation target name was absent from its exact quote. Neither failed result was stored. Two calls took about 159 seconds total; a 37-call sequential run would likely require roughly 40–60 minutes before any invalid-output retries, so a full run was not started. This is an estimate from two pilots, not a service-level latency guarantee.

### Decision and next fix

This is evidence of an implementation blocker: strict provenance validation is doing its job, while the current model/prompt combination cannot yet produce consistently valid relation tuples at practical cost. Do not weaken literal quote checks or infer missing relation endpoints. Next concrete work is a bounded extraction design that separates entity mentions from relation proposals, validates each item independently (rejecting only invalid items while reporting rejection counts), and uses a faster already-installed local extraction-capable model if available; then rerun a small held-out chunk sample and only scale after exact quote and endpoint validation pass. The current experiment does not select a graph backend, prove graph benefit, satisfy T-KG-01/02 gold-label requirements, or close Phase 4.

Implementation verification after the extraction prompt version update: `.venv/bin/pytest tests/unit/kg -q` — 42 passed. This verifies contracts and prompt-version expectations only; it does not turn either failed model pilot into passing extraction evidence.

## Item-level extraction rejection diagnostics (2026-10-04)

- `ConceptExtractionService` now checks each entity and relation separately. An invalid quote or endpoint is rejected without discarding unrelated valid items in that response/batch; only accepted source-grounded relations are written.
- Result telemetry exposes `chunk_id`, item kind, item index, and a closed reason code (for example `entity_quote_not_exact_source`, `relation_target_not_in_quote`, `relation_endpoint_not_accepted_entity`). It never contains the model quote, source passage, or free-form exception text.
- Prompt version advanced to `concept-extraction-v3-exact-endpoints`; it explicitly requires exact Unicode-preserving quote spans containing both endpoints and the relation statement, and asks the model to omit unsupported items.
- Tests: `.venv/bin/pytest tests/unit/kg -q` — 43 passed. They cover partial acceptance, sanitized rejection records, exact-span/endpoint reasons, and persistence only for valid relations.
- No new Qwen call was made after the two failed pilots because Phase 3 was using the same local inference service. Inventory inspection found no other already-installed local generative chat model in the B2C model directory. A new small representative model run remains pending resource availability; do not scale to all 37 chunks before it yields accepted exact-span relations.

Suite after item-level telemetry and prompt v3 changes: `.venv/bin/pytest tests/unit tests/contract -q` — 189 passed, 0 skipped (8.77s). This is implementation coverage, not a Phase 4 gate pass.


## Extraction validation v4 and isolated LightRAG trial (2026-10-04)

### Concept extraction safeguards

- Rejection telemetry now includes chunk-level model failures as closed, privacy-safe reason codes; it contains no source excerpts or free-form exception text.
- Prompt version `concept-extraction-v4-explicit-relation-cues` adds conservative direction/type wording checks. Relation candidates are retained only when the source includes a matching explicit cue (for example, "X is part of Y"); mere co-occurrence is rejected. Earlier two-chunk Qwen candidates with semantically wrong relation types were excluded and never persisted.
- `.venv/bin/pytest tests/unit/kg -q`: 45 passed. `.venv/bin/pytest tests/unit tests/contract -q`: 191 passed, 0 skipped. These verify software behavior only; model extraction correctness remains unadjudicated.

### Pinned isolated LightRAG smoke

- Downloaded and ran the official `ghcr.io/hkuds/lightrag:v1.5.7` image, pinned to digest `sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e` (4,939,528,501 bytes; Linux/amd64). The v1.5.7 upstream license is MIT. The image reports Python 3.12.14. No vulnerability scanner or cosign binary was available, so CVE/signature verification was not performed.
- The trial used only a temporary container and volume on `b2c_b2c_internal`; no host port was published and no standing Compose service was changed. LightRAG API-key authentication was enabled, `WHITELIST_PATHS=/health`, and a non-default JWT secret was set. Authenticated health/OpenAPI calls returned 200; unauthenticated document insertion was denied (403).
- Model calls stayed local: the existing B2C `qwen3-coder-30b` endpoint plus a temporary BGE-M3 OpenAI-compatible embedding service using the same CLS pooling and L2 normalization as Phase 3. No paid or external model endpoint was used. Resource limits were 4 CPUs/10 GiB for LightRAG and 4 CPUs/8 GiB for embeddings. Snapshots during ingestion showed LightRAG at 318.6 MiB, BGE at 879.7 MiB, and Qwen at 25.23 GiB and roughly 20 CPUs; these are snapshots, not peak measurements.
- A two-chunk subset was indexed in one isolated store: Lane `lane-intro-stats:ch01` (spine, pages 1-2) and Think Stats `think-stats-2e:ch01` (supplementary, pages 51-53). Under default gleaning, Lane completed in 332 seconds and the Think Stats document hit LightRAG's 480-second worker timeout. A clean-volume retry with `MAX_GLEANING=0` processed Think Stats in 393.6 seconds and Lane in 140.2 seconds.
- Two local-mode queries returned populated graph results and source references. The Lane query returned 5 entities, 5 relations, and both chunk IDs; the PMF query returned 5 entities, 14 relations, and the Think Stats chunk. On this exact two-chunk subset, the project's lexical baseline ranked the same top chunk for both spot queries. The references resolve to book, role, and physical pages through the frozen source-chunk mapping.
- This was not the required 37-chunk/50-query paired evaluation. Only two source-derived spot queries were run; their labels are not human-adjudicated, and extracted graph relations were not human-reviewed. No Recall/MRR/nDCG claim or backend decision follows from top-1 agreement. The observed two-item `MAX_GLEANING=0` mean is 4.45 minutes per chunk, a rough point extrapolation of about 2.74 hours for 37 chunks before 50 queries or retries; the samples varied substantially and do not constitute an ETA. The bounded comparison is therefore incomplete and Phase 4 remains open.
- Detailed non-source-text results and hashes are recorded in `/mnt/wsl/b2cdata/phase1-sources/phase4-lightrag-two-book-pilot.json` (SHA-256 `974a84fb8606b28abb80f1b7a7ce018e08abd5097cbb99701e9faae782d3f077`). The temporary LightRAG container, volume, embedding container, and ephemeral credentials were removed after the trial; no persistent service change or host port remains.


## Persistent LightRAG runtime verification (2026-10-04)

This is a current operational snapshot, distinct from the earlier temporary pilot:

- `b2c-lightrag` is running and Docker reports `healthy`, restart count 0. `b2c-lightrag-embed` health endpoint returned HTTP 200.
- LightRAG `/health` and authenticated `/openapi.json` returned HTTP 200. A request without the API key was denied with HTTP 403. The current T-KG-06 table still names 401 specifically, so the denial demonstrates protection but the exact status-code criterion remains unresolved; do not silently count it as an exact pass.
- Docker inspection showed no published host port (`9621/tcp` maps to null) and attachment only to `b2c_b2c_internal`. The service retains 4 CPU / 10 GiB limits; the embedding sidecar has an 8 GiB limit. No AI Company resource or configuration was changed.
- Authenticated document status reports all counts zero, including `processed=0` and `all=0`; persistent data directory is 7.0 MiB. Thus the service is healthy and securely reachable inside B2C, but this current persistent instance has no corpus indexed yet.
- Existing B2C Qwen and local BGE-M3 routes are configured. No document ingestion was started in this verification because the earlier two-chunk pilot took 140.2 and 393.6 seconds with gleaning disabled (and default gleaning timed out at 480 seconds); scaling that same setup to 37 chunks was estimated at 2.74 hours before retries or queries. That estimate is only based on two variable samples.

### Comparison status and gate decision

- The existing 37-chunk / 50-query project diagnostic compares lexical retrieval against an **empty in-memory project ConceptGraphRetriever**, not LightRAG: lexical Recall@10 0.980, MRR@10 0.726, nDCG@10 0.789; empty graph returns no results. Its qrels are model-auto-validated (`gold_eligible=false`), so these numbers are diagnostic only.
- The historical LightRAG pilot indexed two chunks and ran two source-derived questions. Both happened to match the lexical top-1. The pilot was not a paired 37-chunk/50-query run; its labels and extracted relations were not human-adjudicated, and its latency makes it unsuitable as an acceptance benchmark.
- Focused software verification now run: `.venv/bin/pytest tests/unit/kg tests/unit/eval/test_phase4_compare.py -q` — **48 passed**. This validates implementation contracts, not LightRAG corpus-level quality.
- **Phase 4 remains OPEN.** No evidence currently supports selecting LightRAG or a project-owned graph over the RAG-only control. Required next evidence is a frozen Phase 3 dataset suitable for comparison, a bounded/credible same-corpus LightRAG run with source attribution, reviewed entity/relation labels for T-KG-01/02, update/delete/cross-book checks (T-KG-03..05), and resolution of T-KG-06's expected 401 versus observed 403. The LightRAG runtime itself remains healthy and empty so it can be used for a future controlled run.


## Full isolated LightRAG comparison and current gate evidence (2026-10-04)

This later run supersedes earlier statements that no 37-chunk LightRAG comparison had been run. It does not change the persistent service or select a graph backend.

### Paired retrieval run

- A B2C-isolated LightRAG `v1.5.7` trial was pinned to image digest `sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e`. Authentication was enabled; the service had no published host port and ran only on `b2c_b2c_internal`, limited to 4 CPUs / 8 GiB. The persistent `b2c-lightrag` service/store and AI Company were not changed.
- The frozen corpus was the 30-chunk Lane spine plus 7-chunk Think Stats supplement (37 total; combined corpus SHA-256 `7d2778a46fd9f92d296765fd20b938d21551af12a6f4e7103c924ede8c2a0d6c`). The 50-query development set SHA-256 was `4361df96655fb0a2ba70466ca16936c67fc409c74368fbc44925270bd19d8b99`. Its query/qrel judgments are automatically generated and exact-source checked; `gold_eligible=false`.
- Ingest completed 37/37 source chunks with no pending, failed, or processing documents. Retrieval returned 50/50 successful queries, 500 resolved source IDs, no empty result, no unknown chunk IDs, and preserved book, role, page, and section-path attribution.
- Against the same dataset and corpus, project lexical baseline scored Recall@10 `0.980`, MRR@10 `0.726222`, nDCG@10 `0.789315`; LightRAG `mix` scored `0.960`, `0.791778`, `0.832614`. LightRAG therefore improved early ranking metrics but reduced Recall@3/5/10; this is a diagnostic comparison, not evidence of a universally better retriever. The 39 spine-query slice had Recall@10 `0.949` for LightRAG vs `0.974` lexical; the 11 supplementary slice scored `1.000` for both.
- Cold LightRAG query latency: mean `2.683 s`, p50 `2.831 s`, p95 `3.921 s`, max `4.027 s`. A later rerun used cached responses and is reported separately in `eval/reports/phase4/lightrag-v1.5.7-full-comparison.json`.
- The explicit DeepSeek V4 Flash 0731 extraction track made 88 successful provider requests (37 ingestion and 51 query-related), with no fallback: 121,848 prompt tokens, 32,229 completion tokens, zero reasoning tokens, provider-reported cost `$0.04310521`. Including the pre-run extraction/integration canaries and retries, the complete experiment total was `$0.0542257292`. No paid requests were made after retrieval-only evaluation began. Detailed result JSONs are `eval/reports/phase4/lightrag-v1.5.7-full-candidate.json` and `eval/reports/phase4/lightrag-v1.5.7-full-comparison.json`.

### Operational, deletion, and third-book checks

- **T-KG-03 PASS (isolated live persistence check):** inserted two book scopes, snapshotted the original three entity IDs, then inserted a third book. All 3/3 original entities remained and both third-book entities appeared. The temporary database scope was deleted and read back empty. Executable evidence runner: `eval/suites/phase4/t_kg_03_three_book_smoke.py`.
- **T-KG-04 PASS on the isolated LightRAG store:** deleted the seven supplementary documents, reran the 11 supplementary-labeled queries, observed zero deleted-source citations, and verified zero references to the seven deleted source IDs remained in the store. Sanitized evidence is `eval/reports/phase4/lightrag-v1.5.7-delete-smoke.json` (SHA-256 `7dc5f89ced2b6aae7cff2359bd8eb5f125c8d627847359c9f7ded11a415df354`).
- **T-KG-06 protected but exact criterion not met:** unauthenticated access is rejected with HTTP 403 (authenticated access succeeds); the current table specifies 401, so do not count exact T-KG-06 as passed. Network isolation, enabled auth, and no published port were independently inspected.
- **T-KG-01 / T-KG-02 remain NOT EVALUATED.** No independent, source-grounded entity reference set or 50 entity-resolution pair set exists. Automatic headings or graph output alone would create a narrow or circular label set, not a defensible precision/recall benchmark. No manual labels were introduced. The configured Top-Tools-Ai service returned HTTP 502 for both `/models` and the canary; no request fallback was used.
- **T-KG-05 remains NOT EVALUATED:** the frozen 50-query set contains no cross-book qrels (39 spine-only and 11 supplementary-only), so it cannot establish cross-book retrieval success.

### Acceptance decision

**Phase 4 remains OPEN / NOT PASSED.** The previously missing full-corpus retrieval comparison, document deletion smoke, and third-book persistence check now have measured evidence. The measured comparison does not establish graph value across all query types; entity extraction/resolution quality and cross-book graph cases are still unevaluated, and T-KG-06's exact status-code gate differs from observed behavior. Keep LightRAG opt-in and do not route production retrieval through it. No additional paid inference is authorized by this report.

## Final Phase 4 research decision — 2026-10-05

This section supersedes the older interim gate statuses above. It records a **closed research decision with a negative production recommendation**; it does not mark the Phase 4 quality/acceptance gate as passed.

### Results

- **Frozen retrieval comparison:** 37 chunks (30 Lane spine + 7 Think Stats supplementary), 50 source-checked machine-generated queries, dataset SHA-256 4361df96655fb0a2ba70466ca16936c67fc409c74368fbc44925270bd19d8b99, corpus SHA-256 7d2778a46fd9f92d296765fd20b938d21551af12a6f4e7103c924ede8c2a0d6c. LightRAG v1.5.7 mix completed 50/50 queries with no invalid source IDs and preserved source/page attribution. Its Recall/MRR/nDCG@10 were 0.960/0.7918/0.8326 (mean latency 2.683 s). On the same queries, lexical was 0.980/0.7262/0.7893, dense BGE-M3 0.960/0.7918/0.8326, RRF hybrid 1.000/0.7986/0.8484 (mean 1.247 s), and RRF+BGE reranker 1.000/0.8800/0.9107 (mean 31.380 s). Attribution integrity was 1.0 in the LightRAG run. All qrels are automatic, gold_eligible=false; this is diagnostic evidence, not a gold benchmark.
- **T-KG-01 — diagnostic quality below thresholds:** the 87-chunk DeepSeek V4 Flash 0731 track recorded all 87 chunks; 386 accepted entity mentions had exact source quotes, and 15 chunks were valid abstentions with no entities. Against LightRAG's generated names, only 24 overlapping Lane chunks could be compared: TP=51, FP=157, FN=68; precision 0.2452, recall 0.4286, F1 0.3119. This fails the provisional precision/recall targets. The independent side is another automatic model output, not human gold. Evidence: eval/reports/phase4/phase4-deepseek-vs-lightrag-entity-agreement-final.json and /mnt/wsl/b2cdata/phase4-kg-eval-20261004/phase4-deepseek-3book-entities-final.json.
- **T-KG-02 — diagnostic below threshold:** 50 automatically adjudicated, source-quoted pairs from the 30 processed Lane documents (12 repeated-surface candidates, one ambiguous formula-symbol pair, 37 lexical near-match candidates). The measured LightRAG candidate resolves a pair only when both mentions are present and their normalized names are identical. Confusion matrix: TP=8, FP=0, FN=6, TN=36; precision 1.000, recall 0.5714, F1 0.7273, below the 0.90 threshold. This candidate-enriched set is diagnostic, not a random sample or human gold. Evidence: /mnt/wsl/b2cdata/phase4-kg-eval-20261004/tkg02-entity-resolution-result.json; frozen candidates SHA-256 1035f2a09b7f3a42163eb4e6861b3b6018cb77b498d536898914d2eae316f565.
- **T-KG-03 PASS:** adding the third book retained the original 3/3 entity IDs and added the new entities; test scope was removed afterward.
- **T-KG-04 PASS:** after deleting the seven supplementary documents, no deleted-source citation or reference remained in the isolated candidate store.
- **T-KG-05 NOT PASSED:** DeepSeek evaluated the 39 existing spine-only questions against all seven Think Stats chunks and accepted zero supplementary qrels. A separately bounded generation request using Lane ch17 and Think Stats ch01 abstained and returned zero questions (2,196 input / 7 output tokens, provider cost $0.0000423392). No cross-book retrieval score is claimed because there was no accepted cross-book label/query to score. The frozen query set itself has no cross-book qrels.
- **T-KG-06 PASS (security behavior):** authenticated read returned 200; unauthenticated read/query/insert/delete/graph-write probes returned 403; data counts remained unchanged. The gate accepts either 401 or 403. Evidence: eval/reports/phase4/t_kg_06_auth-smoke.json.

### Cost, backend decision, and disposition

The recorded DeepSeek LightRAG extraction/comparison track was $0.0542257292; the separate 87-chunk exact-quote entity track was $0.0230704490; T-KG-02 and the 39×7 T-KG-05 adjudication together were $0.0057834792; the one-pair question-generation abstention was $0.0000423392. Total recorded Phase 4 provider cost is approximately **$0.0831219966**, within the authorized $0.25 Phase 4 cap. Requests pinned deepseek/deepseek-v4-flash-0731, reasoning_effort=none, JSON object output, price sort, and disabled provider fallback. No secret values were recorded.

**Decision: NO-GO for putting a knowledge-graph/LightRAG path in the production retrieval default. Close the bounded research evaluation as negative, with Phase 4 acceptance explicitly NOT PASSED.** The measured hybrid RAG baseline provides better Recall@10 and nDCG than LightRAG mix at lower latency; extraction agreement and entity-resolution F1 miss their thresholds, and no cross-book qrel was produced. Keep production on the existing RAG path. A later reconsideration requires an independently validated entity set, a passing 50-pair ER benchmark, and a genuinely source-supported cross-book set; these are future evidence requirements, not work claimed done here.

No AI Company files/services were used or changed. The LightRAG trial remained isolated to B2C; the persistent B2C LightRAG store was not repurposed for these evaluations.


### Free-model canary check (2026-10-05)

A read-only OpenRouter model-list check returned HTTP 200 (466 model records); 22 records advertised exactly zero prompt and completion price. The selected exact model, qwen/qwen3.8-27b:free, advertised $0/$0. The key-status endpoint returned HTTP 200 and showed account usage $0.228382364, configured limit $8, remaining $7.771617636, and is_free_tier=false; therefore the account itself is not a free-tier account. No account balance was treated as authorization for paid inference.

One explicitly zero-priced model request was sent for a single existing first chunk of the frozen ch03 document, with provider fallback disabled. It returned HTTP 200 from the exact selected model, reported 1,328 prompt tokens, 512 completion tokens, total 1,840, and provider-reported cost $0. The completion stopped at the 512-token limit. The isolated evaluation harness then raised a TypeError while handling the response content, so valid JSON/entity/relation counts and quality could not be verified. This is availability evidence only, not a successful extraction canary; no second request or bulk free-model reindex was made. The current service's attempted local retry was requested to cancel at a safe checkpoint; no claim is made that cancellation has completed or that its corpus state is now fully processed.

The completed, same-corpus offline paired comparison remains the usable evidence for the research decision above. The canary does not change the NO-GO production recommendation or authorize production wiring. Further free-model retry/reindex requires a separately bounded, parseable canary and a verified quota/rate-limit allowance; no paid route or fallback is approved.



### Free Qwen extraction canary and paired replay follow-up (2026-10-05)

The canary harness was repaired to handle content-block responses and null assistant content explicitly, to save the sanitized assistant message/content and SHA-256 hashes, and to request JSON output with reasoning disabled. Six local decoder/quote-validation unit tests pass. Two diagnostic requests before the final bounded canary had shown a content-block list and then null content at the output cap; they were not treated as successful extraction.

The one final canary used the same source chunk, exact listed model qwen/qwen3.8-27b:free at $0/$0, provider fallback disabled, JSON mode, reasoning_effort=none, and max_tokens=4096. It returned HTTP 200, finish_reason=stop, 3,087 prompt tokens, 762 completion tokens, and provider-reported cost $0. The JSON parsed to 9 entities and 6 relations. Only 5/9 entity quotes and 2/6 relation quotes were exact contiguous source strings (7/15 overall); several other quotes dropped spaces between words. The source-grounding check therefore failed. No 37-document reindex or production change followed.

The sanitized full assistant message and content were saved to /mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261005/qwen-free-ch03-chunk01-canary-attempt3.json; artifact SHA-256 d5ee7b601e90d0e1d8ddb1ed0d3d629c6bbbe60c0c61675af375e0a28f071245, raw-content SHA-256 b20992d659d651f801f502881d7bf205d009540d0a1aebbb84789d9dc2779483. The persisted file was checked to contain no secret, and the stored raw-content hash was recomputed successfully.

A fresh offline replay of the saved paired 44-query rankings used identical Phase 6 query/corpus hashes (dataset ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65; corpus 73e325e08b1b523a6cd0ecb335d83ac59ea8007da75ee2d4a08a0c8217637815). It is a scoring replay, not a new live LightRAG run and does not include the one-chunk canary. At k=10, LightRAG R@10/MRR/nDCG were 0.9535/0.8036/0.8398; lexical was 0.9767/0.6875/0.7589; attribution integrity was 1.0 for both. The LightRAG ranking artifact is the same saved completed 50-query run filtered to the exact 44 IDs. Replay output is /mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-results/phase6-lightrag-paired-replay-after-canary-20261005.json. Labels are still automatic diagnostics, not human gold; the Phase 4 NO-GO production decision is unchanged.

