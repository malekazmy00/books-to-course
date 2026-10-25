# Book2Course learner app (Android)

Kotlin + Jetpack Compose + Media3 client for learners. Arabic and RTL first, and usable with TalkBack and large text. It talks only to the read-only learner API v3 (`/v3/learner`, contract `docs/api/learner-v3.openapi.json`).

- Design: `docs/proposals/2026-10-25-learner-app.md`.
- Evaluation of the old app: `docs/audits/2026-10-25-android-ux.md`.
- Operator functions stay in `android/operator-app` for now.

## Build

Requirements:

- JDK 17+ (21 works).
- Android SDK with `platforms;android-35` and `build-tools;35.0.0`.
- The Gradle wrapper (8.13).

```sh
cd android/learner-app
export ANDROID_HOME=/opt/android-sdk        # or write sdk.dir=... to local.properties (gitignored)
./gradlew :app:testDebugUnitTest            # JVM + Robolectric/Compose UI tests
./gradlew :app:assembleDebug                # app/build/outputs/apk/debug/app-debug.apk
B2C_API_BASE_URL=https://<host>.ts.net ./gradlew :app:assembleDebug   # optional preset server URL
```

The first build downloads the AndroidX, Kotlin and Media3 dependencies from Google Maven and Maven Central. The unit tests also download the Robolectric framework jar `android-all-instrumented:15-robolectric-12650502-i7` (about 200 MB). Gradle resolves it and Robolectric runs offline against `app/build/robolectric-deps`.

## Setup on the device

The API is tailnet-only (D-030). On first start, the app asks for the server's HTTPS address and the API token, and checks them with `GET /v3/learner/meta` before saving.

- The token is stored encrypted: AES-GCM, with a non-exportable AndroidKeyStore key.
- The token is sent only to that host.
- Debug builds also accept `http://10.0.2.2` and `http://localhost` for local API testing.

## Layout

| Path | Contents |
|---|---|
| `data/api` | Typed Retrofit client and models (mirrors the OpenAPI document; `OpenApiConformanceTest` checks it) |
| `data/LearnerRepository.kt` | Cache-first loading (`Loadable`), ETag revalidation |
| `data/local` | Learner state on the device: progress, bookmarks and notes, revealed answers, settings; plus the response cache |
| `data/download` | Offline episodes (WorkManager → app-private storage) |
| `playback` | `PlaybackService` (Media3 `MediaSessionService`), `MediaControllerConnection`, and `PlaybackCoordinator` (resume, progress, sleep timer, section skips, pause before answers) |
| `domain/Timeline.kt` | Section and segment navigation, spoken durations |
| `ui` | Compose screens: setup, library, course, player (transcript, sections, terms, questions, bookmarks), concepts, settings |
