# Contextual terminology and Arabic editing — 2026-10-12

Source-only implementation; no service/workflow/data change or model inference. B2C only.

## Behavior
Translation requests accept an optional contextual_glossary snapshot with domain_id,
mappings, and verified/computed sha256. The API persists the snapshot in the original
request, includes its hash in workflow identity, and returns it in the validated resume
request. A resume cannot change the snapshot: use a new run for a new terminology policy.

The raw generator enforces mappings only where the explicit source phrase appears.
It rejects mismatching terminology into the existing retry/deferred/partial path.
A glossary for intelligence analysis can use intelligence => استخبارات; a separate AI
glossary can use intelligence => ذكاء. No global word-sense substitution is added.

Raw checkpoints include the glossary hash and editor metadata. Their reuse depends on
the glossary, source/model/language identity, numeric/structure/completeness checks;
a later editor change does not require repeating raw translation. Cross-run adoption
rejects different glossary hashes in both directions, including explicit vs absent.

The final immutable artifact exports translation-raw.json, translation.json,
translation-glossary.json, translation-arabic-editing.json, and source provenance.
Both raw and corrected result hashes plus editing/glossary identity distinguish output
artifacts. The optional SWEET/Qalb14 post-editor retains an accepted raw translation
when its candidate changes numbers, names, logical qualifications, terminology,
paragraph/table structure, completeness, or exceeds the edit budget. The previously
missing terminology_violations import is now implemented. Isolated retranslation
candidates also preserve raw text and editing audit.

## Checks
- 129 passed: tests/unit/translation, test_translation_partial_result.py,
  test_translation_partial_checkpoint_durability.py, test_hy_translation_route.py,
  test_translation_partial_status.py.
- Final post-change targeted run: 23 passed (new contextual/editing suite + HY routing).
- compileall succeeded for changed translation/orchestration/API sources.
- Five existing test_translation_checkpoint_selection.py tests still fail because
  their target language is ar while their purported complete target is long English
  prose. Diagnostic removed only the new glossary compatibility guard in-memory:
  the same five failed and two passed. No checker was weakened or test skipped.
- Models, downloads, quality scores, and live deployment were not tested by this task.

## Request example
{"contextual_glossary":{"domain_id":"intelligence-analysis",
"mappings":{"intelligence":"استخبارات"}}}

Domain/mappings are explicitly selected by the caller. This change does not implement
automatic book-domain classification or automatic term collection.

## Follow-up fixture correction and final validation

The five historical checkpoint-selection failures were invalid EN-to-AR fixtures:
their source/identity used target_language=ar but complete targets were long English
paragraphs, correctly rejected by the existing target-script guard. Adjusted only
their complete/wrong/short target fixtures into Arabic while preserving the temporal,
incompatibility, truncation, and number-corruption scenarios. Added a negative test
that a newer untranslated English checkpoint cannot displace valid saved Arabic.

Final command:
.venv/bin/pytest tests/unit/translation tests/unit/orchestration/test_translation_checkpoint_selection.py tests/unit/orchestration/test_translation_partial_result.py tests/unit/orchestration/test_translation_partial_checkpoint_durability.py tests/unit/api/test_hy_translation_route.py tests/unit/api/test_translation_partial_status.py -q

Result: 137 passed, 0 failed, 0 skipped, 9.46s.
No validator source was changed for this fixture correction; the earlier diagnostic
and failures above remain recorded as history. No runtime/services/data changed.


## D-only live deployment follow-up (2026-10-12 UTC)

The full B2C Compose set was validated and the D deployment is running. The project data disk is mounted read-write at /mnt/wsl/b2cdata (device /dev/sdh). API health and the HY model health endpoint both returned healthy; HY reports tencent/Hy-MT2-1.8B on CPU. The HY worker environment enables the Arabic Qalb14 editor and its model files and source module are present. Observability services that were down (collector, Langfuse worker, Temporal UI, cAdvisor) were brought up. No AI Company containers or C installation were changed.

Two earlier test translations used an incorrect global intelligence=>استخبارات mapping. They were stopped/ended, and their saved MinIO checkpoints were retained. Clean HY translation runs were started with no glossary:
- body: b2c-translate-acd3463d527d3bffbf7280cef0bde4adb0a832cbcba62eb6e4a4236ae5afa0d2 (2620 segments)
- course appendix: b2c-translate-2a041116176a753254098fb06230b0fec9313fbbd5b46005d6612c285d040614 (644 segments)

At 2026-10-12 18:11 UTC the body run was RUNNING, 14/2620 saved, 4 active, 0 failed. A saved-sample check found 0 suspect segments; raw and final Arabic samples matched (no visible editor change in the checked headings). The course workflow was RUNNING on the same HY task queue but had not written progress yet; the generation worker intentionally runs one activity at a time and the HY API also serializes inference behind a CPU lock. Do not increase concurrency without measuring memory and throughput. The audio sample is queued behind completion of both translation artifacts because the lineage validator correctly rejects partial translation artifacts; route for a one-lesson audio sample is documented by the audio audit agent.

Focused source validation reported 137 passed, 0 failed, 0 skipped (translation, checkpoint selection, partial durability, HY routing, and partial-status suites). This is not a phase-completion claim. Continue monitoring both workflows, verify the Arabic editor audit/cache on a substantive paragraph, then generate and compare one lesson/attachment audio pair after both required translation artifacts are complete.

