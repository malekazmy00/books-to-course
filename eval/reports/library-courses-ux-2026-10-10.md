# Global book library and course management — 2026-10-10

## Implemented source behavior

- GET /v1/library/books discovers course prefixes using S3 pagination, lists saved books globally, deduplicates exact PDF SHA across source courses, and supports case-insensitive title/name/filename search. A misspelled current course never hides existing books.
- POST /v1/courses creates a stable generated identifier; PATCH /v1/courses/{id} changes only the human name. Neither operation changes the source book or processing artifacts.
- PUT /v1/courses/{id}/memberships/{book_id} stores a validated original source-course reference, PDF fingerprint and per-course role. Promoting one book to spine demotes the other explicit spine membership to supplementary. Source course records remain untouched.
- GET courses and memberships include native uploaded books as well as explicit memberships, so new uploads appear without a secondary manual import.
- Android library screen hides raw book/course IDs and exposes course selection, new course, rename, all saved books, current course membership list, upload into selected course and the existing native-course composition selector.
- The all-books picker has title filtering, open-and-monitor and add-to-course actions. A membership from another source course is labeled as such; opening it uses its original processing context.
- Switching courses clears every active selection and workflow pointer before choosing a new book. Source artifacts and stored progress remain intact.

## Evidence

- pytest tests/unit/api/test_course_library.py tests/unit/api/test_book_upload.py: 31 passed.
- Final Gradle assembleDebug + assembleDebugAndroidTest: BUILD SUCCESSFUL after all library UI changes. aapt confirms version 0.5.7/code24 and minSdk24. Preserved candidate: outputs/Book2Course-operator-v0.5.7-library-candidate-armv7-2026-10-10.apk.
- Added LibraryCoursesInstrumentation offline regression for current-course-independent lookup and safe course-switch workflow clearing. Instrumentation build is prepared; not executed on a device.
- Android SDK/emulator executable and API27 x86 system image exist. At inspection adb listed no device; no AVD or /dev/kvm exists. A compilation result does not demonstrate device UI success.
- No live server restart, inference or upload to Drive performed by this task.

## Deployment and remaining integration requirement

The course metadata router must be deployed with the APK. Membership persistence alone does not materialize already processed book artifacts into a different course namespace. Existing QA/index/translation artifact guards enforce same-course references. Cross-source course generation therefore needs a separate validated resolver/materialization path before the UI can produce a new course from external-course saved outputs. The current open/monitor action deliberately reads the original source-book stages, while membership roles are saved under the target course. No state is reassigned and no production completion is claimed.

The coordinator was notified of this boundary and requested the implemented library/course membership controls with this limitation explicitly recorded.
