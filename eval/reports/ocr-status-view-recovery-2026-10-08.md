# OCR status/view recovery — 2026-10-08

## Observed failure
API log at 2026-10-08T17:21:26.443Z and subsequent 10-second polls: GET /v1/ingestions/b2c-ingest-7fa128cc314aa3c679bdd75e93d6b746eb0663a0db713997356755af23f4ade8 returned HTTP 503. Underlying Temporal error was RPC NOT_FOUND (`workflow not found for ID`). The API incorrectly mapped all describe errors to `Temporal is unavailable`. Adjacent book progress, indexing, QA, and translation endpoints returned 200.

APK `refreshStatusesBlocking` reads the book registry, then this ingestion endpoint. OCR text viewer calls /v1/ingestions/{id}/text, which also requires Temporal describe/result. Both paths therefore depend on a retained Temporal execution despite durable extracted content.

## Actual stored output
Read-only MinIO and associated completed index workflow input verified:
- course b2c-demo, book book-main, 677 pages, 776 blocks.
- exact committed parsed reference key e40a641ee1016034e6f24b1c191c32725472dc3edad6ae6ad0aecf9db0d67dcf.
- aggregate SHA256 719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3.
- created 2026-10-05T16:15:27.547483Z.
- The same exact reference appears in the index execution's starting input, connected by the book-progress registry. No latest-output guessing.

Missing ingestion history is consistent with retention expiry; retention configuration itself was not checked in this task. OCR inference failure is not the reason for this HTTP 503.

## Prepared source fix
Added API ingestion_recovery helper. On Temporal NOT_FOUND, match the requested ingestion ID to its durable book-progress record, use its explicit parsed reference or legacy associated indexing input, validate course/book identity, URI/stage, and committed manifest/hash. Return COMPLETED with completion_scope=extraction and workflow_history_available=false, explicitly distinguishing recovered extraction from historical outline generation. Text preview reads the same recovered result. Unknown IDs without associated committed output return 404; genuine Temporal unavailability remains 503.

No Android source changes, rebuild/deployment, live jobs, DB/data writes, or service restarts occurred. Source still requires deployment to take effect on the APK. Legacy recovery depends on the associated indexing input still being available unless parsed_book_ref has already been persisted in the book record; longer-term durable job-result index remains recommended.

## Evidence
31 focused source tests passed: 7 recovery regression tests plus 24 existing book-upload API tests. Includes successful status/text recovery, unknown ingestion, wrong book/course, uncommitted output, actual Temporal outage, and legacy index-reference recovery. The new helper was also executed read-only against real MinIO + Temporal and returned the exact 677-page/776-block reference above.
