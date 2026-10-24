# Phase 7 — Translation and quality estimation

Date: 2026-10-05  
Status: **NO-GO for phase closure.** A bounded free and paid-provider benchmark is now recorded, but the project translation activity, approved termbase, calibrated QE, and persistent artifact/review path have not passed their gates.

## Scope
The project plan defines Phase 7 as translation, approved termbase construction (G3), chunked translation with approved term injection, deterministic and model-based quality estimation, uncertainty routing, review workflow, and G4. Owner direction defers O-1 human review to experimentation; it is not a prerequisite to starting generation. G4 still requires validated translation artifacts and quality gates.

## Implemented in this checkpoint
- Added deterministic paragraph/sentence chunk packing targeting 500 words, with safe word-boundary splitting for oversized unpunctuated text.
- Added a termbase compiler that only attaches caller-supplied Arabic mappings to existing glossary entries. Missing, duplicate, or extra mappings fail closed; it does not invent translations or approve drafts.
- Added a structural G3 check requiring unique source terms, nonempty mappings, and an approval identity.
- Added prompt-time injection of only approved terms present in a source chunk; injection fails closed unless G3 passes.
- Added deterministic checks for missing approved glossary terms, forbidden variants, and numeric-token changes.
- Added a conservative item-level G4 predicate. No uncalibrated model output is auto-accepted.

## Benchmark and local model evidence
- Downloaded the official FLORES-200 devtest archive to the B2C data disk at `/mnt/wsl/b2cdata/eval-datasets/translation/flores200/flores200_dataset.tar`. The official corpus is CC-BY-SA 4.0; this is a research evaluation dataset, not project content. The `eng_Latn` and `arb_Arab` devtest files each contain 1,012 aligned rows. Archive SHA-256: `b8b0b76783024b85797e5cc75064eb83fc5288b41e9654dabc7be6ae944011f6`; English SHA-256: `612e9fbe87997617c0fa8fa8929654a4f49b728d96738112c2b86ef6a1d78d88`; Arabic SHA-256: `ff09a52e63884e9b5bb1083780b58f37d3d14acb0c58c6b1f1f2544ecea12ff8`.
- Ran the locally stored Hunyuan Hy-MT2 1.8B model on three evenly spaced FLORES rows, saving each partial output. CPU run took 276.54 seconds total (92.18 seconds per sentence). All three outputs were truncated by the 90-second per-sentence time limit. BLEU 0.701 and chrF++ 14.734 are therefore **invalid as quality estimates** and are retained only to document the failed run; numeric token match was 2/3. No longer CPU inference loop will be repeated for this candidate on the current host.
- OpenRouter was rechecked before each request; the Qwen model was listed with zero input/output price, and the B2C daily request guard was 40/day with six-second minimum spacing. The worker's default provider/model was not changed.
- Qwen free (`qwen/qwen3.8-27b:free`) was tried on three evenly spaced FLORES rows after disabling reasoning. One initial request at `max_tokens=1024` was truncated by the gateway; the gateway discarded its body, so no usage or quality score is claimed. A single bounded retry with `max_tokens=4096` completed in 2.619 s using 134 prompt + 98 completion tokens (232 total, provider cost $0). The 3-row diagnostic score was chrF++ 39.562, BLEU 7.718, and exact numeric-digit preservation 2/3. A separately bounded 30-row free Qwen request returned HTTP 429 in 0.51 s; it was not retried and no fallback occurred. The daily ledger ended at 7/40 reservations, including four existing reservations and these three requests.
- After the Qwen 429, one explicitly authorized DeepSeek V4 Flash request (`deepseek/deepseek-v4-flash-0731`) was sent as a paid diagnostic, not as a runtime fallback. It sampled 30 evenly spaced FLORES rows, completed in 34.529 s, and returned 848 prompt + 1,196 completion tokens (2,044 total) with provider-reported cost **$0.000518432**; the conservative preflight reserve was **$0.00530254**, below the $0.01 per-request and $0.05 Phase 7 limits. Finish reason was `stop`; all 30 rows parsed and preserved numeric digit sequences exactly. Scores were chrF++ **56.355** and BLEU **29.460**. These are single-reference diagnostic scores on a small sample, not a model ranking or release threshold.
- Reproducible offline audit `eval/suites/phase7/audit_translation_invariants.py` reran the project's `deterministic_translation_check` on all 30 saved DeepSeek outputs: 30/30 pass, zero violations, all route to `human_review`. The private data-disk result is `deepseek-30.qe.json`, SHA-256 `dacc2d9620bc24686d8b8f4c4c3d99acbb5940dd60d5d1ea1c25f5dfce348e0b`, mode `0600`. No provider setting, worker image, or permanent model default was changed.
- Frozen FLORES source/reference hashes are listed above. Other data-disk outputs are mode `0600`: `qwen-free-3.raw.json` SHA-256 `3c206649707659965285b4cb898e47c5a50fb59100c8d123df7da8cc20350e49`; `qwen-free-3.scores.json` `55a24893589401cadf9744be1036c811ca78291687d85d4cf0fb8cb550a6aafc`; `qwen-free-30.attempt.json` `4d7f3b8d718e4263cbf85f8d95a820b8f0a633ed15c37bf9ac92ecaff2323d23`; `deepseek-30.raw.json` `e164d8c381f9f189e5a60824a2920275be3ad2a61988c023c99670ef93f585be`; `deepseek-30.scores.json` `ddea8e70db055aa87696a39fcd2c7e7b6793ede13acc9498b28de2431428eb72`.

