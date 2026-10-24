# Android progress/audio compatibility repair — 2026-10-14

## Verified causes
- Live API logs: legacy GET `/v1/course-qa/b2c-qa-9146da0974d35c6cd3199c5a652f1ca147d14f0f7b4635a84b7a4bc2fdcac6ef` returns503 after Temporal history lookup fails. Old clients do not attach the newer recovery query parameters. Read-only durable-registry inspection found its QA artifact only under `b2c-demo/book-aaea5c4b55e6a7519bdea580`; legacy `book-main` has the workflow ID but no QA artifact.
- Live API logs: GET `/v1/courses/b2c-demo/audio` returns405. Only the POST creation route existed; released Android clients still request the older GET listing contract.
- Android `run()` rejects every action while `pipelineRunning`, including opening the book library and viewing status/audio. Its executor is also occupied by the long pipeline wait. The existing independent book-monitor dialog cannot be reached through the blocked library button.

## Changes
- `b2c_api.qa_recovery.saved_qa_owner` discovers a unique saved QA owner from paginated book-progress records only on Temporal NOT_FOUND. The status endpoint still passes the resolved owner through existing book/source/artifact validation. Ambiguous owners and mismatched record prefixes are refused; no data is modified.
- GET `/v1/courses/{course_id}/audio` compatibility route discovers independently committed tracks through existing exact PDF+parsed-source checks, attaches book identity/download paths, and supports `book_id` filtering. POST creation is preserved.
- Android read-only library, status, audio, result, and lesson actions use `runRead()` on `monitorIo`, allowing access while `io` is occupied by the live pipeline. Processing-context changes remain guarded; existing independent monitor chooser supports viewing another book without rewriting live pipeline variables.

## Validation
- `pytest tests/unit/api/test_qa_checkpoint_recovery.py tests/unit/api/test_saved_audio_library.py tests/unit/api/test_source_identity_progress.py -q`:10passed.
- New/expanded tests: `test_qa_result_recovery_requires_matching_book_workflow_and_retains_progress`, `test_legacy_qa_owner_lookup_refuses_foreign_or_ambiguous_book_records`, `test_legacy_course_audio_get_preserves_book_identity_and_download_route`.
- Added Android instrumentation scenario blocking the pipeline executor while reading the library/audio and checking that processing book identity stays unchanged. Compilation/test execution status is reported separately; this report does not claim device verification.

## Deployment boundary
Source only. No service restart, container image build, inference request, or saved-result mutation performed. API publication is needed for503/405 fixes; new APK and device instrumentation are needed for UI behavior. Translation/generation worker restart is unnecessary for these repairs.


## Deployment and APK build

- Deployed the rebuilt API image to the B2C D runtime by recreating only b2c-api-1 with the exact Compose file set and hy-translation profile from its existing labels. /health is healthy after startup.
- Focused API regression tests: 10 passed. Authenticated live checks: book-scoped saved-audio returned HTTP 200 (21,627 bytes; 27.32 s); legacy QA status returned HTTP 200 with PARTIAL and recovered_from_artifact=true (394,470 bytes; 39.89 s). OpenAPI confirms GET and POST on /v1/courses/{course_id}/audio; unauthenticated call correctly returns 401 rather than 405.
- Rebuilt debug Android APK 0.5.7, versionCode 24, minSdk 24 (Android 7), armeabi-v7a only. Gradle assembleDebug and assembleDebugAndroidTest passed. APK SHA-256: de138bef422acb5450862879dabc806f261c73d110de1775b1aca919fe8d1c98; copied to the Windows Desktop as Book2Course-0.5.7-ARMv7-debug.apk.
- No Android device/emulator test was run because adb is unavailable in this WSL environment. The unfiltered legacy course-wide audio GET exceeded the 60-second client timeout during the smoke test; the APK's book-scoped saved-audio endpoint did return successfully, but its 27-second response is a performance item to optimize separately.
- HY translation worker container ID and start time were unchanged by API deployment; no generation, translation, Temporal, or database worker was restarted.


## Follow-up timeout and Drive upload (2026-10-14)
- Raised the Android API client's GET read timeout from 30,000 ms to 120,000 ms; the API health probe retains its 4-second limit, and long uploads/POST operations keep their existing timeout.
- Bumped APK to 0.5.8/versionCode 25; rebuilt ARMv7 debug APK and AndroidTest APK successfully. SHA-256: 45f68295af677bc1499b69917615978eb591df0670c88c4bd29705752f54931a.
- Uploaded and verified in Drive: https://drive.google.com/file/d/1nDvGpXqr8ZwNKyN6CwYTKgpxWy2SmHJ9/view?usp=drivesdk (125,809 bytes).

## APK 0.5.9 rebuild follow-up (2026-10-14)

Owner identified the prior desktop APK as Book2Course-operator-0.5.7-armv7-debug-2026-10-11.apk. A read-only comparison against 0.5.8 found five ZIP entries in each APK, no entry additions/removals, and equal sizes for the common AndroidManifest.xml and classes2.dex; AndroidManifest.xml and classes2.dex are the only differing entries. The 0.5.9 candidate was built from the currently available D-side source tree, retaining its current UI and 120-second GET read timeout, versionCode 26/versionName 0.5.9, minSdk 24, armeabi-v7a only. assembleDebug and assembleDebugAndroidTest succeeded. apksigner verify --verbose passed. SHA-256: fe5d004b93a33495a7fa32560f8c9ac2f6958189d8afa319345bc0298f9c6a18. Desktop copy hash matched. Uploaded as a new Drive file; previous APKs were retained. The original Ubuntu distro on C is not currently registered in WSL; only B2C-On-D on D and B2CRecovery (Alpine) on F are registered. A static Ubuntu-C VHDX backup exists on F, but it was not mounted or started, so this build is not claimed to be from that VHDX's source. No emulator/device run was available, and no worker or live processing service was restarted.



