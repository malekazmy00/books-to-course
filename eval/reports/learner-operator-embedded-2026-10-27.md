# Learner APK 0.3.1 operator console integration - 2026-10-27

## Delivered

- Embedded operator-app 0.5.11 in learner-app as a non-exported Activity opened from Settings.
- The console exposes workflow/output controls, stage model selection and defaults, provider settings, library/course controls, per-book stage monitoring and actions (start, resume, pause, retry), plus result/audio export.
- The embedded console reuses the learner app's saved server origin and token only when the host matches. The token is not passed in an Intent or written to logs; otherwise the operator console uses its own encrypted token storage.
- Existing learner-stage actions in the working tree remain available, including guarded start/resume/pause and export.
- APK version 0.3.1, versionCode 8, minSdk 24; ARMv7 split and universal builds produced.

## Validation

- `ANDROID_HOME=/opt/android-sdk ANDROID_SDK_ROOT=/opt/android-sdk ./gradlew --offline :app:testDebugUnitTest :app:assembleRelease`: BUILD SUCCESSFUL in 3m45s.
- ARMv7 APK signature verified with v2. Package metadata: com.book2course.learner, versionCode 8, versionName 0.3.1, minSdk 24, targetSdk 35.
- ARMv7 APK: 2,866,825 bytes; SHA-256 d0a685fe4b3d662bbe49a3d42d2d2bdfdbbab523e7d0be16f8b0a4d11f344fe4.
- The previous 0.3.0 universal APK was installed and its learner settings, processing setup, and embedded console launch were verified on Android API 24. This 0.3.1 credential-sharing follow-up was compiled and packaged, but no emulator is attached to the current shell, so 0.3.1 itself was not reinstalled here.
- Drive upload and metadata verification succeeded for `Book2Course-learner-0.3.1-armv7-release.apk` (2,866,825 bytes).

## Runtime readiness

- API, Temporal, Postgres, MinIO, Gemma4 Ollama, Surya OCR, translation worker, and audio worker remained up; no worker or saved workflow was restarted.
- Fasih TTS, Supertonic TTS, and audio-renderer are healthy. Direct WAV smoke requests for Fasih and Supertonic and Arabic/English renderer requests returned HTTP 200. No course-audio workflow or saved course output was created by these checks.
- The configured local Qwen3-Coder service is now healthy. Its health endpoint returned HTTP 200 and a minimal 14-token local completion returned "OK". The cold CPU-only prompt took about 15 seconds for 11 prompt tokens, so it is available but slow; treat this as a connectivity smoke test, not a translation-quality benchmark.
- Gemma4 remains loaded in Ollama and Surya OCR remains healthy. These and Qwen3, plus the two TTS engines and renderer, cover the configured local OCR, generation, and speech services inspected here. Other RAG/embedding alternatives are selectable only if separately configured; they were not started or verified in this pass.
- Temporal's last checked state showed no active translation workflow; the only Running workflow record was a stale audio workflow started on 2026-10-19. This is not evidence that either book is making progress.
- Optional BookTrans generation-worker remains stopped because its persistent bind directory `/mnt/wsl/b2cdata/state/booktrans-work` is missing. It is not required by the configured B2C local pipeline.
- No AI Company service was touched.
