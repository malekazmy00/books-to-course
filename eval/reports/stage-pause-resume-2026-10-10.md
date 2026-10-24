# Stage checkpoint pause/resume — staged source, 2026-10-10

B2C only. No live requests, deployment, or restarts performed.

Implemented POST /v1/workflows/{workflow_id}/pause, GET /v1/workflows/{workflow_id}/control, and POST /v1/workflows/{workflow_id}/resume. Control records are durable. Pause rejects invalid or stopped workflows. Resume releases the same workflow by the resume_stage signal; signaling failure can be retried. QA delegates to its existing scope checkpoint pause/resume routes.

Activities acknowledge PAUSED only after committed output. A nonretryable StageCheckpointPaused exits the activity; its workflow waits in durable Temporal history until resume. Activity capacity and start-to-close deadlines are released while paused. Resume repeats activity entry with immutable caches/checkpoints, preserving saved translation/rendering calls.

Actual boundaries:
- Translation: after a persisted translation batch checkpoint and progress. Resume loads accepted units and skips saved provider requests.
- Lesson/course audio: after an independently saved track and incremental manifest; rendering uses track cache on resume.
- Indexing: after an indexed book and chunk snapshot; prior data remains intact. Resume can repeat idempotent database/index maintenance for earlier books.
- OCR/ingestion: after the entire parsed-book artifact, before outline work. A pause during OCR finishes the current book before acknowledgment. Per-page pause requires parser slicing/checkpoints; it is not implemented or claimed.
- Whole-book single-file audio: pause rejected with HTTP 409 because there is no per-track boundary. Use lesson layout.
- QA: existing paused result and checkpoint-source resume flow retained.

Focused validation: 27 passed, using test_stage_pause_controls.py (orchestration and API), test_audio_tracks.py, test_translation_partial_checkpoint_durability.py, test_indexing_activity.py, test_ingest_cache.py, and test_course_qa_activity.py. New checks cover workflow activity/wait/resume ordering, same-workflow controls, invalid/stopped rejection, and translation saved-before-pause with no repeated saved provider request on resume.

Runtime integration and APK buttons require validation after coordinated deployment; source tests do not establish deployment completion.
