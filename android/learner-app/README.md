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
./gradlew :app:testDebugUnitTest            # JVM + Robolectric/Compose UI tests (API 24, 25 and 35)
./gradlew :app:lintDebug                    # must report no errors (NewApi included)
./gradlew :app:assembleDebug                # app/build/outputs/apk/debug/app-<abi>-debug.apk
./gradlew :app:assembleRelease              # minified (R8); signed if a signing file exists, see below
B2C_API_BASE_URL=https://<host>.ts.net ./gradlew :app:assembleDebug   # optional preset server URL
```

The first build downloads the AndroidX, Kotlin and Media3 dependencies from Google Maven and Maven Central. The unit tests also download the Robolectric framework jars `android-all-instrumented` for API 35 (`15-robolectric-12650502-i7`), API 24 (`7.0.0_r1-robolectric-r1-i7`) and API 25 (`7.1.0_r7-robolectric-r1-i7`), one Gradle configuration each. Robolectric runs offline against `app/build/robolectric-deps`.

On the shared server, build with `nice -n 15 ./gradlew ...`.

### APKs per CPU (ABI splits)

Every build makes three APKs: `armeabi-v7a` (32-bit ARM, typical for cheap Android 7 phones), `arm64-v8a`, and `universal` (all ABIs). The only native library is Compose's `libandroidx.graphics.path.so`. All three have the same `versionCode`; they are for side-loading, not for one store listing.

### Release signing

The key stays outside the repository. Gradle reads a properties file with `storeFile`, `storePassword`, `keyAlias` and `keyPassword`:

- default path: `~/.config/book2course/learner-signing.properties`;
- another path: environment variable `B2C_SIGNING_PROPERTIES`.

Without the file, `assembleRelease` makes unsigned APKs. On this server, the file points to a **development** key (`learner-dev-release.jks`, alias `learner-dev`, certificate SHA-256 `7176d6cffd8d6d46f0a7ae6e653e966828f4c1bbd0082c701266ef715f044106`). An APK signed with another key cannot update an installed one: uninstall first.

Check an APK:

```sh
$ANDROID_HOME/build-tools/35.0.0/aapt2 dump badging app-armeabi-v7a-release.apk | grep -E 'minSdkVersion|native-code'
$ANDROID_HOME/build-tools/35.0.0/apksigner verify --print-certs app-armeabi-v7a-release.apk
```

## Setup on the device

The API is tailnet-only (D-030). On first start, the app asks for the server's HTTPS address and the API token, and checks them with `GET /v3/learner/meta` before saving.

- If the check fails, the message names the cause (`data/api/ConnectionProblem.kt`, tests `ConnectionProblemTest` and `SetupErrorsTest` on API 24 and 35):

  | Failure | Message (start) |
  |---|---|
  | Name does not resolve (`UnknownHostException`) | «تطبيق Tailscale غير مفعّل أو غير متصل على هذا الجهاز…» |
  | Timeout, refused, no route | «تعذّر الوصول إلى الخادم أو انتهت مهلة الاتصال…» |
  | TLS (`SSLHandshakeException`, `CertPathValidatorException`, `SSLPeerUnverifiedException`) | «تعذّر إنشاء اتصال آمن…» |
  | 401 / 403 | «الرمز غير صحيح…» |
  | `min_client_version` of `/v3/learner/meta` above the app's `CLIENT_VERSION` (or HTTP 426) | «إصدار التطبيق قديم…» |
  | 5xx | «حدث خطأ في الخادم…» |

  The button «نسخ تفاصيل الخطأ» copies the exception types and messages. The token and any bearer value are replaced by `[redacted]`.
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

## Android 7 (API 24 / 25)

`minSdk` is 24. Checked on 2026-10-25 (tests: `compat/OldAndroidTest`, `compat/TrustAnchorsTest`, and `ui/AccessibleScreensTest` on API 24, 25 and 35):

| Topic | Status on Android 7 |
|---|---|
| TLS to Tailscale Serve | Fixed: the app trusts ISRG Root X1 and X2 itself (below) |
| Notification channels (API 26+) | Media3 creates its channel only on API 26+; the media notification is posted on 24/25 (test) |
| `foregroundServiceType` (API 29+), `FOREGROUND_SERVICE_MEDIA_PLAYBACK` (34+), `POST_NOTIFICATIONS` (33+) | Ignored by older systems; Media3 passes the type to `startForeground` only on API 29+. The app never requests notification permission at runtime |
| `java.time` | Not used by the app; no core library desugaring needed. Lint `NewApi` would flag new uses |
| Token encryption | AES-GCM in AndroidKeyStore works from API 23. If the key is lost, the app asks for the token again |
| Downloads | `filesDir` (app-private, no storage permission) and WorkManager (JobScheduler on 23+) (test) |
| Arabic text and RTL | System Arabic font (Noto Naskh). Compose forces RTL whatever the system language (test). The type scale has no letter spacing: older Android versions also apply tracking to Arabic letters and break their joining |
| Launcher icon | PNG in `mipmap-*dpi` for API 24/25; adaptive icon in `mipmap-anydpi-v26` (test) |
| Lint | `./gradlew :app:lintDebug :app:lintRelease`: no errors and no `NewApi` |

### Trust anchors (Let's Encrypt)

Tailscale Serve uses a Let's Encrypt certificate. On 2026-10-25 it sent this chain: `leaf ← YE1 ← Root YE ← ISRG Root X2 (cross-signed by X1)`. Android 7.0 trusts neither root:

- ISRG Root X1 is in the Android store only from 7.1.1, and X2 only from Android 14 (letsencrypt.org, "Certificate Compatibility").
- Older Android validated through the cross-sign of X1 by DST Root CA X3. That cross-sign expired on **2024-09-30** (letsencrypt.org, "Shortening the Let's Encrypt Chain of Trust", 2023-07-10).

So `res/xml/network_security_config.xml` (release and debug) keeps the system store and adds the two roots as trust anchors:

| File | Certificate | SHA-256 |
|---|---|---|
| `res/raw/isrg_root_x1.pem` | ISRG Root X1, self-signed, valid to 2035-06-04 | `96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6` |
| `res/raw/isrg_root_x2.pem` | ISRG Root X2, self-signed, valid to 2040-09-17 | `69:72:9B:8E:15:A8:6E:FC:17:7A:57:AF:B7:17:1D:FC:64:AD:D2:8C:2F:CA:8C:F1:50:7E:34:45:3C:CB:14:70` |

Where the files and fingerprints come from:

- Files: the source of letsencrypt.org/certificates (`github.com/letsencrypt/website`, `static/certs/isrgrootx1.pem` and `isrg-root-x2.pem`). letsencrypt.org itself was not reachable from this server.
- Fingerprints: the same as Mozilla's CCADB report (`IncludedCACertificateReportCSVFormat`), Debian's `/etc/ssl/certs`, and, for X1, the copy at `http://x1.i.lencr.org/`.
- `TrustAnchorsTest` checks the fingerprints and validates the saved Tailscale chain against each root. It also checks that the chain fails on a store without ISRG roots, as on Android 7.0.

The new roots Root YE and YR are not bundled: the default chains end in X1 or X2.

### Low-memory phones

- No `largeHeap`: the app loads no images (icons are vectors), so it has no bitmaps to hold.
- Long lists are `LazyColumn` (library, episodes, transcript, concepts, questions).
- Downloads are streamed to disk in 64 KB blocks.
- One episode's JSON (transcript and sections) is held in memory at a time.

No measurement on a real device was made.
