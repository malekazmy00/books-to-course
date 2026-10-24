# Android button recovery — 2026-10-09

Scope: Android source only. No backend/data changes, translation POST, inference or workflows were triggered. Existing unrelated changes were preserved.

## Cause and fix

- The former “server update needed” label belonged to a disabled resume button, not a deploy/update action. A terminal response without `resume_request` now presents a clickable **check resume availability** action. It reads GET `/v1/translations/{workflow}` and explains the missing original request; it does not restart a server or translation.
- Safe resume with an exact saved request still POSTs `/v1/courses/{course}/translations` with `resume_from_workflow_id` and `force_regenerate=false`. The button remains blocked during an active in-app pipeline.
- Translation-unit preview now GETs `/v1/translations/{workflow}/units` before optional status retrieval. A missing historical status no longer prevents rendering saved units. When status cannot establish terminal state, editing stays disabled.
- Manual asynchronous actions now show start/completion notifications and an error dialog on the currently displayed screen instead of reporting errors only in a potentially hidden log. Missing translation IDs show an immediate explanation.
- GET JSON and PDF reads use a 60-second read timeout (previous JSON timeout was 30 minutes). Existing long-running POST/upload allowances are preserved.

## Verification

- `assembleDebug -Pb2cTestAbi=x86_64`: PASS.
- `assembleDebugAndroidTest -Pb2cTestAbi=x86_64`: PASS.
- Android 8.1/API27 emulator-5554 instrument regression: PASS for unavailable resume check enabled, ready resume label, and duplicate resume blocked while pipeline active. Test sends no HTTP/inference requests.
- Isolated Android smoke: PASS install/start/five-screen navigation/background-resume; no AndroidRuntime crash. Evidence: `eval/android/2026-10-09-api27-049-smoke`.
- Actual UI tap on body translation-preview button with no saved workflow: immediate visible “choose a saved book and refresh” dialog confirmed in `translation-preview-empty.xml`.
- ARMv7 `assembleDebug`: PASS; APK signing verification PASS; minSdk24/versionCode15/versionName0.4.9 confirmed. Test-only x86_64 artifact is not for distribution.

## Deliverable

`outputs/Book2Course-operator-v0.4.9-armv7-2026-10-09.apk`

SHA256: `215dc5eabfcdb2582bd477c855316c4de662421eae2e678b6e84d5462d676019`.

Authenticated live saved-unit rendering and actual resume remain backend integration checks; this audit did not start processing or claim those production actions completed.

## Model catalogue follow-up

Live authenticated GET `/v1/model-providers` returned HTTP200 and eight legacy records, all without `evaluation_status`. APK intentionally hides records without stored test evidence. This is a deployed API/source mismatch, not proof that the installed models vanished. Tested identities in the new source include RapidOCR, Surya2, Fasih, Gemma4E4B, BGE-M3, EmbeddingGemma2, LightRAG1.5.7, HippoRAG2; live UI names are not claimed before API deployment.

Android now distinguishes catalogue not loaded, legacy catalogue without test metadata, and genuinely absent tested options. Opening Settings loads the list automatically when a saved access token exists. The refresh action displays a visible error if GET fails. Test-only instrument fixtures confirmed untested records are excluded and tested records remain visible.

Expanded on-device regression PASS: six button/catalogue conditions, zero HTTP or inference requests. Evidence: `button-regression.txt`. An initial final smoke hit transient UIAutomator null-root/stale-dump at the audio transition, with no AndroidRuntime error; retry evidence is retained separately.

Final smoke retry PASS: install/start/five tabs/background-resume; API27/x86_64, no AndroidRuntime crash. Evidence: `eval/android/2026-10-09-api27-049-final-retry`.
