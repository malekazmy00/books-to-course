# RAG model readiness — 2026-10-09

Scope: B2C only. Read-only inspection of repository code, existing model files, saved evaluations, and B2C container status. No model inference, download, service restart, or AI Company access was performed for this report.

## Connected versus tested

| Choice | Existing assets and evidence | Current connection | Required for operational selection |
|---|---|---|---|
| Lexical retrieval | `packages/indexing/src/b2c_indexing/query_service.py`; persisted structured chunks in PostgreSQL | Connected to course QA; runtime default | Preserve as selectable baseline with course/book/role filtering. |
| BGE-M3 dense / hybrid RRF | `/mnt/wsl/b2cdata/models/phase3-rag/bge-m3`, complete 2.27 GB PyTorch weights; Phase 3/4 saved comparisons; `docker/compose.retrieval-hybrid.yml` | Worker/API accept lexical/hybrid and inject BGE embeddings when configured; embedding service not running at this inspection | Start only the dedicated B2C embedding service after rollout authorization; verify authenticated readiness, persist stage choice, route worker to matching embedding endpoint. Internal QueryRetrievalService also supports dense, while current workflow contract exposes only lexical/hybrid. |
| BGE reranker v2 M3 | `/mnt/wsl/b2cdata/models/phase3-rag/bge-reranker-v2-m3`, complete 2.27 GB safetensors; saved evaluator results | Evaluator only; no production reranker call in QueryRetrievalService | Add scoped post-retrieval reranking and bounded batches, preserving source IDs; include timeouts and resource states before offering a running mode. |
| EmbeddingGemma 2 text 270M / LiteRT | `E:\B2C-Model-Lab\embeddings\embeddinggemma-2-text-270m.litertlm` (164,626,432 bytes); WSL `/mnt/e/B2C-Model-Lab/embeddings/embeddinggemma-2-text-270m.litertlm`; tested with `litert_lm.embedding_engine.EmbeddingEngine` | Tested locally in Model Lab; not connected to B2C embedding API or QA | Reuse the exact tested LiteRT artifact and CPU runtime; implement authenticated embedding service with distinct query/document input policies, 768-dimensional vectors, bounded batching and cache keys including model/policy. Configure worker and API to the service and keep model/dimensions separate from BGE-M3 1024-d vectors. |
| LightRAG 1.5.7 mix | `docker/compose.lightrag.yml`; pinned container image; `/mnt/wsl/b2cdata/lightrag`; completed isolated 37-chunk/50-query evidence | Dedicated service exists, but b2c-lightrag and embedding sidecar are stopped (exit 127); not connected to QA selection | Restore dedicated B2C runtime, validate full selected-course index completion and authenticated readiness, then add scoped source-hit adapter to QA. Persistent instance had 2 processed/35 cancelled records in the last documented check; completed isolated benchmark data is a different store. |
| HippoRAG 2 | Pinned source `/mnt/wsl/b2cdata/phase6-assets/src/HippoRAG-2bfd831417202b49cda9da7973e141a456e16872`; patched runtime; `eval/suites/phase6/hipporag2_bridge.py`, `run_hipporag2.py`; completed 44-query run | Isolated experiment plus opt-in GraphRetrieverAdapter; no production worker integration | Expose an isolated backend using explicit LLM/embedding endpoints and durable course-scoped storage; convert returned IDs to authorized source chunks; do not reuse arbitrary serialized graphs. Existing owner decision is NO-GO for default adoption, so retain as tested experiment pending explicit selection. |
| Project concept graph | `packages/kg/src/b2c_kg/graph_retriever.py`, `postgres_concepts.py`, `extraction.py` | Custom one-hop exact-entity retriever exists; early comparison used an empty in-memory graph, not a populated production graph | Populate and validate source-linked relations, then connect scoped adapter. Do not label this Microsoft GraphRAG. |

Microsoft GraphRAG was not found in the inspected B2C source, dependencies, Compose files, or RAG asset directories. It must not be presented as an installed/tested operational option from this evidence. The newer partial safetensors download at `/mnt/wsl/b2cdata/models/phase3-rag/embeddinggemma-2` is separate from the already tested LiteRT artifact and is not required to reuse the tested implementation.

