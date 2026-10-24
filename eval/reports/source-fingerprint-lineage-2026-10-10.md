# Stable source fingerprint and stage lineage — 2026-10-10

B2C only. No worker restart, deployment, production data mutation, or workflow operation was performed by this change.

## Cause and identity model

`ArtifactRef.sha256` hashes named output files. It is not the source PDF digest. Different OCR engines/options can produce different parsed SHA values for one PDF. Historical clients also reused `book-main`, permitting a later upload to overwrite the progress pointer for that alias; downstream exact-source validation then hides the older results rather than safely attaching them.

The original PDF fingerprint and exact derived artifact fingerprint are both retained. Matching a PDF fingerprint alone never bypasses exact parsed/outline validation or reuses translations of different text. New uploads already use content-derived server book IDs.

## Implemented

- Ingestion version 6 adds the original verified PDF digest to `ParsedBook.parser.source_file_sha256` and records the immutable upload ref in `ingest-source.json`. The source.pdf bytes are still SHA-verified before parsing. Legacy cached artifacts are preserved.
- `b2c_core.source_identity` resolves the exact parsed -> approval/QA -> translation source chain. It distinguishes stable PDF SHA from derived artifact SHA. Legacy artifacts lacking PDF metadata remain explicitly unverified; the digest is never inferred from a title or mutable ID.
- OCR stage imports validate any existing fingerprint against upload identity and attach the verified import fingerprint. Imported/indexed RAG snapshots use matching snapshot version 2.
- Outline output, approved outline payload, index per-book result/snapshot, QA full/partial output and scope/enrichment checkpoints, translation progress/batch checkpoints/source metadata, and newly generated audio track/manifests now carry source identity metadata.
- QA and translation checkpoint fingerprints are unchanged to preserve existing resume compatibility; the added identity is descriptive metadata and exact immutable source hashes remain reuse guards.
- Progress registry records upload SHA/ref at book and ingestion level. Replacing a legacy ID with a different PDF archives its previous stage references and clears the current downstream entries. Downstream read views validate parsed book identity as well as artifact SHA.

## Remaining release work

Deploy the prepared source only when coordinated by the parent. Reconcile legacy production `book-main` source artifacts by reading their original ingestion inputs/upload refs; never substitute the current alias source merely because book_id matches. Existing immutable artifacts are not rewritten by this patch. Audio may start only after its exact parsed/outline/translation chain is proved.

## Validation

Focused regression command includes source identity/import, ingestion cache, index/QA/audio activities, outline inference, translation partial durability/result/status, upload API, and workflow resume. The initial expanded regression run passed 72 tests. After alias/progress projection changes, 37 API regression tests passed, including a real ASGI progress request for old and new books and exact-source audio routing. Additional source/progress/audio/QA checks passed 20 tests. One upstream Starlette/httpx deprecation warning remains.

## Legacy external identity projection

Library records with a PDF digest now expose `book-<first 24 hex characters>` as external book_id and retain `artifact_book_id` for legacy immutable references. For the old book fingerprint aaea5c4b55e6a7519bdea580… this yields book-aaea5c4b55e6a7519bdea580. A legacy registry is projected read-only onto this external ID only after its library ingestion workflow resolves to the exact parsed SHA. Legacy stage/current-history references are exposed only when their parsed SHA matches that ingestion. The new book remains isolated by its own PDF-derived ID.

New stage progress writes for legacy artifacts use the library ingestion's exact parsed SHA to resolve the correct external ID. In particular, old audio progress is persisted under the old PDF alias rather than the currently reused book-main pointer. Conflicting source writes are retained in stage_history and never replace current stages. The API regression exercises both external IDs and verifies no cross-book audio attachment.

No runtime records were modified during preparation. Deployment and live reconciliation/verification remain with the parent agent.

## Explicit old-book backfill prepared and dry-run verified

Added `packages/core/src/b2c_core/legacy_source_backfill.py` and `scripts/backfill-old-book-source.py`. Default operation is dry-run; `--apply` is explicit opt-in. It streams/hashes every file for the three committed artifacts and compares aggregate manifests, validates source.pdf SHA against upload metadata, validates old parsed identity/677 pages/776 blocks, and verifies the exact approved outline -> parsed ref link.

Live read-only verification succeeded against these exact pinned values:

- Upload key cde04e1eb268f8bf1abb558729bd7959ef5f1403047e5d57856995a02dc510f7; aggregate SHA979d5c3919aad160744061adc51f96d19904c90ed71a829ef508f962b6e63432.
- Raw PDF SHAaaea5c4b55e6a7519bdea580fcab7075b0778da690bbe3534e97279a5c447e01.
- Parsed key e40a641ee1016034e6f24b1c191c32725472dc3edad6ae6ad0aecf9db0d67dcf; SHA719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3.
- Approved outline key8a4a762123950944953d19517db21d1ba8247f6181a46aebc3390d48fe0b0325; SHA03780a086d2f8462a0a2d1c94455179fabe717e5007c4d14f808dfe06670ba01.
- Explicit historical ingestion ID from the October8 recovery report: b2c-ingest-7fa128cc314aa3c679bdd75e93d6b746eb0663a0db713997356755af23f4ade8.

Dry-run proposed ingest/outline/book_translation under alias book-aaea5c4b55e6a7519bdea580. It never writes the legacy book-main progress record, the new book-70… record, or existing processing artifacts. Apply preflights conflicting destinations and is idempotent: repeated apply preserves later audio/translation progress under the alias. A PDF-indexed and parsed-SHA-indexed durable source mapping lets downstream source resolution verify legacy PDF identity without modifying ParsedBook bytes. `ingestion_recovery` now validates the immutable artifact_book_id for aliases, allowing old OCR text/status after Temporal history expiry.

Post-backfill changes: 31 focused source/backfill/API recovery/progress/audio/QA tests passed. Includes read-only preparation, corruption rejection, alias recovery after history expiry, idempotent apply preserving later audio progress, and legacy source resolution by the verified mapping. No runtime backfill was applied.
