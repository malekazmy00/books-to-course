# Android stable book identity — 2026-10-10

The operator APK now keeps PDF-derived public book IDs separate from immutable artifact IDs. A saved legacy selection such as book-main is resolved against the server library before fetching stage progress; ambiguous aliases require selecting the book instead of guessing. The resulting public ID remains the ID for library selection, source download and workflow resume. artifact_book_id is retained only as metadata and when building the supplementary_workflow_ids map expected by approved-outline contracts.

Progress cache entries now include course ID, public book ID and ingestion workflow ID. Global legacy progress fallbacks require the same course/book/ingestion context, preventing a saved result from another book being displayed during selection. Recovered library records without a workflow_id field can be selected and then reconciled with server progress.

## Checks executed
- BookIdentityRegression: PASS — PDF hash to stable ID, artifact alias retention, same-course/book cache isolation and legacy context rejection (9 assertions).
- Gradle assembleDebug --offline: BUILD SUCCESSFUL; compileDebugJavaWithJavac succeeded.
- aapt badging: versionName 0.5.7, versionCode 24, minSdk 24 (Android 7+).
- Deliverable: outputs/Book2Course-operator-v0.5.7-armv7-debug.apk, 116125 bytes.
- SHA256: afe7e1019066dc3645ec6514337cd0e4a6f7a0c69b76f18127be36ba37c29a9e.

No emulator/device test was performed; this is source/identity regression and build evidence only. The APK was not uploaded or published; parent requested backend review first. Older named APK copies were preserved.
