# Operator UX implementation — 2026-10-11

## Source implemented

Five bottom tabs now separate Library/Courses, Stage Setup, Stage Monitor, Outputs, and General Settings. Existing global library, course create/rename and per-course membership controls are retained. Stage prerequisites/targets, numeric controls, optional enrichment/assessments and per-stage model links are in Setup; workflow progress and pause/resume/retry remain in Monitor. All EditText controls now have persistent visible labels above values. Server-confirmed model defaults appear beside each stage.

Outputs contains audio playback (seek, previous/next, play/pause, stop and speed 0.75–2x), course audio folder lookup, workflow selection, per-file download and guarded complete-course ZIP, alongside OCR/RAG/questions/translation results. Audio list refresh runs through independent monitor executor, with loading/error/empty/count state directly beside its controls. Browsing a historical audio run never overwrites the active generation workflow. Downloads use server-projected display_filename and download_url. Segment progress and remaining/failed counters appear in audio stage progress. Question scopes can export their own human-readable text file; individual translation units can export their own text.

## Checks

- compileDebugJavaWithJavac + compileDebugAndroidTestJavaWithJavac: PASS (40 s final run).
- EndpointRoutingRegression: PASS.
- BookIdentityRegression: PASS.
- New OperatorNavigationInstrumentation compiles: verifies five tabs, setup/monitor separation, stage model selectors, source-run isolated audio browse/download and audio selection clearing.
- No adb device was attached at this check. Instrumentation execution, all-button device validation and user acceptance are pending; compilation does not claim those passed.
- No APK build/publish, server restart or live inference performed by this task.

## Remaining integration contracts

- Book display-name PATCH route and library propagation (bookDisplayTitle already honors display_name).
- Separate document/lesson export endpoint or client export packaging for complete sets (individual question/translation text is available).
- Audio regeneration after accepted edits: GET /v1/audio/{id}/request exposes original_validated refs only; latest accepted source resolution must land before regeneration can claim to use edited content.
- Current include_lesson_enrichment bundles summary/terms; independent summaries selection needs backend contract support.
- Cross-course artifact materialization remains the prior documented API requirement; membership metadata alone does not move source processing artifacts.

## Follow-up integrated against backend contracts

- Course membership display-name editing now calls PATCH memberships with display_name, preserving source book identity.
- Saved-result discovery uses course results route; each artifact exposes preview, individual file download and paginated separate-unit preview/download. Existing stage-specific readers remain available.
- Full result ZIP uses authenticated POST export streamed into cache, followed by Android document save; the archive includes backend-generated separate-unit JSON/Markdown and provenance.
- Independent summary/enrichment checkboxes and selected assessment scopes now send exact QA fields include_lesson_summaries, include_lesson_enrichment and review_assessment_scopes (explicit empty list disables exams).
- Audio include_summaries follows the operator summary choice.
- Selected-lesson audio regeneration validates lesson membership in selected book outline and builds a request from current committed source/QA/translation workflow refs. It requires complete gap-free translations, displays source URI/hash for explicit confirmation and requires user to resume translation after accepting edits first. It never reuses the old audio request, preserves historic files, selects exactly one lesson and disables extra scopes.
- Cross-course library membership remains metadata-only. App refuses to use unmaterialized cross-course books in current pipeline, preserves target course ID, offers independent source monitor and states the requirement directly.

### Follow-up checks

Main + instrumentation Java compile PASS (31 s). Instrumentation fixture expanded with explicit independent QA flags, empty-scope disabling and course-scoped result route encoding. Device execution remains pending; no APK assembled or deployed here.

## Final emulator validation and current limits

Earlier pending contracts are historical: course-specific book rename, saved results discovery/preview/unit download/course ZIP, independent summary/enrichment/exam selections and validated single-lesson audio regeneration are integrated. Saved-result previews format body, questions, answers and evidence; unknown JSON is indented. Enabled assessment selection cannot have an empty scope list. Regeneration requires selected audio source ParsedBook SHA to match the current book, and current committed translation refs after accepting edits and resuming translation.

