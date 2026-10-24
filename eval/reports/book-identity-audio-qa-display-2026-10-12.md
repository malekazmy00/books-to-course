# Book identity, saved audio and QA display audit — 2026-10-12

Scope: B2C-On-D only. No container restarted, no workflow stopped, no source/data disk or unrelated project modified.

## Live evidence
- First PDF remains book-aaea5c4b55e6a7519bdea580, immutable parsed SHA 719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3, artifact book ID book-main.
- Authenticated book progress returned all stages; HY body workflow d90c6ff73b794f6e7cf40f8078fee4f0532d3a9d2f95f751a15387363dfa11fe RUNNING. Public identity has not changed.
- Audio workflow 57f9ded6d7b8eea8843b56866c13639d157b3bedcbb885e4301f3503e2f188a3 manifest returned HTTP 200, planned 135 tracks, current tracks empty.
- MinIO retains 14 independent track.mp3 objects. Twelve have exact first-book PDF/parsed identity; two old demo objects lack identity and cannot be associated automatically.
- Those twelve configs predate the new sentence-aware render policy. Existing bytes remain stored; they cannot be claimed as newly rendered current tracks.
- QA registry has persisted artifact f626ae40478741f2edcbab1c7b03781ad7da6842ad02dac183622f447bcaa81a despite old Temporal execution no longer available.

## Causes and source fixes
1. write_book_progress refreshed ingestion requested_at even when workflow did not change, causing false older-upload labels. Preserve original date for the same workflow; a new workflow receives a new date.
2. Android audio refresh used cached local audio ID before reading current book registry. It now resolves legacy identity, loads current registry, verifies selected course/book context and uses its workflow.
3. Stage audio button fetched list without opening its screen. It now opens audio screen.
4. Empty running manifests produced empty selector. Display waiting for first saved file and planned count.
5. Activity retry starts empty track list and replaces populated incremental manifest before gathering cache. Archive immutable previous manifest before any track-count regression; retain its pointer/count. Do not merge old render-policy audio into a new run or modify its stored bytes.
6. Terminal QA status incorrectly read translation-progress namespace. Read qa-progress and report scope counters.
7. Old QA execution retention yielded HTTP 503 while its exact source-bound artifact survived. Add explicit course/book recovery parameters; recover only matching registry workflow with validated artifact scope/stage. Report PARTIAL for incomplete QA with actual scope/question counters. Android QA refresh supplies recovery parameters.
8. Book-progress QA stage recovers status from verified artifact on Temporal NOT_FOUND and records the historical workflow as NOT_FOUND.

## Verification
- API identity regression initially four passed.
- Audio snapshot and existing audio-track suite eight passed.
- Android compileDebugJavaWithJavac BUILD SUCCESSFUL after all Android changes.
- Final combined suite result is reported to coordinating agent.

## Deployment
API rebuild required for timestamp and QA recovery changes. Audio-worker rebuild required for previous-manifest snapshots. New APK required for audio navigation/list recovery and QA recovery parameters. No deployment performed here. Java compile does not replace live emulator button exercise.

## Read-only historical saved-audio library
Implemented saved_audio.py router and two tests. Endpoint /v1/courses/{course_id}/books/{book_id}/saved-audio returns independently committed tracks only after exact PDF and parsed fingerprint validation. Download repeats validation and reads original bytes; no workflow progress or object is rewritten. MainActivity has explicit previous-output button and uses validated download_path for playback/download, keeping comparison outputs separate from current run.

Read-only execution of source helper against live MinIO returned exactly twelve: Preface lesson source and attachment, Introduction lessons 1–5 source and attachment. Their metadata is under b2c-demo/generate-mp3-track/{artifact_key}/ with _manifest.json, source-identity.json, track.json and track.mp3. Two demo tracks without source-identity.json were excluded. No completed historical course audio-manifest.json or earlier audio-progress-history object exists. Their configs are legacy render policy, so list labels state historical.

Additional verification: two focused saved-audio API tests passed; Android Java compile after historical-library UI succeeded in 22s. No rollout performed by auditor.
