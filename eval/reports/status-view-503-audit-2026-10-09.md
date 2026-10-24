# OCR/index/RAG view 503 audit — 2026-10-09

Scope: B2C only; read-only live checks, source-only narrow fix. No workflows, inference, data writes, container restarts, or AI Company operations.

## Live finding

The running API still returns 503 for `b2c-demo` / `book-main` ingestion status and text preview. Its extraction recovery attempts to read the associated index workflow history; that history has also expired (`RPC NOT_FOUND`). Index status likewise maps NOT_FOUND to 503, while its old chunks handler raises an unhandled error. The running container does not yet contain the newer approved-outline recovery and recovered-index handler.

APK source uses `/v1/ingestions/{id}/text` for OCR and `/v1/indexings/{id}/chunks` for the index/RAG viewer. There is no separate retrieval query/view endpoint. Therefore provider readiness is not the cause of these viewer errors.

## Verified source recovery against real data

Executed the current `ingestion_recovery.py` read-only inside the running API environment. The exact book-progress record includes its approved-outline artifact. The source recovery validates committed manifest/hash, course/book identity and explicit source reference, returning 677 pages and 776 blocks. `recover_index` reconstructs the legacy index deterministically from the same approved outline and compares every chunk against PostgreSQL: 397 chunks, one book, exact match. It does not rerun extraction or persist reconstructed data.

## Narrow correction

QA generation preflight still bypassed `indexing_status` and required Temporal history directly. Changed it to use `indexing_status` so the same committed-index recovery also permits subsequent QA/retrieval work after history retention. Course and approved-outline SHA checks remain required. Explicit non-lexical providers still require readiness; no provider fallback was added.

Added five regressions in `tests/unit/api/test_index_history_recovery.py`: exact recovery, changed index parameters, mismatched database chunks, status/chunks HTTP recovery, and QA preflight using a recovered index without starting a workflow.

Focused check: `.venv/bin/pytest tests/unit/api/test_ingestion_recovery.py tests/unit/api/test_indexing_chunks_api.py tests/unit/api/test_index_history_recovery.py -q` — **13 passed**, zero failures/skips. Live HTTP verification after deployment remains required; source helper success is not a claim that the old running API is fixed.
