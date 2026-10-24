# Independent translation and quality-gate review — 2026-10-16

## Scope and operational boundary

This is a source review and repair of the worker-to-HY translation contract, retry evidence, structural/numeric validation, cache identity, and failure counters. The live Book 2 workflow was observed read-only. No workflow or worker was paused, stopped, restarted, rebuilt, or deployed by this review; no inference request was sent and no saved checkpoint was changed. These changes are verified by regression tests, not yet by live model output.

Reviewed repository instructions, `docs/06-DELIVERY-PLAN.md`, `docs/PROJECT-STATUS.md`, `eval/reports/book2-deferred-retry-runtime-audit-2026-10-14.md`, and `eval/reports/deferred-unit-audit-2026-10-16.md` before edits. Existing unrelated changes were preserved.

## Evidence

Read-only snapshot: Book 2 workflow `b2c-translate-34a38983393ed2772c77236afccdd6cfc39725197b65fc978bb153931b92e440`, progress updated at `2026-10-16T18:39:33.901937+00:00`, 646/2620 saved, batch 252, 34 unresolved segment failures. All 34 persisted causes were compared with the source unit and the latest associated response in the gateway cache. Each latest response reproduced its persisted error under the running validator. The cache lookup associates responses by segment ID and timestamp; it does not prove that every cached response belongs to the most recent activity attempt, because request metadata linkage is incomplete.

| Persisted reason | Units |
| --- | ---: |
| numeric_token_mismatch | 12 |
| numeric_placeholder_order_mismatch | 11 |
| malformed_numeric_placeholder | 5 |
| duplicate_numeric_placeholder | 2 |
| suspiciously_short_translation | 2 |
| unclosed_target_bracket | 1 |
| unexpected_table_placeholder | 1 |
| **Total** | **34** |

The 34 source/response pairs were inspected individually. This is an inspection of the currently unresolved set, not a quality audit of all 646 saved translations.

| Unit suffix | Observed defect in associated cached response |
| --- | --- |
| 109:3 | Omitted numeric footnote marker. |
| 118:1 | Severe genuine omission; 1852 source characters become 687 target characters, with missing earlier content. |
| 121:0 | Duplicated marker and markedly shortened content. |
| 125:1 | Preserved years but omitted footnote markers 2 and 4; previously labeled as order mismatch. |
| 13:2 | Editorial square bracket closes in source but not target. |
| 142:0 | Preserved years but omitted footnote markers. |
| 147:1 | Missing marker 2 among otherwise retained markers. |
| 14:2 | Missing footnote 13 while years were retained. |
| 164:1 | One malformed numeric marker and an extraneous closing brace. |
| 166:1 | Both footnote markers omitted. |
| 16:1 | Years retained; footnotes omitted. |
| 171:2 | Footnote marker omitted. |
| 174:1 | Footnotes omitted. |
| 175:1 | Year retained; footnotes omitted. |
| 177:1 | Short OCR fragment followed by translated retry/instruction data and generic marker examples absent from the source. |
| 17:1 | Short OCR fragment contaminated by translated retry/instruction data and generic marker examples. |
| 186:0 | Source scanner failed to protect the first year in a prose-attached year range; target also has an extra closing brace. |
| 18:0 | Some footnote markers omitted. |
| 205:0 | Omitted numeric marker and invented table-cell markers. |
| 20:2 | Footnote marker omitted. |
| 213:1 | Missing marker 5 among retained historical years and footnotes. |
| 222:1 | Numerous earlier numeric markers omitted while later years retained. |
| 229:0 | Numeric markers omitted. |
| 37:2 | Entire sequence of footnote markers omitted. |
| 42:0 | Response largely translates validation instructions rather than the OCR fragment. |
| 46:3 | Invented table-cell marker; parenthetical source content omitted. |
| 47:1 | Duplicated numeric markers and instruction/feedback contamination. |
| 48:2 | Missing marker 1; other markers retained. |
| 51:0 | Translation followed by translated retry JSON and generic marker examples absent from the source. |
| 54:0 | Many footnote markers omitted among preserved years. |
| 60:0 | Severe genuine omission; 1152 source characters become 434 despite retaining all numeric markers. |
| 67:2 | Missing marker 2. |
| 93:1 | Source number words converted to new digits and meaning shifted from employment duration to age. Preserving numeric markers alone would not detect this semantic shift. |
| 99:1 | Missing final footnote marker and substantially shortened content. |