## Tests
- `pytest tests/unit/translation/test_pipeline.py -q`: **8 passed**.
- `.venv/bin/python -m compileall -q eval/suites/phase7`: **passed**.
- `git diff --check` on Phase 7 report, runners, translation pipeline, and focused tests: **passed**.

## Gate decision
- **G3: NOT PASSED.** No approved bilingual termbase was supplied; only its fail-closed structural and injection rules are implemented.
- **T-TR-01: NOT PASSED.** No approved glossary/translation pair is available for a coverage audit.
- **T-TR-02: PARTIAL.** Numeric and glossary invariants have unit coverage; citation/identifier and equation preservation are not connected to a real TranslationUnit path.
- **T-TR-03: NOT PASSED.** No 30 human-post-edited reference pairs were supplied; the FLORES sample is a diagnostic automatic metric only.
- **T-TR-06: PARTIAL.** Actual provider outputs and BLEU/chrF++ scores exist, but the sample is too small and single-reference, and no calibrated QE comparison exists. The Hy-MT2 sample remains truncated and cannot support a quality claim.
- **T-TR-04/T-TR-05: PARTIAL.** Conservative routing predicates have unit coverage, but no persisted review queue or measured routing ratio is connected.
- **G4: NOT PASSED.** No generated translation units, calibrated QE, or runtime artifact integration has passed.

## Minimum remaining gates
The licensed candidate snapshot now exists on the B2C data disk; implement an offline candidate resolver and contextual evidence/conflict policy against an approved book glossary, then connect the selected permitted source layer and successful provider adapter to the translation activity/artifact store. Implement persisted QE/review routing; obtain the required human-post-edited references or an approved benchmark protocol; calibrate automatic QE and acceptance thresholds; then rerun focused gates. Keep outputs non-final until those gates pass. O-1 human review is deferred experimentation, not a blocker to starting generation.


## Bilingual termbase source review and candidate snapshot (2026-10-05)

A rights and source review was completed for reusable/reference EN↔AR terminology resources. The source archive and extracted candidate data are on the B2C data disk under `/mnt/wsl/b2cdata/eval-datasets/termbases/`; the rights, URLs, transformation notes and hashes are recorded in its `README.md`.

### Source decision

