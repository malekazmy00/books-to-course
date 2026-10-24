# APK interface proposal — 2026-10-08

## Deliverable

`apk-ui-proposal-2026-10-08.html` is a self-contained original Arabic RTL interaction proposal. It needs no external fonts, scripts, API keys or assets. Its example counters, books, downloads and connection behavior are explicitly simulated. It changes no APK, API, worker, model or live workflow.

## Current interface inspected

`android/operator-app/app/src/main/java/com/book2course/operator/MainActivity.java` builds a single ScrollView containing connection settings, internal identifiers, source controls, pipeline choices, per-stage commands, status cards and logs. That makes the saved book and its outputs hard to distinguish from configuration. The proposal groups user work into three destinations: Library, Workspace and Outputs. Advanced model/settings controls are collapsed; user choices remain available.

## Proposed behavior

- Library keeps uploaded books and completed stage outputs discoverable. Reopen a book without re-uploading it.
- Workspace shows detected format/language and compatible saved artifacts, then asks for output, language and whether a course with questions is wanted. The visible plan explains required, reused and omitted stages.
- Arabic source -> Arabic output omits translation. A native-text PDF extracts text without image OCR. Imported OCR text skips OCR only after validation. A compatible RAG bundle can reuse its index, sources and page provenance.
- Outputs keeps each stage/version/model independently downloadable. Lesson audio and its question/answer audio are separate tracks.
- Progress includes completed/total units, last successful save and exact waiting/paused/offline states. When phone connectivity is lost, the UI labels the displayed state as stale rather than claiming the server remains active. Server-side enforcement, real resumability and persisted version identity are prerequisites for implementation.
- Any external RAG package must provide validated source texts, page/provenance mapping and index configuration/version. Unknown embeddings alone are not sufficient. Changing models preserves prior outputs and requires a server-defined compatibility rule before reusing them.

## Source research and rationale

Sources consulted 2026-10-08. These support design patterns; they do not prove the backend features exist.

- [Material navigation bar](https://m3.material.io/components/navigation-bar/overview): stable primary destinations make Library/Workspace/Outputs reachable. Original colors and layout; no brand graphics copied.
- [Material progress indicators](https://m3.material.io/components/progress-indicators/overview), [Material Web progress](https://material-web.dev/components/progress/): show measured progress for the task at hand. Unknown progress must remain indeterminate.
- [Android accessibility](https://developer.android.com/guide/topics/ui/accessibility/apps): 48dp touch targets, readable contrast, labeled controls. HTML uses minimum 48px controls, visible focus and text states; APK implementation still needs dp, TalkBack and font scaling checks.
- [Google Play Books PDF/EPUB library](https://support.google.com/googleplay/answer/11012086?hl=en-GB): uploaded books belong to a reusable library rather than a temporary upload screen.
- [Spotify chapter metadata](https://developer.spotify.com/documentation/web-api/reference/get-a-chapter): chapter-level organization informs separate titled lesson and questions tracks. B2C downloads follow its own server artifacts.

## Verification performed

Opened in Codex browser at desktop width and at 390x844. Verified library navigation and book selection; Arabic-source example marks translation unnecessary; changing output language marks it required; visible counter/progress simulation, disconnect/reconnect and pause/resume worked. Browser error log was empty during these checks. Fixed bidirectional numeric counter rendering and mobile book-card spacing after preview. Screenshot saved as `apk-ui-proposal-mobile-2026-10-08.png`. Temporary preview server stopped after inspection. No device installation or backend E2E test is claimed.