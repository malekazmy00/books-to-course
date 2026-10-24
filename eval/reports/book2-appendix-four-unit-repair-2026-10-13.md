# Book 2 appendix four-unit repair — 2026-10-13

Scope: B2C-On-D only. Active Book 1 HY activity was not stopped or restarted. No saved translation was deleted or sent for inference by this investigation.

## Findings from original requests, raw cached responses and durable status

Parent workflow `b2c-translate-2a041116176a753254098fb06230b0fec9313fbbd5b46005d6612c285d040614` is COMPLETED with a PARTIAL artifact, 640/644 validated checkpoints. It is not a hung active appendix.

- `m07-s-truc-ture-of-the-book.l01:lesson-qa:4:answer`: source “Chapters 3-6 ...”. Scanner incorrectly skipped the second range endpoint because it interpreted the range hyphen as a sign then rejected a number joined to another digit. HY consequently received NUM_1 followed by literal -6 and returned nonexistent NUM_6. All three raw attempts contain the same malformed marker. Fix scans both endpoints as unsigned range values, while retaining genuine signed measurements.
- `m29-renewal-decompos-i-t-i-on-and-ne.l01:lesson-qa:3:answer`: raw output omitted rigidification, mummification, morbid phenomena and becoming an anachronism. This is genuine omission. It was correctly rejected; the old recovery floor of 600 characters prevented adaptive repair of its 192-character enumerated source.
- `m59-c-o-u-n-t-e-r-c-u-l-t-u-r-e.l01:lesson-qa:2:question` and `...:answer`: source post-1968 was excluded from numeric scanning; Arabic output correctly contained 1968, producing an extra-number false rejection. Fix recognizes temporal pre/post/mid/early/late compounds without treating their hyphen as a numeric sign.

## Source changes and checks

`packages/translation/src/b2c_translation/generation.py`: recognize temporal/range hyphens; add configurable minimum split-piece size; bounded adaptive recovery for exhausted short units >=120 characters with pieces >=40 characters. Keep full original ID/text validation and checkpoint adoption; never checkpoint fragments independently.

`tests/unit/translation/test_generation_adaptive.py`: regression cases for chapter range, temporal year, genuine signed numbers; complete recovery and full-unit-only checkpoint assertion for a short omitted list.

111 focused checks passed: 88 adaptive/resume, 12 contextual glossary/editing, 11 orchestration checkpoint/partial-result tests. An initial command referenced a nonexistent orchestration test filename, ran zero tests, and was corrected to the three existing files.

## Retry and checkpoint safety

Resume child `b2c-translate-073b1a3ec802443add344082dcfafe3045303c0ee1c020c6c60234d9f8b4ecb8` was queued via original validated resume_request with force_regenerate=false. API readback confirmed exactly 640 inherited saved units and four remaining. Sorted saved ID/text SHA-256 was unchanged: `331c7dca00a022589516b12d2549721d1ba31ef4685147769c6be56e43c18107`.

Before dispatch, the child was re-described as RUNNING with its only activity SCHEDULED attempt 1. Parent coordinator requested holding it until corrected code is deployed. Only that never-started child was TERMINATED with explanatory reason; zero inference was issued. The 640 parent checkpoints remain durable. Live Book 1 activity was untouched.

Fixes are SOURCE-ONLY: do not claim four units completed or deployed. Deployment must preserve the active Book 1 checkpoint boundary. After deployment, recreate a child with explicit workflow reuse policy, or a fresh resume request nonce; resume_from_workflow_id still seeds and skips all 640 saved parent IDs. A fresh nonce must not remove resume_from_workflow_id.

At final live inspection Book 1 body remains STARTED attempt 1, 75/776 saved, progress updated 2026-10-13T12:19:36Z. Deployment is held to preserve its active work. Thin image build recipe: docker/worker/Dockerfile.hy-appendix-repair-20261013; image tag b2c-hy-worker:appendix-repair-20261013. It copies only translation generation.py over the current installed HY worker image.

## Deployed continuation and live closure evidence (supersedes source-only state above)

Safe boundary pause of three original workflows first committed Book1 body76 units, Book1 appendix3 units and Book2body41 units. Old deployment exposed a separate pause serialization bug: re-raising the original StageCheckpointPaused inside its wrapper exception produced a cause/context cycle; Temporal reported maximum recursion depth and retried3 times before old workflows FAILED. Their checkpoints remained intact. Fixed by preserving control exceptions directly in translation callback and using a fresh nonretryable application exception in the orchestration wrapper. Regression serializes the exception via DefaultFailureConverter;99 relevant translation/orchestration checks plus12 terminology checks passed.

Before service replacement, all3 activities were verified absent; worker was frozen and state rechecked to eliminate dispatch race. Replaced HY worker only, keeping model and other services alive. Worker image b2c-hy-worker:appendix-repair-20261013 started at12:37UTC. API-only numeric validator refresh uses b2c-api:hy-validator-repair-20261013. Both live generation.py hashes match c9b540ca074a2c4ec245d6730f7e74de42e4e76daf5b109eb8005b86605d08cb. API healthy. Compose override files docker/compose.hy-appendix-repair.yml and docker/compose.hy-validator-api-repair.yml select these images.

Four-unit child b2c-translate-1c6b7d92b959da710d8ee7113bdc3111f72f6e38b12b2469277d946e49b636fb COMPLETED with644/644 saved,0retry,0suspect. Sorted original640 ID/text hash remains331c7dca00a022589516b12d2549721d1ba31ef4685147769c6be56e43c18107 before/after. Raw cache audit after deployment found7 HY requests with the unique requested IDs EXACTLY the4 failed originals; no saved original was inferred again. m29 adaptive fragments produce only a full original checkpoint.

Personal semantic check found genuine m29 mistranslations rigidification→الجفاف and mummification→التماثل. With coordinator authorization, recorded a reversible operator correction through existing /corrections endpoint, revision db7bebd63dd64a3387b2b9f453930014, using التصلّب and التحنيط and retaining every listed concept. Original raw checkpoint remains available. API exposes corrected text. Publication child b2c-translate-de510a92d01a295a9fa61b52ec0dbf1162b1a7f95e7451e98f8c67e01f7d9d24 is queued with644 inherited,0retry,1operator revision; it needs no translation inference. Final corrected artifact is not yet committed because this publication activity waits behind running HY work. This distinction must remain visible.

Original3 continuations were resumed as exact checkpoint-seeded children, with all inherited saved texts hash-equal:
- Book1 body b2c-translate-03498a97c61d1d4390e91945675de8457251e87d9d2efc28fd9822a1e0cb2148:76/776;RUNNING/SCHEDULED.
- Book1 appendix b2c-translate-79d7295a94a06c300c1449651013c7715d33d03fdf5290faf8d96973e66a09ab:3/888 inherited, then7/888 and STARTED at final readback.
- Book2 body b2c-translate-34a38983393ed2772c77236afccdd6cfc39725197b65fc978bb153931b92e440:41/2620;RUNNING/SCHEDULED.

Canonical Book2 API progress points to its new body and publication children; canonical Book1 body points to its new child. Legacy Book1 QA lacks spine metadata, so API association omitted its new appendix pointer. Repaired that one progress stage only using prepare_backfill(OLD_BOOK_MAPPING), validating the exact QA source f626ae..., QA approval, parsed719c... and original PDFaaea...; retained previous stage history and all other stages with conditional ETag write. Pointer now79d729... . No immutable QA artifact was changed. Generic association for this metadata-deficient legacy QA remains a migration concern; current library pointer is repaired and verified separately.