- **FAO AGROVOC — reusable licensed specialist layer.** The official Core RDF release observed was modified 2026-09-07. AGROVOC supports multilingual SKOS/SKOS-XL concepts, labels and relations; FAO publishes monthly releases and provides RDF downloads, SPARQL and Skosmos REST. Its EN/AR content is licensed CC BY IGO 3.0 under the current AIMS access statement. Preserve FAO attribution, license link, indicate modifications, and do not imply endorsement. Scope is agriculture, food, forestry, fisheries, nutrition and related rural topics, so use it only when the book domain matches. Sources: [AGROVOC access and license](https://aims.fao.org/standards/agrovoc/access-agrovoc), [FAQ and update cadence](https://aims.fao.org/standards/agrovoc/faq), [releases](https://www.fao.org/agrovoc/releases), [machine access](https://www.fao.org/agrovoc/machine-use), [CC BY 3.0 IGO](https://creativecommons.org/licenses/by/3.0/igo/).
- **Arabic WordNet 4.1 + Open English WordNet 2024 — reusable lexical sense candidates.** Both declare CC BY 4.0. They provide broad EN/AR lexical and synset coverage joined by ILI, but AWN explicitly reports AI-assisted Arabic translation, so it is a sense-discovery aid rather than an authority-approved technical termbase. Require book-context evidence and corroboration before any term injection. Attribution must include Arabic WordNet/Salah Abdo, OEWN contributors, and Princeton WordNet. Sources: [AWN dataset and methodology](https://github.com/Salah-Sal/arabic-wordnet-v4), [OEWN release](https://github.com/globalwordnet/english-wordnet/releases), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
- **IATE — high-quality reference, bulk rights not established for this use.** The EU database offers domain/context and entry-level validation/reliability information, but language coverage varies by entry and export has separate conditions/EU Login. Do not bulk-copy it to the local product unless current download terms explicitly permit the planned storage and redistribution. Individual lookup only for now. Sources: [IATE](https://iate.europa.eu/), [official export guide](https://iate.europa.eu/assets/brochure_search_by_collections_and_download.pdf).
- **UNTERM and WIPO Pearl — reference-only.** Both have strong expert terminology and Arabic/English coverage, but their terms restrict local bulk copying/storage/redistribution absent permission; WIPO additionally restricts automated/bulk acquisition. Do not cache/import. Use permitted manual lookup or a specifically licensed API. Sources: [UNTERM](https://unterm.un.org/), [UN terms](https://www.un.org/en/about-us/terms-of-use), [WIPO Pearl](https://www.wipo.int/en/web/wipo-pearl), [WIPO Pearl terms](https://www.wipo.int/en/web/wipo-pearl/terms-wipopearl).
- **ArabTerm/WikiTermBase — not imported.** It indexes useful specialist bilingual dictionaries, but its code license does not settle the rights of aggregated dictionary contents. Verify each source collection’s content license before acquisition. [Repository and source catalog](https://github.com/forzagreen/arabterm).

### Downloaded/validated data-disk snapshot

- AGROVOC official archive: `agrovoc/agrovoc_core.rdf.zip`, SHA-256 `521087c43c2046aec42a1e4f8e7eb059441e18c202d9efb7dc7e0b7c81bddf9a`. Derived EN/AR JSONL: `agrovoc/agrovoc_en_ar_candidates.jsonl`, 42,003 concepts with both preferred labels, 947 with bilingual definitions; SHA-256 `125888420889ee22746cf740b6c74aaf985f112bd81c573eb853b4150b25ed43`. One invalid XML control byte in an unrelated-language definition was normalized only in the streaming parser; source archive unchanged, and the derived rows record this.
- AWN archive `arabic-wordnet-v4/awn4.xml.gz`, SHA-256 `bd24bee99026ecc773cf51f3dc58979d8424092a49dc80f90b938fb44d32a085`; OEWN archive `arabic-wordnet-v4/oewn-2024.xml.gz`, SHA-256 `e1f633b0a93758cae34ea27c44c4dad310a8af2467b155f99dd6673af697e875`. Streaming ILI crosswalk `arabic-wordnet-v4/en_ar_ili_candidates.jsonl`: 117,414 rows; each row has nonempty EN and AR lemma arrays; SHA-256 `5b53ad490360737806e4d35d31fc31f688ce4468d912d4bc487a94db506a90c2`.
- The extracted artifacts were structurally parsed and row-checked; data and provenance remain on the B2C data disk. These are candidate datasets only, not approved mappings. No production files, runtime configuration, or model/API calls were involved.

### Practical automated G3 policy

Build the course-specific termbase primarily from source-book terminology and definitions, preserving the exact supporting text, page/block IDs, source book hash and extractor version. Treat AGROVOC as a versioned, licensed domain module when the subject matches; use WordNet only to enumerate candidate senses. Keep source URI, dataset release/hash, license, domain, alternatives and automated confidence/evidence with every mapping.

At creation, a named deterministic policy (for example, `g3-policy-v1`) can automatically include a term only when bilingual fields are complete, normalized source terms are unique, source rights allow use, the selected sense matches the lesson context, conflicts are absent, and the mapping has exact book provenance plus permitted corroboration. This does not require a human approval step at creation. If ambiguity or source conflict remains, mark the candidate unresolved and omit it from prompt-time term injection while allowing translation to continue; do not represent an uncertain mapping as approved. Future corrections can be logged separately and need not block the initial course build.

### Gate update

This research satisfies the **source inventory and licensed-candidate acquisition** task, but **does not pass G3**. The termbase compiler is not connected to these datasets or to persisted course artifacts; no EN↔AR contextual precision/coverage test, conflict policy calibration, or production attribution packaging has passed. The immediate implementable next gate is an offline, non-production candidate resolver on a representative approved book glossary, with exact provenance, domain filtering and automated conflict/coverage metrics. Only then decide which permitted source layer can be connected to runtime.

Revise the former minimum-gate wording accordingly: a sample termbase is no longer absent; runtime connection and evidence-based approval policy remain open. No change to the runtime or production default is made here.



## AGROVOC translation-pipeline integration (2026-10-05)

Implemented an opt-in EN→AR candidate resolver at packages/translation/src/b2c_translation/agrovoc.py, re-exported through b2c_translation.pipeline. It reads the versioned data-disk EN/AR candidate JSONL, verifies each row carries the expected CC BY IGO 3.0 license, and matches normalized English preferred/alternate labels. It returns a TermbaseEntry only when the caller explicitly confirms the agriculture domain, there is exactly one concept and one Arabic preferred label, bilingual definitions exist, and at least two content words overlap between the book glossary definition and AGROVOC's English definition. The entry's approved_by is the explicit machine policy identity g3-policy-agrovoc-en-ar-v1; this is a narrow policy result, not a claim that all source terms or the phase are approved. Ambiguous, out-of-domain, or context-mismatched candidates remain unresolved and cannot enter prompt-time injection.

Each result also carries an audit record with status/reason, concept URI, matched Arabic label, overlapping context words, data checksum, release, license and policy identity. The data source is optional and explicitly path-addressed; no production provider/default/runtime configuration or app service was changed. The existing translation primitive term_injection_block consumes accepted results and emits only the source term appearing in the current chunk. No live translation activity or ArtifactStore workflow exists to persist this audit record yet.

A data-backed integration test used the real downloaded AGROVOC snapshot and the glossary definition for the concept “soil”: it resolved FAO concept c_7156 to تربة and verified injection into “Soil retains water.” Out-of-domain use, a mismatched sense, and missing bilingual definition evidence were rejected. Focused checks: **11 passed** across tests/unit/translation/test_agrovoc_terms.py and the translation pipeline unit suite.

### Decision boundary

AGROVOC is now a usable, rights-documented **opt-in agriculture-domain terminology source and pipeline primitive**. It is not a general EN↔AR termbase and is not configured as production default. The result demonstrates deterministic preference for a context-supported term, not independent semantic precision/recall or commercial quality. G3 remains **NOT PASSED** until a representative book glossary evaluation establishes conflict/coverage/precision behavior, provenance is stored with course artifacts, and the owner accepts the domain-specific policy. The rest of the G4/QE and translation activity gates remain open.



## General EN↔AR termbase implementation checkpoint (2026-10-05)

The general layer is now more than a source inventory: an offline, opt-in resolver is connected to the translation pipeline primitive. The chosen broad reusable seed is structured Wikidata Lexeme/concept data (CC0 1.0), with AGROVOC retained as a separate agriculture-only module (CC BY IGO 3.0). Official licensing states Wikidata structured data, including Lexemes, is CC0; attribution is recommended. Its sense model carries glosses, translation links (P5972), concept links (P5137), and optional usage-domain IDs (P9488). Sources: [Wikidata licensing](https://www.wikidata.org/wiki/Wikidata:Licensing), [sense data](https://www.wikidata.org/wiki/Wikidata:Lexicographical_data/Documentation/Senses), [P5972](https://www.wikidata.org/wiki/Property:P5972).

The official 2026-09-30 Lexeme dump (650,549,696 bytes; SHA-256 `24b68c109044dc327498a68bee6db07f2b0e566ddb0bbeb2fbb289bf46b2f7fc`) is stored on the B2C data disk at `/mnt/wsl/b2cdata/eval-datasets/termbases/wikidata-lexemes/latest-lexemes.json.gz`. The scan found 1,691,396 lexemes, including 90,049 English and 85,929 Arabic; extraction produced 1,309 linked EN↔AR sense pairs (P5972 and/or shared P5137; SHA-256 `4a40e5e7b4320c4a6cee0e6670fdfd9f52229077e653d7524843b6e587420861`). Of 792 linked concepts enriched through the official Wikidata item API, 709 had both preferred-language labels. The bilingual candidate file is `/mnt/wsl/b2cdata/eval-datasets/termbases/wikidata-lexemes/wikidata_concept_en_ar_candidates.jsonl`, SHA-256 `523aa61b81b51787816a02c394c5f753a17c01d497c23f31694b3c8c60dddc43`. Every row carries source/license/release/hash; P9488 domain IDs are separate from P31 type IDs. No prose from Wiktionary was copied.

`packages/translation/src/b2c_translation/multisource_terms.py` loads that candidate snapshot, checks the CC0 marker and bilingual preferred labels, and resolves exact normalized English labels/lemmas against the book glossary. It requires bilingual source definitions and at least two meaningful context-word overlaps; it ranks candidates by overlap and rejects ties. AGROVOC is consulted only when agriculture is explicitly confirmed. The resulting accepted mapping is passed to the existing approved-term injection primitive, which injects only terms present in the current source chunk. Audit data records source, concept URI, Arabic label, context evidence, candidate count, domain IDs, license, release, data hash, and policy ID. This is a deterministic candidate policy, not proof of universal term correctness.

Focused integration tests cover a general non-agricultural `probability` example absent from AGROVOC (Wikidata Q9492 → `احتمال`), an agricultural `soil` case (AGROVOC c_7156 → `تربة` only with domain confirmation), and rejection of a deliberately mismatched context. The combined translation-focused run passed **14 tests** (`test_multisource_terms.py`, `test_agrovoc_terms.py`, and `test_pipeline.py`). No LLM/provider inference, production default, service, or compose configuration was changed. The source/license inventory, extraction hashes and statistics are also recorded in `/mnt/wsl/b2cdata/eval-datasets/termbases/README.md`; data files remain permission-restricted on the B2C data disk.

### Decision boundary

The practical base is **Wikidata CC0 concept candidates + AGROVOC for confirmed agriculture**, with AWN/OEWN retained only as CC BY 4.0 sense-discovery candidates (AWN reports AI-assisted Arabic content). EuroVoc's official current language list has no Arabic; IATE bulk rights for this storage/use remain unestablished; UNTERM and WIPO Pearl are reference-only pending permission; Wiktionary share-alike/GFDL is not in this default package. Official references and rights details are in the data-disk manifest and source review above.

This is a working, connected offline resolver, not G3 approval or production rollout. The 709 general concepts are a bounded seed, not evidence of complete multi-domain coverage; there is no representative textbook glossary precision/coverage evaluation, no persistence of resolver audit records in course ArtifactStore, and no production TranslationActivity connection yet. **G3 remains NOT PASSED and Phase 7 remains NO-GO.** The next gate is a representative book-glossary evaluation with measured coverage/conflicts and exact provenance persistence, then connecting this policy to the translation activity/artifact workflow.

## Owner disposition update (2026-10-05)
The owner accepts the current Phase 7 terminology-preparation checkpoint as closed for app build, using the general Wikidata CC0 candidate seed with AGROVOC for agriculture-confirmed terms. End-to-end translation activity, persisted provenance, calibrated QE, and G3/G4 evidence are deferred to app integration/trial and remain unpassed. This does not claim complete multi-domain terminology coverage or translation quality.


## Translation resume hardening — 2026-10-08

The live failure was an EN→AR course-translation batch rejected by the deterministic numeric-token guard (`numeric_token_mismatch` on a lesson question). The saved source and completed checkpoints were retained; no OCR, QA, or source artifact was regenerated. The current artifact has two distinct translation workflows: the book-text workflow translates OCR blocks for the lesson body, while the course artifact workflow translates lesson support content (summaries, glossary entries, questions, answers/rationales and course-scope Q&A). Audio consumes these as separate sources. Translation batching is four text segments, not four pages; page citations come from source OCR blocks.

Recovery changes in `packages/translation/src/b2c_translation/generation.py` and `packages/orchestration/src/b2c_orchestration/translation.py`: prompt version v2 tells the model to preserve numeric tokens; Persian/Arabic-Indic digits and decimal separators are normalized to ASCII digits in translated output before validation and storage; each batch gets a corrective retry; batches still failing are deferred while later batches proceed, then retried; persistent failure is recorded with batch/error type and the activity fails only after that deferred retry, allowing Temporal to resume from durable successful batch checkpoints. Retry/deferred events refresh progress timestamps without being counted as completed batches. Seeded checkpoints remain compatible across the prompt revision only when source/model/language identity still matches.

Deployment evidence: `py_compile` passed for the translation and orchestration modules; the updated `b2c-generation-worker:dev` image built, and a new `b2c-generation-worker-number-normalize` worker started on `external-generation` without stopping existing workers. Existing active translation workflows were not restarted. At 2026-10-08 19:34 UTC, the course-support translation workflow was active at 144/888 segments; the book OCR-text workflow was active at 12/776 segments, with a recent checkpoint. Therefore this is an operational recovery improvement, not completion of translation/audio or Phase 7 acceptance.

The OpenRouter key meter reported $0.97754297 used of an $8 limit ($7.02245703 remaining); the activity endpoint returned HTTP 403, so this is key-wide usage, not attributable to DeepSeek alone, and exact token totals/per-model cost are unavailable.

## Independent translation-track progress and recovery update (2026-10-08)

The operator APK now presents independent status cards for the book body and the course appendix. Each card reports its own status, successful/total segments, active segment count, deferred-unit count, failed-segment count, current unit, model, and latest error. The API exposes the same derived counters for running, completed, and failed workflows. Existing response fields remain supported by the APK fallback, so the display can read the current API response shape.

The v3 translation activity batches by source page or lesson/scope, writes completed batches to durable object storage, and resumes from valid saved segments. Progress events now clear stale failure counts when a retry succeeds. Deferred retries continue through every deferred unit before failing the activity, so one persistent error does not prevent attempts on the remaining units. Successful units stay checkpointed for Temporal retry/resume.

Verification performed: Python `py_compile` passed for the translation, orchestration, and API modules; Android `assembleDebug` succeeded for the 32-bit `armeabi-v7a` APK; `b2c-api:dev` and `b2c-generation-worker:dev` images built successfully. No API or worker container was replaced, and no live workflow was restarted. The APK artifact is `android/operator-app/app/build/outputs/apk/debug/app-armeabi-v7a-debug.apk`.

Last live status check at 2026-10-08 19:45 UTC: the appendix/course translation remained RUNNING at 388/888 segments with a fresh checkpoint; the book-body workflow ID previously tracked had FAILED and its status endpoint reports only the wrapper type `ActivityError`. The latest built worker/API changes have not been deployed, so these changes do not alter either live workflow. A live local-model concurrency comparison has not been run. Separate workflow counters are implemented; model calls should remain serialized per local model until a measured capacity check supports parallel inference.

Decision: **prepared for rollout after the current live translation finishes; not live-verified**. Phase 7 remains NOT PASSED.

### Body-track status recovery correction (2026-10-08)

The operator showed the book body as not started because the APK starts the appendix translation and waits for it before submitting the body translation. In addition, the API attempted to record the `book_translation` workflow under a stage name missing from `stage_route`; that persistence error was caught and logged silently, so a reopened app could not recover the workflow ID. Added the missing route mapping. The parsed-book artifact was verified in MinIO for `book-main` (677 pages, 776 blocks; source hash matched the prior failed workflow's checkpoint identity).

Resumed the failed body translation as a new Temporal workflow from the existing workflow's saved segment checkpoints and registered it under `book_translation` without changing the appendix entry. Current verification: the book-body workflow is RUNNING at 16/776 segments; the appendix remains RUNNING at 784/888. The worker/API services were not restarted. The API image including the stage mapping fix rebuilt successfully; deployment remains pending after the live workflows finish. Phase 7 remains NOT PASSED.

### Two-track launch correction (2026-10-08)

The Android audio path now starts the appendix and body translation workflows before waiting for either, saves both workflow IDs immediately, and reuses already registered IDs on resume. This avoids relying on the app remaining alive until the first translation completes. APK version 0.4.5 (versionCode 11, `armeabi-v7a`) built successfully and was uploaded to Drive. Last status check: body RUNNING 24/776, appendix RUNNING 852/888, both with recent progress checkpoints. Local-model concurrency has not been exercised; this live run uses the external DeepSeek route. API fix is built but not deployed; the active server's progress registry was updated for the body workflow so the installed app can discover it now.

### Appendix translation failure and number preservation fix — 2026-10-08

The course-appendix translation stopped on a structured-validation error: one generated question segment contained a numeral, which the model repeatedly omitted or changed. Temporal marked the workflow failed after the normal and deferred-batch retries. Its immutable translation checkpoints remain readable: 880 of 888 segments are saved; the other 8 have no accepted checkpoint. The independent book-body workflow is still RUNNING at 36/776. No workflow or worker was restarted.

The translation request path now replaces numeric literals with per-segment markers before model submission, requires each marker exactly once, restores the original normalized numeral after translation, and rejects extra markers. Its prompt fingerprint version is now v4; resume logic can reuse compatible checkpoints from v3 while regenerating only missing segments. The updated worker image built successfully as b2c-generation-worker:numeric-protection-candidate; it is not running or deployed. The failed appendix run has not been resumed, pending availability of the updated worker code.


### Translation retry, unit preview and correction versions — 2026-10-08 (source only)

The API persists each original translation input for future runs and can recover older exact inputs from the Temporal workflow-start event. Resume requests must match the original course, source ArtifactRef, source kind, languages and token limit. Terminal status counters reconcile successful segments against source-scoped immutable batch checkpoints; confirmed failed IDs and unsaved/retry segments are distinct. The units endpoint exposes each original source segment, citations, translated text and saved state.

Operator corrections are allowed only for terminal workflows, including completed ones. Each edit appends a unique immutable revision; numeric preservation is checked. Explicit resumed runs snapshot revision IDs into their workflow identity, seed corrections before generation and include correction provenance in a new final artifact/cache identity. Existing checkpoints and output artifacts are not mutated. Active workflows cannot be edited.

Android source adds per-track resume controls using only the server-validated original request, selectable unit preview, and terminal-only edit/save controls. Successful, confirmed failed, and unsaved/retry counts are displayed separately. Python compileall, API/orchestration imports, and compileDebugJavaWithJavac passed. No inference, tests, service deployment/restart, live endpoint verification, or APK assembly occurred in this change. The active body translation and failed appendix workflow remain untouched; these features require a coordinated API/worker rollout and a subsequent APK build before mobile use.

Remaining targeted-operation gap: automatic retranslation of one already-saved unit is not implemented. Whole-source checkpoint resume and manual per-unit corrections are implemented in source. A single-unit translation activity with a versioned accepted result is needed to regenerate only that unit without also triggering other unsaved source units.


### Isolated segment retranslation — 2026-10-08 (source only)

The dedicated RetranslateSegmentWorkflow and retranslate_segment activity are registered on generation/heavy workers. Queueing resolves the exact original workflow request, rejects active parents and absent/ambiguous units, and sends only the selected segment identity. The worker rechecks terminal parent, persisted original request, course/stage scope and source text hash; standard translation numeric/output invariants apply to exactly one segment. A separate immutable candidate survives activity retry and never overwrites checkpoint content.

API routes queue/review a candidate, append human candidate revisions and explicitly accept a selected revision into the existing immutable override/resume mechanism. Accepted revisions are idempotent. Normal translation proceeds automatically without human review. Android source adds segment-only retranslation, status refresh, source/current/result comparison, per-segment pending-candidate reopen, append-only editing and explicit reviewed-revision acceptance. Saving an edit does not silently apply it. Existing source unit preview reopens accepted manual/model versions for subsequent new corrections.

Evidence: Python compileall and API/orchestration imports passed; compileDebugJavaWithJavac passed. No tests, inference requests, service deployment/restart or APK assembly were performed. End-to-end runtime validation and coordinated rollout remain necessary after active body translation reaches its safe point.

Revision selection is reversible: units expose the original machine checkpoint and all immutable accepted revisions; explicit selection appends a new selection revision. The operator can preview/select an older accepted version or revert to the original machine text without deleting/overwriting any checkpoint or revision. Selection is applied by a later explicit resume; it does not gate or mutate active automatic work.

### Translation omission triage — 2026-10-09 (source only)

Read-only cache and checkpoint inspection found schema-valid body translations containing only source headings. Numeric preservation alone did not reject those when the retained heading had the same numeric tokens. A new conservative triage rule rejects machine/checkpoint text when stripped source length is at least 200 characters and stripped target/source character ratio is below 0.25. This threshold identifies severe omission candidates; it is not semantic quality measurement or proof that an unflagged translation is complete. Source/target length differs by language, so broader word/paragraph alignment and coverage assessment still require independent validation.

The worker validates fresh output and excludes suspect persisted text from resume inputs, preserving all original checkpoint objects. Resume matches source IDs, rather than requiring complete legacy groups, so valid peers survive migration from fixed four-segment groups to page-bounded batches; only missing, numerically invalid or suspect units are requested. Progress and unit previews distinguish physical checkpointed segments from accepted saved segments and expose suspect IDs/details. Terminal status reconciles these counts from the source and immutable checkpoints, including current revision selection. Active progress clears suspect flags after successful replacements.

No inference/provider requests, tests, deployment or service actions were performed for this source change. Compilation/import checks are recorded separately after completion.

The stopped body workflow `b2c-translate-4c5b2f4ae11d54c79432e513dcd0438729d46e5c68a45d46a17e7cbcfb385eba` has 204 unique immutable checkpoint segments. Read-only application of the length gate flags 27; 177 pass this gate (passing is not proof of full translation quality). Flagged IDs: book-main:26:0, 27:0, 45:1, 130:0, 131:0, 155:0, 156:1, 157:0, 163:0, 164:0, 181:1, 182:0, 183:0, 202:1, 203:0, 204:0, 256:0, 258:0, 264:1, 265:0, 266:0, 304:0, 306:0, 307:0, 338:0, 339:1, 340:0 (all abbreviated IDs after the first retain the book-main prefix). Examples: page26 source1625chars/target5chars; page45:block1 source1976/target10; page307 source1730/target250. These ratios are severe enough to flag for regeneration while keeping the machine originals available for comparison.


## Automatic translation unit recovery candidate —2026-10-09

See `translation-automatic-unit-recovery-2026-10-09.md`: fresh-cache source/target comparison found two heading-only omissions and one Arabic-attached numeral false rejection. Candidate generation.py automatically isolates uppercase headings, bounds long-unit requests, splits exhausted units, reassembles original IDs and preserves strict full-source validation;58 focused tests passed. Only generation.py is staged; runtime/workflow untouched by this preparation. Phase quality gates and all776 live-unit completion remain unproven.
