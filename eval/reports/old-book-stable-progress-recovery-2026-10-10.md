# Old book stable progress recovery — 2026-10-10

Scope: B2C only; no AI Company changes. No generation worker restart and no audio inference.

## Verified identity and migration

The old upload PDF SHA aaea5c4b55e6a7519bdea580fcab7075b0778da690bbe3534e97279a5c447e01 maps to public ID book-aaea5c4b55e6a7519bdea580. The immutable artifact ID remains book-main, preserving its saved outputs and citation IDs. Parsed source SHA is 719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3 (677 pages, 776 blocks).

The backfill verified every named file's hash and committed aggregate hash for the exact PDF upload, parse, approvals, QA result, and appendix translation. The index workflow ID was verified by deterministic request identity. QA approval 8f187... and the current approval 8a4a... both explicitly reference the same parsed source. The QA result is 38d9...; appendix translation d6bff... explicitly references that QA result. Historical workflow input and output were read to independently confirm these pins.

Dry-run succeeded and apply created a new alias progress record containing ingest, outline, index, qa, translation, and book_translation. New book-70c88ae9b2ba9ed6f61e813c and the existing book-main registry were compared byte-for-byte before/after apply and were unchanged. Repeated apply preserves later alias progress and existing source objects.

The old cover OCR blocks 1:0–1:2 identify THIRD EDITION / STRUCTURED ANALYTIC TECHNIQUES / FOR INTELLIGENCE ANALYSIS. A new library metadata artifact records Structured Analytic Techniques for Intelligence Analysis, 3rd ed., with verified cover provenance. The old display name includes fingerprint suffix aaea5c4b; the new book retains its current title and suffix 70c88ae9. Both remain spine/main sources as requested.

## API and source validation

Focused source/backfill/identity/progress/upload/index-recovery/ingestion-recovery suite: 51 passed. Regression now covers approval recovery from an immutable exact-source artifact and PARTIAL QA display when the Temporal execution completed with a failed internal result.

Only b2c-api-1 was recreated. Authenticated live API verified library and both progress endpoints HTTP 200; unauthenticated library returns 401. All six old stages have parsed SHA 719c... and PDF SHA aaea..., while the new book retains parsed SHA 95271e... and PDF SHA 70c88...

Old display routes checked authenticated and returned HTTP 200: OCR text, indexing chunks/RAG, QA, main-body translation units, appendix translation units. The old index is recovered from deterministic persisted chunks/database after Temporal retention expiry. QA status endpoint correctly returns FAILED for its incomplete output; this is not a complete-quality claim.

API progress originally showed outline UNKNOWN because its extraction workflow history expired, despite a verified saved approval. Source fix now resolves outline approval from its committed source-bound artifact. QA registry workflow completion is exposed separately from PARTIAL result status.

## Deployment completion

Final image rollout and resulting stage statuses are recorded below after verification. Android build and audio remain parent-coordinated tasks.

Final API image: sha256:253efd32c128e63008a81973dddef119e83468523364b1a08a68417e691a3faa. Final authenticated verification after API-only recreation: health 200; library 200 with distinct display names; old progress 200 with ingest COMPLETED, outline COMPLETED, index COMPLETED, qa PARTIAL, appendix translation COMPLETED, main-body translation COMPLETED. Assertions verified all six old stages carry the exact old parsed SHA719c... and original PDF SHAaaea...; new progress 200 retained SHA70c88... . QA workflow completion does not change its failed internal result into a final-quality output.

## New-book root metadata reconciliation

Persisted book-70c88ae9b2ba9ed6f61e813c root lacked source_file_sha256; its API view already projected the valid upload fingerprint. Added scripts/reconcile-book-root-identity.py with dry-run default and conditional S3 IfMatch write, protecting against overwriting concurrent progress updates. Verified source upload key c5e8ea2c496dc914bef376abd28c249ff12c61c1a30c95c5dfa874b71c0b9f3a: every file matches the committed aggregate manifest, raw source.pdf SHA70c88ae9b2ba9ed6f61e813ca80e8685773ce18245477ed38e14d46eb7632292, matching canonical book ID and existing stage fingerprints.

Dry-run then apply filled root source_file_sha256, source_upload_ref, and artifact_book_id only. Before/after assertions confirmed stages and updated_at remained unchanged. Saved object equality verified the write. Repeated dry-run returns changed=false. No image rebuild, service restart, artifact modification, or Temporal operation was required.
