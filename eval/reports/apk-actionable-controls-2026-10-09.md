# APK actionable controls audit — 2026-10-09

Scope: B2C only; API27 Android8.1 x86_64 isolated emulator. Production workers/data/inference were not called or modified. Final ARMv7 APK remains unchanged at SHA256 cae05e756ad037682b9c339fd407973fe1d431b488fc98116f0c16a556493ed0.

## Method
The test-only AllControlsInstrumentation injects a FixtureActivity through Android Instrumentation.newActivity. It uses real MainActivity views, listeners, navigation, rendering, state and request-building logic. It overrides HTTP, provider keys, PDF-download source, file-picker/export intents, and DownloadManager so all server mutation controls are checked as recorded client contracts against fixtures. It uses a separate audit-fixtures preference file. No real API keys are read or injected.

Run:
./gradlew assembleDebugAndroidTest -Pb2cTestAbi=x86_64 -Pb2cTestRunner=com.book2course.operator.AllControlsInstrumentation --no-daemon
adb -e shell am instrument -w com.book2course.operator.test/com.book2course.operator.AllControlsInstrumentation
adb -e shell am instrument -w -e deep_only true com.book2course.operator.test/com.book2course.operator.AllControlsInstrumentation

The Gradle test runner can now be selected by a property; normal runner remains ButtonRegressionInstrumentation. No fixture code is included in the distributable app.

## Executed coverage
- Expanded full pass: PASS 345 recorded UI/fixture events; 46 visible enabled controls clicked, 8 controls guarded appropriately by completed/no-resume state.
- Additional deep pass: PASS 215 events. AndroidRuntime crash log clean.
- All five navigation screens and all five output choices: OCR, RAG, questions, translation, MP3.
- Library refresh/chooser; primary plus supplementary book confirmation; independent saved-book monitor choice and refresh; role/source language/output selection.
- PDF picker intent contract; local PDF renderer; next/previous page with persisted page position. OCR text preview, RAG chunks preview, question-by-lesson chooser, selected-stage preview/export, export document intents.
- Stage refresh, OCR/QA/index status, copy IDs, run details, synchronization; QA pause/resume and safe partial continuation; lesson batch chooser/all lessons; enrichment/review-assessment toggles.
- Body and appendix saved-unit previews; scoped correction; single-segment retranslation and candidate acceptance; manual candidate revision; original translation restoration.
- Partial body and appendix resume constructs resume_from_workflow_id and force_regenerate=false from saved request. Generic OCR/RAG/audio resume checks resume-capability before sending book_id to the selected workflow.
- Audio list refresh/download route, previous/next/toggle/stop button listeners and selection guards; local real MediaPlayer start, 2x speed, seek, pause, resume and stop using generated WAV fixture.
- Server URL save/HTTPS checks; model catalogue refresh, visible catalogue failure and recovery; tested filtering; all seven stage choices; all seven activation/default request contracts.
- Provider credential-dialog save uses a dummy nonsecret fixture value, clears input, sends fixture PUT, and verifies no phone preference contains it.

## Results and remaining capability
All exercised listeners and fixture contracts passed. No production-source fix was needed in this continuation; previous stale audio-list/source isolation fixes remain in the preserved final APK.

Current APK has QA pause controls. Generic OCR/RAG/audio controls only support terminal-state resume. updateStageResume currently hides PAUSED and COMPLETED-with-paused-result. It does not expose all-stage checkpoint pause yet; root was notified to coordinate the new backend control API and APK. This is an unimplemented capability, not a passing test.

Boundary: this is broad client/UI and outgoing-request coverage, not proof of live backend mutation success. Authenticated production PDF retrieval/audio streaming and provider invocation are parent integration checks. ARM binary execution is not covered by this x86_64 emulator. PDF pinch zoom gesture and external Android document-provider write completion are not claimed by these tests. Existing delivered APK and old files are preserved.

## Evidence
- eval/android/2026-10-09-source-lineage/all-controls-expanded.txt
- eval/android/2026-10-09-source-lineage/all-controls-deep.txt
- eval/android/2026-10-09-source-isolation-audio-smoke/
- eval/reports/apk-source-audio-isolation-2026-10-09.md

## Clicked controls
- CLICK 0 اختيار من المكتبة
- CLICK 0 إضافة PDF
- CLICK 0 متابعة كتاب محفوظ وحالته
- CLICK 0 ابدأ حتى الناتج المختار
- CLICK 1 فحص الاتصال
- CLICK 1 عرض النص المحفوظ
- CLICK 1 عرض الفهرسة المحفوظة
- CLICK 1 عرض الدروس والأسئلة المحفوظة
- CLICK 1 عرض مقاطع المتن وتصحيحها
- CLICK 1 مراجعة آخر إعادة ترجمة
- CLICK 1 عرض مقاطع الملحق وتصحيحها
- CLICK 1 مراجعة آخر إعادة ترجمة
- CLICK 1 عرض الملفات الصوتية المنجزة
- CLICK 1 تحديث الحالات
- CLICK 1 نسخ معرّفات التشغيل
- CLICK 1 إيقاف بعد حفظ الدرس
- CLICK 1 استئناف من آخر حفظ
- CLICK 1 استخدام المنجز ومتابعة الناتج المختار
- CLICK 1 إثراء الدروس: ملخص ومصطلحات وأسئلة مراجعة إضافية
- CLICK 1 اختبارات مراجعة: Midterm 1 وTerm 1 وMidterm 2 وTerm 2 وFinal
- CLICK 1 اختيار الدروس
- CLICK 1 كل الدروس
- CLICK 1 اعتماد المخطط / الفهرسة
- CLICK 1 حالة OCR
- CLICK 1 حالة الأسئلة
- CLICK 1 حالة الفهرسة
- CLICK 1 تفاصيل التشغيل
- CLICK 1 إعادة مزامنة
- CLICK 2 فتح PDF مع الزوم
- CLICK 2 عرض نص OCR
- CLICK 2 عرض ناتج المرحلة
- CLICK 2 تنزيل ناتج المرحلة
- CLICK 2 الأسئلة حسب الدرس
- CLICK 3 تحديث قائمة الصوت
- CLICK 3 تنزيل الملف المحدد
- CLICK 3 السابق
- CLICK 3 تشغيل / إيقاف
- CLICK 3 التالي
- CLICK 3 إيقاف الصوت
- CLICK 4 حفظ عنوان الخادم
- CLICK 4 فحص الاتصال بالخادم
- CLICK 4 تحديث النماذج
- CLICK 4 تجهيز وفحص
- CLICK 4 ربط أو تغيير مفتاح API للمزود المحدد
- CLICK 4 اعتماد للمهمات الجديدة
- CLICK 4 عرض تفاصيل التشغيل