No questionable output was accepted by relaxing completeness or number checks.

## Root causes and source fixes

### 1. HY prompt boundary contradicted the intended execution contract

The worker supplied `context_segments`, but the HY adapter did not use that field. The adapter put validation guidance and previous-draft JSON after `[Source Text]`. Actual cached outputs for units 17:1, 42:0, 51:0, 177:1 and 47:1 translated that instruction material into Arabic. This is direct evidence of instruction contamination, not merely a hypothetical prompt concern. Short OCR fragments are especially exposed because instruction text greatly outweighs the source.

The adapter now places trusted instructions, an optional glossary, untrusted background context and bounded repair evidence before the final `[Source Text]` block. Source is last. Same-page context is passed through, capped at two records and 900 characters, excluding the target unit. The model is explicitly told not to translate background or repair data. The worker's generic system instruction and adapter no longer teach generic marker templates when absent from source. The adapter lists only exact markers present in that unit, and instructs it to retain footnotes and citations and not convert number words to new digit literals.

The official [Hy-MT2-1.8B model card](https://huggingface.co/tencent/Hy-MT2-1.8B) documents a context-first, source-last layout and dedicated terminology instructions. The patch follows that ordering. It does not change sampling settings or claim that prompt order guarantees semantic accuracy.

### 2. Missing numbers were being diagnosed as reordered numbers

If only some protected numeric markers survived, restoration often raised `numeric_placeholder_order_mismatch`. Feedback then told the model to reorder, although the missing content was usually a footnote or citation. A new `missing_numeric_placeholder` reason distinguishes omission from actual reordering. Retry evidence now includes missing/unexpected/duplicate marker lists and bounded source excerpts around each numeric anchor. These are evidence, not automatically inserted translations. Output remains rejected until the marker/content contract is satisfied.

Source scanner handling of prose-attached historical ranges was also wrong: in a source such as `uprisings-1848-1849`, 1848 could remain unprotected while 1849 was protected. The source scanner now recognizes four-digit historical years attached by prose hyphens while preserving known model/standard identifier exclusions and true negative numbers. Arabic/Persian digit glyphs in marker indices are normalized before parsing, without accepting ambiguous zero-padded indices.

### 3. Table and bracket repair evidence was sometimes wrong

Expected table markers were derived from raw source, which contains no generated transport markers. Feedback could therefore claim an empty expected list. It now derives evidence from the same protected-source transformation and table policy as the translation request.

Bracket evidence previously counted the square brackets of transport markers and only supplied square-bracket guidance. It now excludes transport markers and reports each relevant bracket kind. Validation previously required every source bracket kind to be balanced before checking any target kind, and rejected only missing closing brackets. It now checks each balanced source pair independently, rejects extra closing brackets and reversed pairs, while allowing source fragments genuinely cut across page boundaries.

### 4. Changed adapter behavior could still replay an old cached answer

The gateway cache is keyed before domain validation; a schema-valid but translation-invalid response can remain cached. An adapter-only prompt fix does not change the caller's cache key. The orchestration prompt namespace is now `phase7-translation-v7-source-boundary`. Existing same-workflow checkpoint compatibility still adopts validated saved segments across prompt versions, so saved translations need not be requested again. The new namespace makes new or unresolved requests bypass older prompt-contract replies.

This is a contract-version fix, not a general cache eviction API. Future adapter/validator contract changes must also change the namespace. Request/response lineage should eventually become explicit for forensic audits.

### 5. Failure summaries overcounted attempts

A terminal summary could increment an already-recorded failure attempt a second time. `translation_partial`, `translation_completed`, and `result_committed` now carry forward the established count rather than adding an attempt. The regression proves three actual model attempts remain three in the final failure state. This does not provide complete provider-call accounting inside every adaptive-split subtree; gateway usage remains the source for billed requests.

## Why previous fixes did not stop recurrence

The evidence supports multiple compounding causes:

1. Previous runtime audit documented workers running divergent source versions and absent repair feedback in older images.
2. Current prompt construction could discard worker context and expose the retry instructions as material to translate.
3. Generic nonexistent marker examples encouraged marker invention; inaccurate missing-marker/table/bracket evidence could ask for the wrong correction.
4. Adapter-only changes could be hidden by cached old responses.
5. Real model omissions and semantic errors remain possible. Stronger detection correctly rejects them; it cannot itself repair meaning.

The existing bounded policy performs an initial request and immediate correction, preserves unresolved units, then visits them during deferred recovery/adaptive handling. A deferred unit is therefore not proof that all later recovery paths already ran. This patch did not increase retry counts or weaken validators to make the failure counter look better.

## Files changed

- `packages/translation/src/b2c_translation/generation.py`
- `docker/hy-translation/server.py`
- `packages/orchestration/src/b2c_orchestration/translation.py`
- `tests/unit/translation/test_strict_gate_contract.py` (new)
- `tests/unit/orchestration/test_translation_partial_result.py`

## Verification

Compilation of the three changed Python source modules passed. Focused test command from repository root:

```sh
.venv/bin/python -m pytest -q tests/unit/translation tests/unit/orchestration/test_translation_checkpoint_selection.py tests/unit/orchestration/test_translation_partial_checkpoint_durability.py tests/unit/orchestration/test_translation_partial_result.py
```

**164 passed in 17.99 seconds.** This includes 17 new strict-contract regressions and the attempt-counter/cache-version assertions in the existing partial-result test. Coverage includes the worker-to-HY prompt contract, external-provider system instruction, context preservation, exact source marker inventory, missing versus reordered markers, numeric glyphs, year ranges versus identifiers/negatives, protected table evidence, bracket types, a fake-provider retry leading to a durable valid checkpoint, and same-workflow checkpoint adoption across prompt upgrades. Earlier narrower runs passed 127 existing tests plus the initial 16 new tests; the 164-test run supersedes those counts.

## Safe rollout plan for the coordinating agent

1. Record workflow IDs, checkpoint counts/digests, active activity state and running worker/HY image or source versions. Do not create replacement workflow IDs.
2. At an acknowledged saved batch boundary, deploy the HY adapter and translation workers together using the project's existing full Compose file set. A worker-only deployment will not fix an old adapter; an adapter-only deployment without the new caller namespace can reuse stale cache entries.
3. Confirm the running source/version and `phase7-translation-v7-source-boundary`, then resume the same workflows. Adopt valid checkpoints; retry only unresolved or explicitly invalid units.
4. Check new request/response evidence for a short fragment, a footnote-heavy paragraph, a table and a genuine omission. Verify source is last, context is separate, marker inventory is source-specific, and a rejected unit receives the actual typed error and anchors.
5. Verify checkpoint counts never fall and no saved valid unit is submitted again. Confirm failure counters reflect attempts rather than summaries. Keep incomplete output rejected; continue processing independent units.
6. Measure accepted units per model call and residual error classes before declaring the change effective. If new symptoms appear, preserve the failing request/response and repair the general contract; do not silently accept output or insert missing content without model/source verification.

## Remaining risks and limits

- No live deployment or model A/B validation was performed. The patch removes reproducible software defects; it does not establish zero semantic errors or zero future deferrals.
- Character ratios and numeric/structural contracts cannot prove complete meaning. Unit 93:1 demonstrates a semantic shift even with several markers preserved; a separate meaning-aware evaluation remains necessary.
- Malformed OCR fragments still limit source quality. Nearby context helps but must never be translated as target content.
- Historical-year recognition uses a conservative heuristic and identifier exclusions; additional languages/technical identifiers may require regression examples.
- Numeric-anchor evidence is bounded. Extremely dense tables can exceed its cap and still need adaptive splitting.
- New structural checks can surface invalid output previously accepted. Existing checkpoints are not silently rewritten or globally re-audited by this patch.
- Adaptive-subtree attempt accounting and cache request lineage remain follow-up observability work.
