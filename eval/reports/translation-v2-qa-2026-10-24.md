# Translation v2 QA (D-028 part B): deterministic checks on the audit gold set, 2026-10-24

Status: **measurement only, not a gate.** No gate passes or fails on the basis of this report.

## What was executed (verified)

- `uv run python -m b2c_translation.evaluation --gold /mnt/wsl/b2cdata/eval-datasets/translation-audit-2026-10-24/gold.jsonl --out <file>`
  - Gold sha256 is `bc795b7e0b03d9423858bb9473b90e7e3d4f2d87503ecf67d3e3a8d04725508f`, which matches `SHA256SUMS`.
  - The run took about 20 s on CPU.
  - The generated tables are reproduced below this analysis.
- `DEFAULT_CHECKS` were run on (a) `pipeline_target` and (b) `corrected_translation` for all 3396 rows. One `TranslationUnit` was built per row, with the kind taken from `block_type`.
- Splits:
  - A deterministic hash of `book|segment_id` holds out 30% as **test**; the other 70% is **calibration**.
  - Per-book results are also reported: book1 has 776 rows and book2 has 2620.
- No semantic QE (LLM judge or CometKiwi) was run. Every number below is for the deterministic layer alone.

## Results against the targets

| target | all | held-out test | met? |
|---|---|---|---|
| leakage recall 100% | 99.4% same-category (475/478); 99.6% blocked by some check (476/478) | 99.3% (145/146); 100% blocked | **no** (3 misses, below) |
| leakage false positives < 1% | 0.0% corrected (0/923); 0.1% approved (1/797) | 0.0% (0/278); 0.4% (1/239) | yes |
| false positives on corrected translations < 5% | 2.1% (19/923) | 2.5% (7/278) | yes overall; **book1 alone 5.7% (9/159)** |
| numbers / footnotes recall 100% (design §6) | number_error 12.5% same-category (40% blocked); date_error 88.0%; footnote_marker 88.5% blocked | 20% / 72% / 94.1% | **no** |
| share of the 1904 false accepts stopped by deterministic checks alone | **30.2% (575/1904)** | 29.9% (168/562) | no target; semantic QE must stop the rest |

### The 3 leakage misses

All three were checked by hand. No pattern was tuned to them.

1. The leak occurred only in an earlier attempt. The final `pipeline_target` has no instruction text, so no check on the final text can see it.
2. The model output a bare list of glossary *target* terms, with no JSON, labels or brackets. Catching this needs the termbase: the check would flag Arabic termbase entries that appear without their English source term. That check has not been built.
3. One pipeline word replaced the subject: "this source" became «هذه المصدر». This is ordinary Arabic, and a pattern for it would not generalise.

The single leakage hit on an audit-approved target is the false-reject segment. Its evaluated text still contains unrestored `[[B2C_NUM_n]]` placeholders, so the hit is a genuine defect.

### Why numbers and footnotes are below 100%

- Most gold `number_error` issues are **number words** the model translated wrongly:
  - "two dozen" became 22;
  - "seventy-one million" became 47 million;
  - "thirty-two" became 33.
- Other cases:
  - figure labels lost their letter suffix ("3.3a" became "3.3");
  - a percent sign was dropped.
- A digit multiset cannot see these errors without an Arabic number-word parser, so they are left to semantic QE.
- Gold footnote markers are inline in the source, because the rows are pre-rebuild segments. A marker defect therefore shows up as a number mismatch.
- The `footnote_markers` check itself is exercised only by unit tests. It requires the markers to appear exactly at the end, as `unit.footnote_markers` lists them.

### Why the semantic categories are low

- meaning_error, terminology, named_entity and polarity_error have **0–1% same-category recall**. They need semantic judgement or a termbase/entity table.
- The audit had no termbase or entity table, so the `terminology` and `named_entity` checks are **not exercised** here.
- The 16–20% of these segments that are "blocked by any check" are blocked by co-occurring defects, mostly leakage.
- Polarity: the cue check catches 1 of 90 polarity errors.
  - Most of these errors are lexical reversals, such as easy↔hard or "disservice".
  - The others sit in long segments that also contain other negations.
  - Rebuild units are at most 400 chars, which should help this check. That has not been measured.
- Omission: 62.6% of omission segments are blocked, mostly through leakage. The length and sentence checks on their own catch 1.5%.
  - The gold omissions are partial drops inside long segments.
  - On the calibration split, their length and sentence ratios overlap with those of the corrected translations.

## Calibration and generalisation

- **Length ratio.** Bounds are on non-space characters and sit *outside* the range observed in corrected and approved translations on the calibration split.
  - Prose with a source of 40 or more chars: observed 0.55–1.37, bounds 0.50–1.60.
  - Sources under 40 chars: bounds 0.35–2.50.
  - Sources under 12 chars are skipped.
