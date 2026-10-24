# Android library and stage controls audit — 2026-10-10

## Verified causes

- MainActivity.refreshStatusesBlocking restores state from the server's book progress registry and applyStageRegistry deliberately replaces every stage reference. A historical book with only ingest recorded therefore shows only OCR. Keeping old local IDs would risk mixing books; the server must recover the registry from verified workflow inputs.
- Stage cards had no permanent start action. Only terminal OCR/index/audio workflows with a positive resume capability revealed a resume button. A never-started stage had no action on its card.
- The global translation card used the appendix translation ID only. A book with book_translation but no translation appeared not started even though the body track was saved.
- Library controls displayed compound display_name and carried generic trial titles into new PDF uploads.

## Changes

- Permanent start/continue button on each of five stage cards; delegates to the existing processing dependency path. Earlier required stages are retained. Terminal ingest/index/audio use the server's owner-validated resume API with immutable inputs and saved work.
- Body-only translation now populates the global translation card.
- Translation stage reuses existing completed workflows and checkpoint-resumes failed/partial workflows; it runs both body and appendix for course output. It does not force regenerate saved units. Completed-but-partial translation cannot silently pass to audio.
- Existing audio workflow is reused or resumed rather than starting another inferred audio job on every click.
- Library primary labels use natural title, falling back to PDF filename for generic trial names. New PDF defaults to provider filename. Duplicate natural titles receive filename and a short ID on the secondary line. Book IDs remain identity keys.

## Evidence

- ARMv7 debug + Android instrumentation build passed before final helper changes; x86_64 build and instrumentation build passed after final changes.
- Android 8.1/API27 isolated emulator, Windows ADB port 5038: ButtonRegressionInstrumentation returned INSTRUMENTATION_CODE 0.
- Tests assert permanent buttons on all five cards, natural-name fallback, duplicate-title identity, body-only translation, complete translation reuse, failed translation checkpoint resume, cross-book registry replacement and stale audio removal, and existing networking/model/resume regressions.
- Offline request doubles were used; no processing or inference job was started on the live server. No API/translation service source files or other projects were changed.

## Deployment requirements and limits

- Server registry recovery and actual book title correction remain API work; this client does not guess historical stage ownership.
- Do not leave the deliberately terminated incorrect full_book audio workflow as the current registry audio entry: generic resume preserves the original input. Link the intended lesson workflow before offering resume.
- These are focused regression controls, not a claim that every network-backed button and full production pipeline was executed on the emulator.

## Context isolation follow-up

- Pipeline execution now locks course/book identity, composition, stage settings and start actions until its local orchestration completes. Selecting another book, composing sources, or receiving a delayed PDF picker result cannot mutate the processing refs during that interval. Additional generic queued actions are rejected with an explicit message rather than being unexpectedly executed after completion.
- A dedicated monitor executor loads the book picker and read-only per-book progress independently of the long-lived pipeline executor. While processing is active, the picker opens the selected book's read-only monitor rather than selecting it as the processing source. The monitor never calls applyStageRegistry and therefore cannot overwrite processing IDs.
- The code retains the safe lock alternative rather than a full immutable pipeline-object refactor. Controls unlock when client orchestration completes, and server workflows continue independently.
- Focused instrumentation additionally checks that processing context/start inputs lock, selectBook and applyComposition cannot change protected IDs while busy, read-only monitor has an independent executor, and inputs unlock afterwards.

- QA pause remains available while the pipeline runs: the request captures its workflow ID before dispatch on the independent executor, sends the pause signal without modifying refs, and does not wait behind the processing loop. Offline regression verifies the request still targets the original ID after the test changes the global QA field.

## Pause and HY translation audit

- Current source API provides cooperative pause only for QA: POST /v1/course-qa/{workflow_id}/pause and resume. Ingest/index/audio generic resume only supports failed terminal workflows. Translation resumes failed/partial requests, but there is no cooperative pause endpoint for OCR, indexing, translation or audio. The APK therefore shows working QA pause/resume and owner-validated terminal resume controls; it does not advertise fictional pause endpoints.
- Required backend contract for additional stages: POST /v1/workflows/{workflow_id}/pause with an owner-validated pause-after-checkpoint signal/control record; resume-capability must explicitly expose pause availability, workflow states must return PAUSED, and POST /v1/workflows/{workflow_id}/resume must recover those checkpoints with immutable validated inputs. Translation must preserve its existing segment checkpoints.
- model_lifecycle.py currently defines hy-mt2-1-8b as unsupported/not_tested. TranslationRequest forbids a provider field, and resume pins the old provider. Backend must expose tested, connected HY on its queue and accept optional provider_id at translation start/resume; overriding provider must keep prior checkpoint ancestry.
- APK now remembers only server-confirmed translation defaults (catalog.defaults.translation or provider.default_for_stages). It includes provider_id in new and resumed translation requests, including the explicit retry buttons. A 202 default-change acceptance alone does not pin a failed/unconfirmed choice locally.
- Instrumentation on API27 returned CODE 0 with a connected tested HY catalogue fixture, confirming HY selection in translation settings, correct provider_id on a new request, correct provider_id plus resume_from_workflow_id and force_regenerate=false on resume, and supported QA pause/resume controls. This is client-contract evidence, not proof of the unconnected production HY adapter.
