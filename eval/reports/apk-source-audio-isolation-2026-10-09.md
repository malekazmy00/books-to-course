# Android source and audio isolation — 2026-10-09
Scope: B2C Android client only. No live workflow/inference started, no containers restarted, no Drive upload performed by this agent.

## Changes
- Upload adopts canonical server-returned book ID (root change included).
- Server-filtered registry replaces every local stage reference; missing refs erase stale prior-book workflow refs.
- Planner translation action run and reuse both require translated input; skip remains optional.
- Selecting/uploading another book clears audio list, player and selected track. Changed registry audio reference also clears media state. An asynchronous audio manifest is discarded if the selected audio workflow changed while the request was pending.
- Focused pure helpers expose registry/translation policy behavior for meaningful on-device regression checks.

## Verification
- x86_64 assembleDebug + assembleDebugAndroidTest: PASS.
- API27 Android 8.1 instrument regression: PASS new-book cache isolation, old-book independent ref restoration, stale audio list reset, translation run/reuse/skip policy, safe read fallback and no mutation replay, partial translation resume, stage controls, tested model filter.
- Five-screen real emulator UI smoke: PASS install/launch/library/stages/results/audio/settings/background-resume; no AndroidRuntime crash. Evidence: eval/android/2026-10-09-source-isolation-audio-smoke.
- Plain Java EndpointRoutingRegression: PASS HTTPS validation, explicit port normalization, same-host alternatives, custom-host isolation, bounded fallback.
- ARMv7 final build and apksigner verification: PASS. minSdk24, targetSdk35, versionCode18/versionName0.5.1, native ABI armeabi-v7a only.
- An initial instrument attempt used newer test APK against previously installed stale app APK and failed NoSuchMethodError; reinstalling exact matched app/test artifacts resolved it. Final tests above use matched fresh artifacts.
- Test environment is x86_64 API27, so it validates shared Java/UI behavior but not ARM native binary execution. No claim that every production mutation button or authenticated saved artifact has been exercised end-to-end; production deployment/readback remains parent integration work.

## Preserved deliverable
outputs/Book2Course-operator-v0.5.1-source-audio-isolation-armv7-2026-10-09.apk
SHA256 cae05e756ad037682b9c339fd407973fe1d431b488fc98116f0c16a556493ed0
Earlier APKs are retained, including the preliminary source-isolation build; distribute this final source-audio-isolation artifact.
