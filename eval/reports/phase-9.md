# Phase 9 — serving preparation (2026-10-05 reboot recovery)

## Disposition
Preparation continues; Phase 9 release gate is **NOT PASSED**. The API and durable SRS primitives are implemented and focused checks passed. No RTL web client or Android APK exists yet, and no full released course with generated audio has been exercised end to end on a device. This report does not treat a successful build as acceptance.

## Runtime implementation traced
- `packages/api/src/b2c_api/app.py`: course list/detail, lesson detail, prerequisite review quiz, audio segments, MP3 byte Range, learner review submission/history and due SRS routes.
- `packages/api/src/b2c_api/serving.py`: discovers immutable `course-release` manifests, requires an approved outline, cleared book rights matching that outline, lesson version coverage, final course QA coverage, and in-course audio references with ACX flag and released voice match. Draft artifacts are not exposed by course discovery.
- `packages/api/src/b2c_api/srs.py`: PostgreSQL learner/course/question state and review history; transactional advisory locks and FSRS scheduling. Identity comes from an injected trusted authenticator. No unauthenticated header is accepted as identity; missing authenticator returns 503.
- MP3 streaming supports a single byte range, emits 206/Content-Range or 416 for invalid/unsatisfiable ranges, and closes its S3 body.

## Corrections after reboot
The existing PostgreSQL advisory lock name embedded NUL characters, which PostgreSQL text parameters reject. It now serializes the learner/course/question identity as unambiguous JSON without NUL bytes. Byte Range parsing now enforces ASCII digit grammar rather than accepting signed integers or internal spaces through Python `int()`.

## Executed evidence
| Check | Result | Evidence |
|---|---|---|
| Focused serving + existing upload API tests | PASS: 38 tests | `/root/.local/bin/uv run pytest tests/unit/api/test_serving.py tests/unit/api/test_book_upload.py -q` |
| Release artifact catalog | PASS within moto/S3 fixture | Roundtrip, drafts excluded, unsafe course path rejected, cross-course QA reference rejected |
| MP3 API | PASS within ASGI/S3 fixture | Inclusive byte range, suffix/open ranges, invalid/multiple ranges, 206 headers/body, 416 Content-Range, closed stream, missing lesson |
| Review quiz | PASS within ASGI fixture | Only prior lessons; answer/rationale/validation hidden; first lesson has empty prerequisite review |
| SRS API identity and scope | PASS within ASGI fixture | Spoofed user header ignored, trusted identity used, unavailable auth fails closed, unreleased course rejected, obsolete due question filtered |
| SRS lock encoding | PASS within connection fixture | PostgreSQL text contains no NUL; FSRS state/log/due produced |
| Unit and contract regression | PASS on repeat: 381 tests, no skips | `/root/.local/bin/uv run pytest tests/unit tests/contract -q` — 381 passed in 11.37s after Phase10 agent corrected the stale datasource UID assertion. Prior run: 380 passed/1 failed, preserved here as history. |
| Import boundaries | FAIL: 5 kept, 1 broken | Existing `b2c_pedagogy.lesson_enrichment -> b2c_synthesis.validation` direct dependency at line 25; unrelated to serving changes; reported to root |

No service startup, persistent disk mutation, paid/free inference, or AI Company inspection was performed by this agent.

## Remaining integration work
1. Implement production learner authentication/session lifecycle and configure it in runtime, then verify learner isolation against actual PostgreSQL. Existing dependency injection is deliberately fail closed and is not production authentication.
2. Connect the guarded release writer and audio-generation pipeline; the catalog expects immutable release artifacts but this change provides no publisher/writer. Current D-017 internal-test source rights cannot be substituted for cleared production rights.
3. Build the RTL learning client with text/audio synchronization, answer submission/feedback, SRS and accessibility, then a real Android package.
4. Run T-E2E-01..04 using an actual released course, audio, PostgreSQL/MinIO and target device, including restart/recovery, answer feedback and accessibility evidence. Mock fixture checks above are preparatory coverage and not these acceptance tests.

No changes were made to PROJECT-STATUS.md or 05-PHASES.md; root coordinates those files.