## Restored C-source UI merge: 0.5.10

The earlier 0.5.9 comparison used the wrong old APK. The actual preserved 2026-10-11 APK on D:C-drive-relief/APK-builds contained setup/monitor separation, course ZIP and per-unit exports, per-course membership renaming, summaries/exam choices, materialization and individual lesson audio regeneration absent in the D source. Read-only VHDX inspection through dissect.hypervisor/dissect.extfs found the full C source in F:WSL-Recovery/Backups/Ubuntu-C-2026-10-12/ext4.vhdx, without starting C or mounting the disk. MainActivity source hash: 6d0254cfdcf2ebdb2188596e515a141666120f3e0aaae07c0a58d223e4b5a59c; 212861 bytes. Android source staged at D:WSL-Recovery/apk-c-source-staging-20261014; pre-merge D files backed up under apk-pre-ui-merge-backup-20261014.

Merged restored source with D read-only monitor executor, saved-audio discovery, book rename, historical audio-path validation, QA recovery course/book parameters, 120-second GET timeout and source identity behavior. Kept restored audio browse workflow isolation, per-course folder/ZIP, full segment/track progress counters, stage model choices, materialization and separate result exports. Historical playback position now uses course+book identity. VersionCode27/versionName0.5.10, minSdk24, armeabi-v7a split.

Validation: assembleDebug + assembleDebugAndroidTest PASS; apksigner verification PASS; BookIdentityRegression and EndpointRoutingRegression PASS. Installed application and test APKs on existing B2C emulator-5554 via Windows adb port5038. OperatorNavigationInstrumentation PASS: agreed tabs, setup/monitor separation, per-stage models, independent summary/enrichment/exams, readable results and source-isolated audio. ProgressConcurrencyInstrumentation PASS: library, independent second-book monitor and audio refresh while the pipeline executor is blocked, with unchanged processing identity; every request mocked GET. No live inference or service/container restart performed. These focused tests do not claim every backend button was exercised against live production.

Final deliverable outputs/Book2Course-operator-v0.5.10-restored-ui-armv7-2026-10-14.apk; SHA256 f9672c0bfa8193f4387996e5b5d6c124b199fedecb92527eddc74fa878064231. Windows deliverable D:WSL-Recovery/apk-c-source-staging-20261014/Book2Course-operator-0.5.10-armv7-debug.apk. Drive upload remains coordinator work.


## Final restored-UI delivery — APK 0.5.11 (2026-10-14)

- Rebuilt from the merged C-source operator UI and current D API-compatible source: versionCode 28, versionName 0.5.11, minSdk 24 (Android 7+), ARMv7 (`armeabi-v7a`). APK size 148,105 bytes; SHA-256 `b2576d9587731a3b5fde22f69f14f6f503cf1eff5c9b286c7b0ed10d867aa8a7`.
- `assembleDebug` succeeded; `apksigner verify --verbose` succeeded. A separate x86_64 emulator build ran `AllControlsInstrumentation` on emulator-5554 (Android 8.1): PASS, 695 control/fixture events, no live requests. Coverage includes all five tabs, course/library selection and renaming, stage model choices and controls, separate book monitoring, audio refresh/list/download/course ZIP/single-lesson regeneration/player controls, result preview/export, provider settings, API routing, and guard states. Controls that require unavailable runtime capability were verified as guarded instead of invoking live work.
- Uploaded to Drive and metadata-verified (148,105 bytes): https://drive.google.com/file/d/1fKkMgRgttS_6ky89DBB3zQaczHEHHy7q/view?usp=drivesdk. Previous APK versions remain intact. The x86_64 test APK is not the user deliverable.
- Translation snapshot at 2026-10-14 17:20 UTC: Book1 body 227/776 (0 deferred), Book1 appendix 403/888 (5 deferred pending final retry pass), Book2 body 118/2620 (10 deferred pending final retry pass), Book2 appendix 644/644 complete. Temporal described the three active runs as RUNNING with one pending activity each. The worker2 correction-feedback rollout is active; deferred counts are not yet zero, so no claim of final translation completion is made.


### Follow-up live translation and runtime check — 2026-10-14 17:29 UTC

Read-only durable checkpoint snapshot: Book1 body 229/776 (0 deferred), Book1 appendix 423/888 (5 deferred), Book2 body 118/2620 (11 deferred), Book2 appendix 644/644 completed. Each of the three active workflows was RUNNING with a pending activity at Temporal inspection. Worker2 is running `b2c-hy-worker:terminology-repair-20261013`; the deployed `generation.py` SHA-256 is `3c2839d0524053605c5d327e100544c554790a04280b3f341050eae5b5fa84a5`, which matches the feedback-enabled source/image. Recent HY requests returned HTTP 200. The active Book2 body last-error category is `numeric_placeholder_order_mismatch`; it remains queued for the final deferred pass after main traversal. No saved result was overwritten or replayed. API container health probe returned HTTP 200.
