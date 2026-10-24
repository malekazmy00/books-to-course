# Android test environment (Windows + WSL)

The production APK remains ARMv7 (`armeabi-v7a`) and Android 7+ (`minSdk 24`). The SDK Android 8.1 test image is x86_64, so its test-only APK must use matching native ABI; it tests the same Java/activity code, not ARM binary execution.

## Build and start

- SDK: `C:\Android\sdk`
- Isolated AVD: `B2C_QA_API27` (Android 8.1 / API 27, x86_64, 2 GB RAM).
- Existing ASJ AVDs and their userdata are preserved.

In WSL, build the emulator APK with:

```sh
cd /root/projects/book2course/android/operator-app
./gradlew assembleDebug -Pb2cTestAbi=x86_64 --no-daemon
```

In Windows PowerShell (process-only policy; leaves system policy unchanged):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& \\wsl.localhost\Ubuntu\root\projects\book2course\scripts\windows\start-b2c-android-emulator.ps1
& \\wsl.localhost\Ubuntu\root\projects\book2course\scripts\windows\test-b2c-android-smoke.ps1 -Apk \\wsl.localhost\Ubuntu\root\projects\book2course\android\operator-app\app\build\outputs\apk\debug\app-x86_64-debug.apk
```

The smoke script uses Android SDK ADB and on-device UIAutomator to save screenshots/UI hierarchy, checks APK installation/start, navigates the five screens, checks home/resume, dumps memory, and checks AndroidRuntime crash logs. `-Stress` optionally adds a reproducible 100-event Monkey run. Use stress only on this fresh isolated test AVD without a production access token, to avoid accidentally starting production work. The evidence goes to `%TEMP%\b2c-android-smoke` by default; choose `-Evidence` to keep a permanent record.

After testing, rebuild without the test property for the ARMv7 deliverable. Do not distribute `app-x86_64-debug.apk` as the user's 32-bit release.

Do not use `-wipe-data` on existing AVDs. This environment setup does not modify production services or processing tasks.
Windows ADB is isolated on port 5038 because WSL owns/forwards port 5037. The scripts set this for their process; existing WSL tools remain available. Rendering uses the guest software path (-gpu off, Vulkan disabled) to avoid a host graphics stall. Tests are serialized with a named mutex because Android 8 UIAutomator allows one automation connection at a time.
