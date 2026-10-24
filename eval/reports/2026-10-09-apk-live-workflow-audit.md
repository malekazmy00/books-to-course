# APK and live workflow audit — 2026-10-09

## Safe test results

- Built the operator app for the isolated Android 8.1/API 27 emulator (`x86_64`) and for the requested 32-bit `armeabi-v7a` ABI. Both builds succeeded; the app remains minSdk 24 / Android 7+.
- Emulator smoke passed: install, launch, all five navigation tabs, background/resume, and no Android runtime crash.
- Android instrumentation passed its offline regression checks for mutation retry safety, safe GET fallback, partial translation recovery, resume controls, and tested-only model filtering. No inference or processing request was made by the instrumentation.
- Read-only server checks returned HTTP 200 for the book catalog, per-book progress, and model catalog. The catalog currently returns one visible item; the model catalog returns ten providers.
- The emulator had no saved API token. Opening the new “متابعة كتاب محفوظ وحالته” action reached the expected secure-token prompt; authenticated monitor UI was therefore not exercised on-device. The server GET routes were checked independently with the server-mounted credential, without printing it.

## Issues found

1. A newly uploaded PDF and the earlier PDF both used `book-main` and the title `كتاب التجربة`, despite different PDF digests. The new ingest is saved and OCR is `COMPLETED`; it appears under the same title rather than as a distinct-looking library entry. The library fallback deduplicates uploads by `book_id` alone, hiding the older upload. Progress is also keyed by course/book ID, so an ingest from one PDF can coexist with QA/index/translation stages from another.
2. At 19:12:40 UTC, book-body translation ended `COMPLETED` but partial: 763/776 valid saved (98.3%), 12 saved checkpoints fail current completeness checks, and 1 segment was not saved. All 13 need retry; no translation/QA workflow was running at the 19:33 UTC check. The 13 affected pages are 81, 119, 120, 121, 169, 353, 426, 465, 467, 560, 580, 647, and 673: 11 numeric-token/placeholder issues, one table-cell sequence mismatch, and one severe content omission. Page 353 was not saved; the other 12 were saved but suspect. The appendix remains complete at 888/888.
3. A new MP3 workflow appeared at 19:34:47 UTC during this audit; the audit agents and root made no POST, inference, start, stop, or restart request. Its input combines QA/outline artifacts from the earlier book with a newer PDF ingest under the reused `book-main`; `source_book_translation_ref` is null although the requested audio language is Arabic. At 19:39:36 UTC it was still RUNNING with one completed track and no reported failure. It was left untouched. The current audio route checks matching book IDs but not matching source artifact revisions.
4. Source inspection confirms automatic continuation depends on the selected output: choosing “translation” ends after translation; choosing MP3 runs required translation before audio. The translation resume button alone does not start audio. The path currently treats a `COMPLETED` partial translation as terminal and can continue to audio with that partial artifact; a completeness gate/retry loop is needed before considering it safe.
5. APK button audit found further gaps: long processing waits share one executor with library/model/status refresh; completed-audio button refreshes a hidden list without opening it; switching books can leave prior QA history/audio entries visible; Android 7/8 MP3 download permission handling is missing. These were not exercised against the live worker because that would mutate current processing.

## Source changes and limits

- The local APK source now offers a direct per-book monitor entry from the library, displays outline and RAG/index statuses separately, and filters model choices to `tested` or `limited_test`. The instrumentation test was aligned with the already-documented tested-only behavior.
- The changes compiled and the offline instrumentation plus API 27 smoke passed. The app has not been deployed or uploaded; APK metadata is still version 0.5.1 / code 18. This audit does not certify every mutating button or the live audio output.
- Do not restart/deploy the API or stop/retry the current MP3 workflow while it is running. Next safe fixes: distinguish library entries by content digest and upload time, scope progress to ingest/source identity, verify QA/outline/book source hashes before audio, and block/retry incomplete translations before audio.
