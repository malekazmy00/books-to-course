# Book lineage and audio isolation recovery — 2026-10-09

## Root cause

The Android client reused the legacy book-main identifier when a second PDF was uploaded. The API progress registry was keyed by course and book ID and retained stage entries from the earlier book. The client then read those stale outline, QA, and translation references as if they belonged to the new PDF. Audio validation checked the shared book ID but did not prove that the parsed PDF, approved outline, QA artifact, and translation all came from the same immutable source. This permitted an invalid audio workflow to mix the new PDF's title/front matter with old-book course content.

An audio workflow created on 2026-10-09 from this mixed set was terminated during the investigation. It is not being resumed.

## Fixes

- Upload now generates a content-derived canonical book ID on the server and uses it consistently in the library record and ingestion workflow.
- Progress resets metadata when a stage is assigned a different workflow ID. Read views and processing plans only expose downstream artifacts whose source parsed-book SHA, approved-outline SHA, and book identity match the current upload.
- Translation progress records its source and target language. QA progress records the matching parsed source and approved outline.
- The API checks the immutable source chain before queuing course/lesson audio; the audio worker repeats the check before rendering. A mismatch or missing source now fails closed.
- Android adopts the server-generated ID immediately after upload, clears stale audio selection/player state when the selected book or audio reference changes, and ignores late responses from the previously selected book. Translation reuse is treated as a required stage when selected.

## Validation and live state

- Focused backend tests: 78 passed across API identity/progress, audio lineage, and workflow resume (one upstream Starlette/httpx deprecation warning).
- Android 8.1 emulator: matched instrumented build and five-screen smoke passed; APK signed, minimum API 24, armeabi-v7a only. Expanded UI audit passed: 46 enabled controls clicked and 8 state-guarded controls correctly remained unavailable; deep pass and crash log passed.
- The API image was rebuilt and only b2c-api-1 was recreated. Health returned 200 afterward. The generation worker remained running and was not restarted. The generation-worker image is built with the lineage fix but not deployed; both the resumed translation and the second book ingestion are RUNNING, so the worker was deliberately left untouched.
- Old-book translation resumed from its saved workflow with DeepSeek deepseek/deepseek-v4-flash-0731: 763 saved units were reused. Latest check (2026-10-09 21:00 UTC): the resumed workflow b2c-translate-8b938c72dce0a2aef345a10f5c35e323f33703b608c315326ff6e703cea8a07c is RUNNING. Its merged checkpoints still show 764/776 saved, 12 retry units, 0 suspect units; DeepSeek is retained from the parent request. The new-book ingestion b2c-ingest-9f82296b6b3f1b0e5abae9b12cf02f56cf7745ef4ca200f43eaeb00a6fbf088b is also RUNNING.
- The second PDF was recovered from the existing library and re-uploaded through the server. It now has canonical ID book-70c88ae9b2ba9ed6f61e813c, ingestion workflow b2c-ingest-9f82296b6b3f1b0e5abae9b12cf02f56cf7745ef4ca200f43eaeb00a6fbf088b; its progress registry contains only that book's ingestion. The original PDF hash was verified before re-upload. Outline/RAG/QA/translation/audio must proceed from this new ingestion and its own artifacts after OCR completes.

## Release artifact

APK: outputs/Book2Course-operator-v0.5.1-source-audio-isolation-armv7-2026-10-09.apk  
SHA256: cae05e756ad037682b9c339fd407973fe1d431b488fc98116f0c16a556493ed0
Drive: https://drive.google.com/file/d/1nXhzO87Jng8pXXiGGbLFqhz-idkvZV08/view?usp=drivesdk

All previous APK artifacts remain preserved. The APK was uploaded as a new Drive file; no previous build was overwritten or deleted.