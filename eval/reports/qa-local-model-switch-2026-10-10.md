# QA local model switch — 2026-10-10

Scope: B2C only; external generation worker remains running and unchanged.

## Evidence

- Immutable Temporal start input for `b2c-qa-07ff671418659d205e1eed001cf2e51ef98975a78b74404cfb22bcced3fe1498`: course b2c-demo, source book book-70c88ae9b2ba9ed6f61e813c, approved outline SHA 42f38efbad22da07e2b6e6dd9a3a11f0db776d8c012df53bbb512326b1eabc1f, generation queue external-generation, model deepseek/deepseek-v4-flash-0731.
- A durable QA pause control was saved. The existing activity completed its current scope and returned COMPLETED / paused without stopping its worker.
- Read-only preview against real MinIO restored 51 scope checkpoints, including 49 final scopes and 253 accepted questions. The incomplete part is 180 of 229 scopes; two partial scopes retain their accepted items.
- Preview snapshot SHA 3853bc8a1270883a2868c884a322df020b87e1f9ab396e991361121a7a293705.
- Local worker configuration: qwen3-coder-30b at http://llm:8080/v1 with remote disabled.

## Repair

Explicit model changes previously ignored model_id and max_tokens in checkpoint comparison but still rejected provider-specific reasoning_effort differences. The comparison now ignores reasoning_effort only when allow_checkpoint_model_switch=True. Source outline, index, retrieval configuration, question configuration and prompt identity remain strict.

scripts/resume-qa-local.py defaults to a read-only preview. Applying requires a failed or paused terminal source workflow and validates the immutable outline/index fingerprints. It uses a deterministic new workflow ID, preserves the original inputs and checkpoint source chain, and routes the remaining generation to the requested local queue. A successful final workflow cannot be repeated.

Expected resumed ID: b2c-qa-7ea762566d239039d7d5a4942989d5e6cab11a55bb16e9a358f1640fa7bac7a9.

## Checks

- tests/unit/orchestration/test_course_qa_activity.py: 9 passed.
- tests/unit/pedagogy/test_qa_generation.py: 37 passed.
- script compilation passed.
- Real checkpoint read-only preview passed without inference.

## Deployment status

Worker image rebuild started; only worker will be recreated after the new source is included and boot verified. At this report's creation, no local inference has been started.


## Applied deployment and live verification

- Full OCR-heavy build was stopped as a build process only. An overlay image b2c-worker:qa-local copied the latest project packages/tests onto the existing pinned dependency image and passed all 46 focused tests inside the image.
- Added docker/compose.qa-local.yml selecting the overlay image for worker only. Recreated worker with --no-deps --no-build after confirming Temporal had no RUNNING workflows. Generation-worker and audio-worker were not restarted.
- Started the deterministic resumed workflow on heavy with selected_model_id=qwen3-coder-30b, selected_provider_id=null and allow_checkpoint_model_switch=True.
- Verified immutable Temporal input and worker log: qwen3-coder-30b, http://llm:8080/v1, remote=False.
- Live initial progress restored 49 completed scopes / 253 known accepted questions before new work. The first incomplete scope is processed; completed scopes remain skipped. 180 scopes remain non-final, including two partially saved scopes.
- Called the API's existing remember_book_progress function to associate the resumed QA with book-70c88ae9b2ba9ed6f61e813c, preserving all other stage pointers and attaching the PDF fingerprint at QA stage level.
- Book progress root lacks source_file_sha256 in the existing record; the identity agent has been alerted to backfill ingestion metadata. The QA stage fingerprint is correct.
