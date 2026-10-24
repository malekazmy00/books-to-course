# APK 0.5.4 launch crash investigation — 2026-10-10

A live Android 8.1/API 27 emulator reproduced the launch failure in the exact distributed ARMv7 0.5.4 APK (this version contains Java code only and no native library). Installation succeeded. Launch failed during MainActivity.onCreate before network or processing actions.

AndroidRuntime reports `IllegalStateException: The specified child already has a parent` at MainActivity.buildStagesScreen, line 43. The field stageBookLabel is created with label(), which immediately calls box.addView(). buildStagesScreen then calls box.addView(stageBookLabel, ...) again. Android rejects the second insertion.

Required fix: create stageBookLabel with text() instead of label() and retain its explicit addView with margins, or remove the second addView. Static search across all label-created variables found no other explicit addView duplicates.

Evidence: eval/android/apk-0.5.4-launch-crash-api27-2026-10-10.log. Earlier 0.4.9 launch evidence at eval/android/2026-10-09-api27-049-final/launch.txt shows Status: ok and screenshots from library, stages, results, audio. Installing the older ARM native APK directly on the x86_64 emulator was rejected with INSTALL_FAILED_NO_MATCHING_ABIS; this expected ABI issue is separate from the Java launch defect proven in 0.5.4.

No application source was changed and no new APK was built during this investigation. The fix needs a fresh build followed by installation, launch, five-tab navigation, background/resume, and AndroidRuntime log inspection before distribution. The B2C processing workers and AI Company were untouched.

## Corrective build 0.5.5

Changed stageBookLabel creation from label() to text(), leaving one explicit insertion into the stages container. A scan of every variable initialized by label() found no remaining explicit addView duplicate. App versionCode22/versionName0.5.5 built successfully with assembleDebug.

The exact deliverable installed and launched on API27. The existing repository smoke script verified all five tabs and background/resume, and found no AndroidRuntime exception. Evidence and screenshots: eval/android/2026-10-10-api27-055-launch-fix/. This verifies startup and basic navigation, not all production operations or server workflows.

APK: outputs/Book2Course-operator-v0.5.5-armv7-launch-fix-2026-10-10.apk. SHA256105401ed4eb1659dd68f14cd45e5277f0d94b204170f68258fcc0e2ff5e4c0cc. Android7+ (minSdk24); APK contains no native code, uses the existing ARMv7 split configuration, and retains signer certificate SHA256eb96763367670d9e7848638b5fafa11688200dd0fd898252179a7200f77128cd. Previous APKs retained. No worker or production service change.