- **Sentence check.** It fires when the source has 3 or more sentences and the target has at most 50% of that count.
  - On the calibration split, a 70% threshold would flag 13 of 371 corrected translations but only 7 of 157 gold omissions, so 50% was kept.
- **Rules added after reading calibration-split misses and false positives.** Each is a general rule:
  - a decade written as a bare year ("1970s" → "1970") is a number error, unless the target also has the Arabic decade word;
  - Latin text glued to Arabic inside one token is flagged;
  - a Latin-only gloss in parentheses or quotes is not counted as untranslated;
  - a surplus copy of a number that is in the source is only minor;
  - a negation stem with a pronoun suffix counts as a negation cue.
- **Calibration vs test.** The test split is consistent:
  - false accepts stopped: 29.9% on test, 30.3% on calibration;
  - false positives on corrected translations: 2.5% on test, 1.9% on calibration;
  - date_error recall: 72% on test, 88% on all rows. This gap is how far the decade rule generalises.
- **Leak patterns are not a held-out measure.** They were built from leak excerpts across **all** rows, as the brief asked.
  - They are Arabic renderings of the retired prompt (protected marker, source label, inventory list, retry feedback, JSON). They are not book text.
- **Remaining false positives on corrected translations:**
  - English bibliographic titles kept verbatim;
  - lexically expressed negation, e.g. "little or no influence".
  - Both occur mostly in reference-like rows. Rebuild `reference`/`toc`/`index` units skip the script and untranslated checks.

## Limitations

- Gold rows are pre-rebuild page segments:
  - the median length is 160 chars, and 695 rows are over 1000 chars;
  - rebuild units are sentence-based and at most 400 chars.
- Recall is measured per segment. "Blocked" means some major or critical deterministic issue fired, not necessarily on the gold error's span.
- No termbase, no entity table, no rebuild footnote handling and no QE were involved.

## Open

- Run semantic QE on the same set (T-TR-03/06) and calibrate its thresholds. Policy never accepts engine output without QE.
- Build a termbase-aware addition check, which would cover leakage miss 2 and catalogue §2.5.
- Add English and Arabic number-word parsing to raise number_error recall.

---

## Generated tables (`b2c_translation.evaluation`)

Gold set: `/mnt/wsl/b2cdata/eval-datasets/translation-audit-2026-10-24/gold.jsonl` (3396 rows; 27 rows without a pipeline target skipped for (a)). Leak patterns version `2026-10-24.1`. Checks: empty_target, prompt_leakage, foreign_script, latin_not_in_source, untranslated_english, numbers, footnote_markers, length_ratio, sentence_alignment, repetition, terminology, named_entity, brackets, quotes, polarity.

A segment counts as *blocked* when any check raises a major or critical issue. *Same-category* means the blocking issue has the gold category (date_error -> number_error, segmentation -> omission/addition, other:* -> foreign_script, footnote_marker -> footnote_marker/number_error, untranslated_english -> untranslated_english/foreign_script).

## Recall on gold issues (major/critical, segment level, `pipeline_target`)

### Split: all

| category | positives | same-category | blocked by any check |
|---|---:|---:|---:|
| meaning_error | 1026 | 0.0% (0/1026) | 19.4% (199/1026) |
| terminology | 716 | 0.0% (0/716) | 16.6% (119/716) |
| prompt_leakage | 478 | 99.4% (475/478) | 99.6% (476/478) |
| omission | 388 | 1.5% (6/388) | 62.6% (243/388) |
| named_entity | 216 | 0.0% (0/216) | 18.5% (40/216) |
| date_error | 92 | 88.0% (81/92) | 94.6% (87/92) |
| polarity_error | 90 | 1.1% (1/90) | 18.9% (17/90) |
| untranslated_english | 74 | 45.9% (34/74) | 52.7% (39/74) |
| grammar | 69 | 0.0% (0/69) | 20.3% (14/69) |
| structure_formatting | 62 | 0.0% (0/62) | 24.2% (15/62) |
| footnote_marker | 52 | 44.2% (23/52) | 88.5% (46/52) |
| addition | 42 | 9.5% (4/42) | 19.0% (8/42) |
| number_error | 40 | 12.5% (5/40) | 40.0% (16/40) |
| bibliographic | 24 | 0.0% (0/24) | 16.7% (4/24) |
| quotation | 22 | 0.0% (0/22) | 36.4% (8/22) |
| segmentation | 13 | 38.5% (5/13) | 76.9% (10/13) |
| foreign_script | 10 | 100.0% (10/10) | 100.0% (10/10) |
| fluency_style | 6 | 0.0% (0/6) | 0.0% (0/6) |
| repetition | 6 | 16.7% (1/6) | 50.0% (3/6) |