Internal x86 assembleDebug + assembleDebugAndroidTest: PASS (42 s). Artifacts: app/build/outputs/apk/debug/app-x86-debug.apk and app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk. Windows copies: C:\Users\User\Documents\Codex\2026-09-29\new-chat\operator-ui-test-x86.apk and operator-ui-instrumentation.apk. Both installs SUCCESS on existing B2C_QA_API27 emulator-5554, adb port 5038, Android 8.1, boot_completed=1.

Runtime command: adb -P 5038 -s emulator-5554 shell am instrument -w com.book2course.operator.test/com.book2course.operator.OperatorNavigationInstrumentation.

Runtime result: BLOCKED, not PASS. No instrumentation result returned. ActivityManager showed the process starting without an attached activity process; direct am start -W also stalled. AndroidRuntime capture provided no crash explanation. At coordinator request, stopped only hung instrumentation/start adb clients (PIDs 30604/14916), then force-stopped com.book2course.operator. Adb server, emulator and backend were not restarted.

No production ARMv7 APK built/published. Internal x86 artifacts do not prove Android 7 ARMv7 delivery readiness. Full authenticated live all-button/API validation remains unproven. Backend routes must be deployed together with app. Cross-course materialization remains unavailable; app labels such memberships catalog-only and blocks processing instead of implying artifacts were copied. Latest source regeneration lineage guard is covered by final source compile, not the earlier installed x86 APK.

Final compileDebugJavaWithJavac + compileDebugAndroidTestJavaWithJavac after source lineage guard: PASS, BUILD SUCCESSFUL in 36 s.

## Cross-course materialization integration

The prior catalog-only gap is now resolved in Android source against the new backend contract. Selecting a cross-course source book or applying a multi-book composition preserves the selected target course, saves target-specific membership roles, then sequentially POSTs /v1/courses/{course_id}/memberships/{book_id}/materialize with no body. Membership PUT omits display_name because the backend preserves its existing name; explicit rename remains PATCH.

A per-book dialog reports waiting, loading, ready or a concrete error. Target course /books/{book_id}/progress is refreshed after commit. Async Handler polling checks /v1/ingestions/{workflow_id} every three seconds; target book/library and progress are read again only after COMPLETED. Composition/selection is applied only when every selected cross-course target ingest completes. Source objects and source course records are not changed; the selected local copy then uses target course/workflow. Existing OCR is reused by the backend, downstream results are not silently reused.

During preparation, context and stage-start controls are locked. Terminal failures, polling timeout or missing target library readback prevent downstream start and show a per-book error. Hidden dialog does not cancel backend work; reselecting resumes via the idempotent endpoint. Single-book membership selection preserves target membership role/display_name; explicit composition sets its chosen primary and supplementary roles.

Focused main/test compile and internal x86 app/test assembly PASS, 47 s. Materialization route encoding assertion added to OperatorNavigationInstrumentation. Both updated internal x86 test APKs installed successfully on existing emulator-5554. Runtime instrumentation repeated with a bounded 40-second wait; outcome recorded below. No live API mutation/inference/deploy occurred.

Materialization follow-up runtime instrumentation: PASS; INSTRUMENTATION_RESULT result=PASS and INSTRUMENTATION_CODE 0. This covers the offline UI/navigation/configuration/readable-preview/audio-isolation/route fixtures, not live server materialization or authenticated full pipeline execution. The final membership PUT schema adjustment is source-compiled separately.

Final compile after removing unsupported display_name from membership PUT: PASS, BUILD SUCCESSFUL in 32 s (main + instrumentation Java).

Final ARMv7 candidate build after cross-course integration: app/build/outputs/apk/debug/app-armeabi-v7a-debug.apk; version 0.5.7/code 24; minSdk 24 (Android 7+); debug-signed. SHA-256: 84d83481f3858457553c4b400af71d020f900613b91c4b787c8b549dde60cdef. Review copy: D:\C-drive-relief\APK-builds\Book2Course-operator-0.5.7-armv7-debug-2026-10-11.apk. Not release-signed or deployed to Drive. Emulator instrumentation passed on Android 8.1 x86; live materialization endpoint not called. Focused API/source regression run: 38 passed; git diff --check passed.