### Android app progress and one-button path — 2026-10-08
Delivered APK 0.2.0 with per-stage auto-refresh and recovery of server-known workflow IDs, addressing the old app's local-only “no question generation” guard. The one-button path executes required stages in dependency order and resumes from existing workflow IDs. Gradle build passed; package metadata confirms minSdk 24 and only armeabi-v7a. Drive upload was updated in place and read back. The APK embeds the active HTTPS Tailscale Funnel hostname, and the health endpoint returned ok; hash is recorded in docs/PROJECT-STATUS.md. Device installation/E2E remains unverified because no Android device or emulator was attached. Phase 9 stays open.


### Explicit library picker correction — 2026-10-08

- Current `MainActivity.showBookComposition` previously combined a platform AlertDialog message and multi-choice list. Android 7 `AlertController.setupContent` inserts the list only when no message is present; hidden selections could therefore apply the previously saved book.
- The corrected picker has a visible list, starts with no checked books, keeps invalid selections open, and requires exactly one stored `spine` plus any stored `supplementary` sources. A confirmation lists the primary book and all supplementary books before application. No book roles or backend contracts are changed. Extra stored `spine` books are rejected explicitly rather than silently omitted.
- Isolated copy build: `cd /tmp/b2c-library-picker-review-20261008 && ./gradlew --offline --no-daemon --max-workers=2 assembleDebug`: PASS, 38 tasks executed, 39 seconds. Existing in-repository APK output was not replaced.
- Review artifact: `C:/Users/User/Documents/Codex/2026-09-29/new-chat/outputs/Book2Course-library-picker-review-armv7-2026-10-08.apk`; SHA256 `d3a783d23809cabb895c78160c4a03d297819a7665c0924e818c6d3dc8282179`. SDK badging confirms minSdk 24, targetSdk 35, ARMv7 only; apksigner verify passes. This review build retains version 0.3.2/code 5 and is not a release or uploaded replacement.
- `adb devices` reports no attached device, and no WSL Android emulator executable is installed. Interactive/device behavior is not verified; build and Android platform-source evidence do not substitute for device acceptance.
- The library API remains course-scoped (`GET /v1/courses/{course_id}/books`); cross-course/global selection and assigning an uploaded primary book as a supplementary book in a different composition need a later contract change.
- No services restarted, workflows stopped, production deployment, or Drive upload performed. Phase 9 gate unchanged; root coordinates current status and decisions.

## 2026-10-08 — Android emulator environment and real runtime smoke

Environment installed: Google stable Windows Emulator 37.2.12 (`emulator-windows_x64-16428233.zip`, SHA1 `488ed747e82de7e9bb5247becd1ac043c7e5e85d`); Android 8.1 API27 default x86_64 system image revision1 (`x86_64-27_r01.zip`, SHA1 `2878261011a59ca3de29dc5b457a495fdb268d60`); Windows platform-tools37.0.1 (`platform-tools_r37.0.1-win.zip`, SHA1 `e03e78b1d80b396f1c3358e31251cb31740e1110`). Every download matched Google's repository checksum before extraction. WHPX10.0.19045 is installed and usable.

SDK at `C:\Android\sdk`. Existing platform-tools contained Linux binaries, so Windows .exe/.dll were added without removing Linux tools. Existing ASJ AVDs/data remain unchanged. Created isolated `B2C_QA_API27` with2GB RAM and2GB userdata. A host GLES startup stall is bypassed with guest software graphics (`-gpu off`, Vulkan disabled). Windows ADB uses5038 because5037 points to the existing WSL server; this avoids changing existing server state. Emulator is booted and left running, `sys.boot_completed=1`, release8.1.0.

First real runtime attempt FAILED on startup: duplicated attachment of TextViews (`label` already attaches to parent). Fixed four occurrences in the new results/audio/settings UI. Builds alone did not detect this defect. Earlier overlapping smoke invocations also caused an Android8 UIAutomator service collision; those attempts are not acceptance evidence. Added a named mutex to the reusable smoke runner and reran serially.

Final evidence: `eval/android/2026-10-08-api27-pass/`.