### Split: calibration

| category | positives | same-category | blocked by any check |
|---|---:|---:|---:|
| meaning_error | 713 | 0.0% (0/713) | 19.1% (136/713) |
| terminology | 497 | 0.0% (0/497) | 14.9% (74/497) |
| prompt_leakage | 332 | 99.4% (330/332) | 99.4% (330/332) |
| omission | 280 | 1.1% (3/280) | 61.4% (172/280) |
| named_entity | 155 | 0.0% (0/155) | 18.7% (29/155) |
| date_error | 67 | 94.0% (63/67) | 97.0% (65/67) |
| polarity_error | 65 | 1.5% (1/65) | 21.5% (14/65) |
| untranslated_english | 55 | 47.3% (26/55) | 54.5% (30/55) |
| structure_formatting | 48 | 0.0% (0/48) | 27.1% (13/48) |
| grammar | 46 | 0.0% (0/46) | 19.6% (9/46) |
| footnote_marker | 35 | 42.9% (15/35) | 85.7% (30/35) |
| addition | 32 | 3.1% (1/32) | 12.5% (4/32) |
| number_error | 30 | 10.0% (3/30) | 33.3% (10/30) |
| bibliographic | 16 | 0.0% (0/16) | 12.5% (2/16) |
| quotation | 15 | 0.0% (0/15) | 40.0% (6/15) |
| segmentation | 9 | 44.4% (4/9) | 77.8% (7/9) |
| foreign_script | 6 | 100.0% (6/6) | 100.0% (6/6) |
| repetition | 6 | 16.7% (1/6) | 50.0% (3/6) |
| fluency_style | 5 | 0.0% (0/5) | 0.0% (0/5) |

### Split: test

| category | positives | same-category | blocked by any check |
|---|---:|---:|---:|
| meaning_error | 313 | 0.0% (0/313) | 20.1% (63/313) |
| terminology | 219 | 0.0% (0/219) | 20.5% (45/219) |
| prompt_leakage | 146 | 99.3% (145/146) | 100.0% (146/146) |
| omission | 108 | 2.8% (3/108) | 65.7% (71/108) |
| named_entity | 61 | 0.0% (0/61) | 18.0% (11/61) |
| date_error | 25 | 72.0% (18/25) | 88.0% (22/25) |
| polarity_error | 25 | 0.0% (0/25) | 12.0% (3/25) |
| grammar | 23 | 0.0% (0/23) | 21.7% (5/23) |
| untranslated_english | 19 | 42.1% (8/19) | 47.4% (9/19) |
| footnote_marker | 17 | 47.1% (8/17) | 94.1% (16/17) |
| structure_formatting | 14 | 0.0% (0/14) | 14.3% (2/14) |
| number_error | 10 | 20.0% (2/10) | 60.0% (6/10) |
| addition | 10 | 30.0% (3/10) | 40.0% (4/10) |
| bibliographic | 8 | 0.0% (0/8) | 25.0% (2/8) |
| quotation | 7 | 0.0% (0/7) | 28.6% (2/7) |
| foreign_script | 4 | 100.0% (4/4) | 100.0% (4/4) |
| segmentation | 4 | 25.0% (1/4) | 75.0% (3/4) |
| fluency_style | 1 | 0.0% (0/1) | 0.0% (0/1) |

### Split: book1

| category | positives | same-category | blocked by any check |
|---|---:|---:|---:|
| meaning_error | 298 | 0.0% (0/298) | 16.8% (50/298) |
| terminology | 235 | 0.0% (0/235) | 14.0% (33/235) |
| omission | 65 | 4.6% (3/65) | 36.9% (24/65) |
| named_entity | 56 | 0.0% (0/56) | 14.3% (8/56) |
| prompt_leakage | 54 | 98.1% (53/54) | 98.1% (53/54) |
| structure_formatting | 52 | 0.0% (0/52) | 21.2% (11/52) |
| untranslated_english | 39 | 38.5% (15/39) | 41.0% (16/39) |
| polarity_error | 33 | 3.0% (1/33) | 15.2% (5/33) |
| grammar | 26 | 0.0% (0/26) | 38.5% (10/26) |
| number_error | 20 | 0.0% (0/20) | 20.0% (4/20) |
| addition | 14 | 0.0% (0/14) | 14.3% (2/14) |
| bibliographic | 14 | 0.0% (0/14) | 28.6% (4/14) |
| date_error | 13 | 100.0% (13/13) | 100.0% (13/13) |
| footnote_marker | 6 | 50.0% (3/6) | 50.0% (3/6) |
| foreign_script | 5 | 100.0% (5/5) | 100.0% (5/5) |
| quotation | 5 | 0.0% (0/5) | 0.0% (0/5) |
| fluency_style | 4 | 0.0% (0/4) | 0.0% (0/4) |
| repetition | 4 | 0.0% (0/4) | 50.0% (2/4) |
| segmentation | 2 | 0.0% (0/2) | 0.0% (0/2) |

