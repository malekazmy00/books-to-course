# Book and course display-name repair — 2026-10-13

## Findings
Android's course rename sends PATCH through HttpURLConnection. The source API implements PATCH; no live request/error was available to establish the exact reported runtime failure. PUT is now an additional compatible route and Android uses PUT. The original PATCH route remains available.
The Android title input previously saved only local preferences; no book rename API or explicit durable-save button existed. This was a confirmed missing execution path.

## Implementation
- Added PUT course display-name updates with unchanged course_id and memberships.
- Added PUT book display-name updates, validating the selected persisted book.
- Display-name records are stored separately under _system/book-names, keyed by content SHA-256 (course/book hash fallback for legacy records). Original book uploads, artifacts, workflow identity and translation checkpoints are unchanged.
- Native book library, global library, course list and membership list apply persisted display names. The same source reused across courses keeps its shared library title.
- Internal _system namespace is excluded from course enumeration.
- Android adds a clear save-name button for an existing selected book, blocks unsaved/new upload renames, and updates the local selected title after server acceptance.

## Evidence and limits
Focused API checks: 3 passed, including rename persistence across two courses, preservation of live workflow/source reference/hash, unaffected other book, membership name readback, blank-name and missing-book rejection. git diff --check passed for edited files.
No production objects were written, no live workflow or service was changed, and no deployment occurred. Runtime reported course rename error still needs its exact HTTP response/log to distinguish an old image/route from client transport behavior.