## Existing evaluation evidence

The exact same 44-query Phase 6 subset (43 answerable, one absent) reports:

| Retriever | Recall@10 | MRR@10 | nDCG@10 |
|---|---:|---:|---:|
| Lexical | 0.9767 | 0.6875 | 0.7589 |
| LightRAG 1.5.7 mix | 0.9535 | 0.8036 | 0.8398 |
| Hybrid RRF | 1.0000 | 0.7929 | 0.8440 |
| Hybrid RRF + BGE reranker | 1.0000 | 0.8605 | 0.8962 |
| HippoRAG 2 | 1.0000 | 0.7054 | 0.7791 |

Sources: `eval/reports/phase-6.md` (paired verification refresh and Run 02), `/mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-results/phase6-lightrag-vs-lexical-verified-20261005.json`, `phase6-hippo-vs-lightrag-verified-20261005.json`. All labels are machine evaluated, not human gold. The one absent query was a false positive for every system. LightRAG's complete isolated 37/37-chunk, 50/50-query run is documented in `eval/reports/phase-4.md`, with results in `eval/reports/phase4/lightrag-v1.5.7-full-candidate.json` and `lightrag-v1.5.7-full-comparison.json`. Do not confuse that successful experiment with the incomplete persistent service index.

The aligned Model Lab comparison uses all source text with identical 768-character whitespace windows and character-weighted normalized means (37 chunks, 50 queries, 49 answerable):

| Embedding implementation | Recall@10 | MRR@10 | nDCG@10 | Document encoding | Mean query | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| BGE-M3 PyTorch | 0.9694 | 0.7851 | 0.8273 | 122.75 s | 0.2305 s | 2107.6 MB |
| EmbeddingGemma 2 LiteRT | 1.0000 | 0.7837 | 0.8358 | 74.15 s | 0.2425 s | 601.7 MB |

Source: `E:\B2C-Model-Lab\reports\aligned-retrieval-comparison.json` and `E:\B2C-Model-Lab\reports\aligned-retrieval-embeddinggemma2.json` (the latter is in reports/, not reports/miracl-v2/). Both use four CPU threads; concurrent work continued, latency is diagnostic; precisions/runtimes differ. Labels are machine adjudicated.

Additional public-data diagnostic: `E:\B2C-Model-Lab\reports\miracl-v2\embeddinggemma2.json` evaluates 200 sampled dev queries (100 Arabic + 100 English) and 1793 documents from MIRACLRetrievalHardNegatives. Overall MRR@10 0.8474, nDCG@10 0.8578, mean query embedding 0.2252 s. Each query is ranked over its judged positive/hard-negative candidate pool; these are not full-corpus MIRACL leaderboard scores. Small pools make Recall@10 weak for selection. Artifact integrity recorded in `E:\B2C-Model-Lab\reports\embeddinggemma2-smoke.json`: SHA-256 `2d079ee2f6f066b1f368e8d7c819f55214eaef1d0513b312321901f30ab286fb`, dimensions 768. Existing runners: `runtime/aligned_retrieval.py`, `runtime/miracl_hardneg_eval.py`, `runtime/embedding_retrieval.py` in the same Model Lab.

## User-selection contract

Present retrieval engine and embedding model as separate settings: lexical/hybrid/LightRAG/HippoRAG are engines; BGE-M3/EmbeddingGemma 2 are embedding implementations; reranking is an additional option. Show tested, installed, starting, loading, ready, or failed accurately. Save model, engine, input policy and provider in each new job/checkpoint identity; defaults must not silently alter an existing job's model provenance. Current `packages/api/src/b2c_api/model_lifecycle.py` generic embedding-local/external catalog entries have no working endpoints, so their presence is not execution routing. Only connected services that pass the actual readiness and scoped integration checks qualify as operational choices; the other tested candidates remain visible as preparation/experiment choices until connected.
