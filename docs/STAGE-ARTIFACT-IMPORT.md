# Portable stage results and recovery

Implemented in source on 2026-10-08; no active worker was restarted or replaced.

`b2c_core.stage_library.StageLibrary` imports validated OCR (`ParsedBook`) and RAG
(`StructuredChunk` list) results and exports committed artifact files. The API must
resolve course ownership and the book's saved upload before calling this service.
An import does not infer provenance from a plain text file: a bundle carries
`format_version=1`, `stage`, `book_id`, the source PDF's `source_sha256`, `config`,
`payload`, and the canonical `payload_sha256` computed by `payload_digest`.

OCR imports preserve the normal `ingest-parse-pdf` stage, so outline/indexing can
consume them. Block IDs must be unique; every page reference must belong to the
uploaded book and fit its physical page count. Import requires the saved upload's
exact file digest and book role. Parser/config identity remains recorded in the
bundle, and changed contents/config produce a different immutable artifact.

RAG imports require the accepted parsed and outline artifact digests in config
(`parsed_sha256`, `outline_sha256`) and the exact `embedding_model` identity. Every
chunk must cite known source blocks, match their physical pages, and contain source
text. Imports replace one book's chunk index transactionally; unknown rewritten
passages, foreign books, duplicate chunk IDs, and changed outline versions fail
before index mutation. Import restores structured chunks without saved vectors;
lexical retrieval can use them immediately. Dense/hybrid retrieval requires configured
query and passage embedding clients and currently recomputes passage embeddings at
query time (with the embedding client's process-local cache); it does not load the
PostgreSQL embedding column. Import itself neither generates nor persists vectors.
Arbitrary external vector databases or unverified LightRAG graph dumps are not imported.

Indexing now commits immutable `chunks.json` snapshots before replacing PostgreSQL
rows. Source+approved outline+chunking settings+embedding identity determine the
snapshot key. A retry after database loss restores saved chunks and repairs the
database without redoing completed chunking. Each book in the index workflow result
includes its snapshot `artifact_ref` and `cache_hit` flag for download and diagnosis.

Export verifies course scope, committed manifest, and the aggregate file digest.
It supports existing OCR, outline, QA, translation and audio artifact refs without
inventing another storage system. Plain OCR text needs a page-aware conversion before
import; a foreign RAG system needs a converter to this schema. API/mobile wiring,
deployment, real restart validation and page-level local OCR checkpoints remain
separate work; these helpers alone do not establish all-stage recovery.

Focused evidence: `tests/unit/core/test_stage_library.py` and
`tests/unit/orchestration/test_indexing_activity.py`: 9 passed. Cases include imported
source/payload tampering, invalid page/duplicate block rejection, foreign-course
export rejection, RAG provenance/outline mismatch, and saved-chunk recovery after a
simulated database write failure.