| Check | Result | Evidence |
|---|---|---|
| Installation of matching x86_64 test APK | PASS | smoke output / installed package |
| MainActivity launch | PASS | `launch.txt`, Status:ok, TotalTime2002ms |
| Five bottom navigation screens, exact expected-content assertions | PASS | library/stages/results/audio/settings XML and PNG |
| Home/background then Activity resume | PASS | `resume.txt` and `resume.xml/png` |
|100 seeded Monkey events on fresh unprivileged test app | PASS | `monkey.txt`,100 events injected |
| AndroidRuntime crash log after serialized test | PASS | `crashes.txt`, no FATAL EXCEPTION/UnsatisfiedLinkError |
| Default ARMv7 build restored | PASS | assembleDebug without test property,18s |
| ARMv7 artifact manifest/signature | PASS | version0.4.0/code6, minSdk24/target35, native-code armeabi-v7a, APK v2 signature verified |

Default ARMv7 artifact: `android/operator-app/app/build/outputs/apk/debug/app-armeabi-v7a-debug.apk`, SHA256 `d42c61f7dea4c55f02553d1c1c813984b4bafe1b08ca5c4435cc2688aa5d27f8`.

Tooling: `scripts/windows/start-b2c-android-emulator.ps1`, `scripts/windows/test-b2c-android-smoke.ps1`, and `android/operator-app/TEST-ENVIRONMENT.md`. The optional `-Pb2cTestAbi=x86_64` property builds the same Activity code for the emulator; the default remains32-bit ARMv7. These checks validate runtime UI/navigation/lifecycle robustness on Android8.1. They do not claim ARM CPU execution, Android7 runtime, authenticated production stage exports, provider switching, or full book-to-MP3 completion. No production worker/service/configuration or AI Company changes were made.

## QA partial pause/resume and per-lesson view candidate — 2026-10-08

Prepared, but **not deployed** while the current generation job remains active. QA can now persist a pause request and stop only after a completed QA or lesson-enrichment scope checkpoint. A paused result is stored as a separate partial artifact, exposed as PAUSED, and can resume in a new Temporal workflow from compatible checkpoint lineage. Checkpoint compatibility still requires the original model/config; the API rejects resume if the configured model changed. The checkpoint API now exposes saved per-lesson enrichment artifacts as well as question scopes. The Android candidate displays lesson draft sentences, summary and both generated question sets; it has controls to request pause, resume from saved checkpoints, or continue the selected downstream output from a paused partial result.

Evidence: focused API/orchestration/pedagogy tests **64 passed, 0 skipped**; Python compile and git diff --check pass. ARMv7 APK Book2Course-operator-v0.4.2-armv7-2026-10-08.apk builds (versionCode 8, minSdk 24). API 27 x86_64 emulator smoke passed install, launch, five-screen navigation and background/resume; that smoke does not call the API or exercise the new pause/resume workflow. The existing 0.4.1 APK and earlier versions were preserved.

Deployment remains pending: do not rebuild/recreate/restart the B2C API or generation worker until the live QA and translation workloads are confirmed complete. The pause/resume API is currently prepared for QA only; OCR, indexing, translation and audio do not yet have generic mid-stage pause/resume controls. The newer APK must not be used for its pause/resume buttons until the matching API and generation-worker images are deployed. This is a candidate feature checkpoint, not Phase 9 acceptance.

### OCR/index display recovery — 2026-10-08

The live book-main OCR and index status endpoints return 503 because their older Temporal histories are unavailable. A read-only check of the staged recovery implementation verified the exact registered ingestion through its committed approved-outline envelope: 677 pages and 776 text blocks, with the draft outline intact. The registered index identity matches the approved outline and default parameters; all 397 persisted PostgreSQL chunks match deterministic reconstruction from that same approved source. Legacy indexing has no immutable snapshot, so recovery explicitly describes the persisted index rather than claiming historical workflow execution evidence.

Source fixes recover extraction from approved source references, recover exact registered indexes from snapshots or fully matched legacy chunks, reuse status recovery for chunk display, and report recovered book-stage states. Terminal ingestion/index status no longer reads unrelated translation checkpoints. New index registrations persist parameters for future recovery. Python compilation passed; no tests or inference were run and no service/workflow was restarted. API deployment is required before the mobile display can use these changes.