### Split: book2

| category | positives | same-category | blocked by any check |
|---|---:|---:|---:|
| meaning_error | 728 | 0.0% (0/728) | 20.5% (149/728) |
| terminology | 481 | 0.0% (0/481) | 17.9% (86/481) |
| prompt_leakage | 424 | 99.5% (422/424) | 99.8% (423/424) |
| omission | 323 | 0.9% (3/323) | 67.8% (219/323) |
| named_entity | 160 | 0.0% (0/160) | 20.0% (32/160) |
| date_error | 79 | 86.1% (68/79) | 93.7% (74/79) |
| polarity_error | 57 | 0.0% (0/57) | 21.1% (12/57) |
| footnote_marker | 46 | 43.5% (20/46) | 93.5% (43/46) |
| grammar | 43 | 0.0% (0/43) | 9.3% (4/43) |
| untranslated_english | 35 | 54.3% (19/35) | 65.7% (23/35) |
| addition | 28 | 14.3% (4/28) | 21.4% (6/28) |
| number_error | 20 | 25.0% (5/20) | 60.0% (12/20) |
| quotation | 17 | 0.0% (0/17) | 47.1% (8/17) |
| segmentation | 11 | 45.5% (5/11) | 90.9% (10/11) |
| bibliographic | 10 | 0.0% (0/10) | 0.0% (0/10) |
| structure_formatting | 10 | 0.0% (0/10) | 40.0% (4/10) |
| foreign_script | 5 | 100.0% (5/5) | 100.0% (5/5) |
| fluency_style | 2 | 0.0% (0/2) | 0.0% (0/2) |
| repetition | 2 | 50.0% (1/2) | 50.0% (1/2) |

## False positives

`corrected` = corrected translations from the audit; `approved` = pipeline targets the audit judged `correct`.

| split | set | flagged (any major/critical) | leakage | top checks |
|---|---|---:|---:|---|
| all | corrected | 2.1% (19/923) | 0.0% (0/923) | untranslated_english 6, polarity 5, numbers 3, repetition 3, sentence_alignment 3 |
| all | approved | 0.9% (7/797) | 0.1% (1/797) | untranslated_english 6, prompt_leakage 1, latin_not_in_source 1, numbers 1 |
| calibration | corrected | 1.9% (12/645) | 0.0% (0/645) | untranslated_english 4, polarity 4, numbers 2, repetition 2, sentence_alignment 2 |
| calibration | approved | 0.9% (5/558) | 0.0% (0/558) | untranslated_english 5 |
| test | corrected | 2.5% (7/278) | 0.0% (0/278) | untranslated_english 2, latin_not_in_source 1, sentence_alignment 1, repetition 1, polarity 1 |
| test | approved | 0.8% (2/239) | 0.4% (1/239) | untranslated_english 1, prompt_leakage 1, latin_not_in_source 1, numbers 1 |
| book1 | corrected | 5.7% (9/159) | 0.0% (0/159) | untranslated_english 4, latin_not_in_source 2, numbers 2, repetition 2, polarity 1 |
| book1 | approved | 0.0% (0/83) | 0.0% (0/83) | - |
| book2 | corrected | 1.3% (10/764) | 0.0% (0/764) | polarity 4, sentence_alignment 2, untranslated_english 2, repetition 1, numbers 1 |
| book2 | approved | 1.0% (7/714) | 0.1% (1/714) | untranslated_english 6, prompt_leakage 1, latin_not_in_source 1, numbers 1 |

## False accepts stopped by deterministic checks alone

| split | accepted major/critical segments | stopped |
|---|---:|---:|
| all | 1904 | 30.2% (575/1904) |
| calibration | 1342 | 30.3% (407/1342) |
| test | 562 | 29.9% (168/562) |
| book1 | 504 | 17.9% (90/504) |
| book2 | 1400 | 34.6% (485/1400) |

## Calibration (length ratio, corrected translations, calibration split, source >= 40 chars)

| kind | observed non-space ratio target/source | configured bounds |
|---|---|---|
| list_item | n=106, min 0.69, p1 0.72, median 0.88, p99 1.23, max 1.49 | (0.5, 1.7) |
| prose | n=374, min 0.64, p1 0.69, median 0.82, p99 1.09, max 1.34 | (0.5, 1.6) |
| table_cell | n=1, min 0.78, p1 0.78, median 0.78, p99 0.78, max 0.78 | (0.4, 2.0) |
