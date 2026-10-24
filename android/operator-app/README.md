# B2C Android experiment client

- Native Java client; Android 7.0+ (minSdk 24), compile API 35.
- ARMv7 32-bit-only APK, ABI armeabi-v7a; no 64-bit APK is generated.
- Emulator default API URL is http://10.0.2.2:8100. A physical device needs a reachable API address; current API binds loopback and has no operator authentication.
- The operator chooses an output target (OCR, RAG, questions, translation, or MP3), book vs course audio content, and output language. The client displays the prerequisite chain and runs connected OCR → RAG → question workflows in order.
- Translation and Fasih TTS/MP3 are not available as API workflows yet. Runs complete connected prerequisite stages, then report the exact missing API; they never report a translated or audio file as complete when none was produced. Course-only translation/audio also requires lesson generation separate from question generation, which the current backend does not expose yet.
- Build: JDK 17+, Android SDK 35, Build Tools 35.0.0, NDK 27.2.12479018, Gradle 8.13, AGP 8.13.2.

From this directory, run the Gradle wrapper task assembleDebug to build the debug APK. The debug APK is suitable for trusted internal installation and experimentation; a production release still needs authenticated API access, release signing, and a device test matrix.


## Partial lesson batches and QA recovery (0.4.2 candidate)

The operator can select approved lesson IDs before QA generation, inspect generated lesson text/summary/questions by lesson, request a safe pause after a persisted scope checkpoint, resume a paused QA workflow from compatible saved checkpoints, or continue the selected downstream output using the saved partial artifact. Pause/resume requires the matching API and generation worker deployment; do not install this candidate against an older API and expect those controls to work. It is prepared separately and is not live until the current generation work finishes and the B2C API/worker are updated.


## 0.4.10 connection and saved-stage recovery

Settings now has a persistent editable HTTPS origin. The built-in default remains the original Tailscale host on443. Only that explicit trusted hostname has automatic443/8443 alternatives; arbitrary configured hosts never gain inferred alternatives. Changing to a different host requires explicit confirmation and a new access token. Same-host changes preserve TokenVault and all workflow/checkpoint IDs.

Health preflight has4-second connect/read timeouts per candidate and selects the active endpoint for subsequent requests. GET may fall back once after a transport or502/503/504 failure. An ambiguous POST failure is surfaced without replay; the user must refresh status first. Authenticated requests and uploads do not follow redirects.

Translation completed-with-gaps exposes a partial label, true coverage and a resume button using the original request. Generic OCR/index/audio resume buttons appear only when the server's GET /v1/workflows/{workflow_id}/resume-capability allows them. POST resume supplies the selected book_id and updates the saved workflow ID. QA pause/resume/continue controls use its existing dedicated routes. These controls rely on compatible server endpoints; they do not claim page-level OCR checkpoints.

Preserved deliverable: outputs/Book2Course-operator-v0.4.10-armv7-2026-10-09.apk (versionCode16, minSDK24, onlyarmeabi-v7a). Existing APK versions remain available. Focused Android8 instrumentation and plain Java routing regressions passed; no paid model calls or production task mutations were used in those tests.
