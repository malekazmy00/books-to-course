# HY Book 2 body orphan activity recovery — 2026-10-13

Scope: B2C on B2C-On-D only. No service restart and no inference request was sent by this investigation.

Workflow: b2c-translate-acd3463d527d3bffbf7280cef0bde4adb0a832cbcba62eb6e4a4236ae5afa0d2; activity 1, translate_content.

## Cause

Temporal still recorded activity STARTED on worker 1@416d3b808afa at 2026-10-12T18:03:04Z with last heartbeat 19:18:31Z. Docker inspection proves that same container was restarted at 19:27:15.978695741Z. Thus the pending body activity belongs to the terminated previous process. The current process started appendix translation at 19:27:34Z, and appendix heartbeats remained current. The workflow has a 24-hour start-to-close timeout and no heartbeat timeout, so a lost process does not promptly release its activity.

Book 1 body and appendix were SCHEDULED, not STARTED. The HY worker allows one concurrent activity; they are queued alongside the recovered body. No guaranteed FIFO order is asserted.

## Recovery

Added optional B2C_RECOVERY_WORKFLOW_ID exact-target filter to scripts/recover-lost-b2c-activities.py. Used existing lost-worker comparison and Temporal reset_activity with reset_heartbeat=False, then failed only the orphan attempt as retryable. The exact target recovered count was 1. No worker restart or current appendix interruption.

Verification: body activity 1 became SCHEDULED attempt 2. Appendix remained STARTED attempt 1 with unchanged activity start and heartbeat advanced to 2026-10-12T22:19:03Z. Both Book 1 activities remained SCHEDULED attempt 1.

## Saved progress

Before and after recovery: 16 durable MinIO checkpoint objects containing 41 unique translated segment IDs. Combined ordered object-content SHA-256 unchanged: c2a082ffc5d079894c311fe5c91d46d21bf1d2bd1032f8b57a461def65fc2ad8. translate_content reads every committed batch checkpoint and resumes saved segments.

## Checks

.venv/bin/pytest tests/unit/orchestration/test_reboot_activity_recovery.py -q: 6 passed. No translation output or source artifact was edited. Full body resume is queued until worker capacity is available.