# Remaining book-body translation units: diagnosis and corrective rollout

## Evidence and scope

Inspected original source text, historical parent failed-unit codes, and retained actual model responses for every remaining unit. Retained responses include adaptive fragments; fragment lengths are not treated as evidence of omission from a full request. No credentials or complete book passages are recorded in this report.

Parent `b2c-translate-8b938c72dce0a2aef345a10f5c35e323f33703b608c315326ff6e703cea8a07c` finished Temporal COMPLETED with durable PARTIAL artifact: 765/776. Page647 recovered in this parent. Eleven outstanding IDs follow.

| Segment | Source-specific cause exposed by inspection | Corrective path |
|---|---|---|
| book-main:81:0 | Numeric protection saw digits inside a PDF-wrapped URL; model rejoined the URL, changing validator classification | Exclude complete wrapped URL spans before numeric protection; validate final restored text |
| book-main:119:0 | `download?` URL followed by `doi=` continuation; line layout changed URL-number classification | Wrapped-URL span recognition and existing bounded adaptive retry |
| book-main:120:0 | Valid Arabic kept note16 after URL; Python `$` prefix matching falsely excluded16 in source and then rejected it in target | Absolute-end `\Z` prefix anchor |
| book-main:121:0 | Valid Arabic kept note18 after URL; same false exclusion | Absolute-end prefix anchor |
| book-main:169:0 | Wiki `cite_note-` newline16 and next-line numbered notes caused inconsistent URL/note classification | Wrapped URL spans plus independent next-note recognition |
| book-main:353:0 | Separate System1 and footnote2 markers joined as `1.2`; numeric parser classified a decimal absent from source | Preserve separating punctuation and insert whitespace between separate adjacent protected values |
| book-main:426:0 | Normal prose beginning “A High Impact/Low Probability…” was misclassified as a rating matrix because articleA looked like a row label | Require a genuine value-only row for labelled rating fragments |
| book-main:465:0 | Broken `http://d-` URL continued on the next line; later note number3 was inconsistently excluded | Wrapped URL and exact-end prefix handling |
| book-main:467:0 | Full retained Arabic response preserved all correct source numbers under corrected tokenization; old marker-order check failed | Final-string marker slot validation and exact-end prefix handling |
| book-main:560:0 | Wrapped DNI URL contained1945 and2019; model rejoined URL during adaptive output | Wrapped URL span handling |
| book-main:673:0 | Numbered notes2,3,4,9 followed URL lines and were falsely excluded in source | Absolute-end prefix anchor and wrapped URL handling |

## Source changes and checks

`packages/translation/src/b2c_translation/generation.py` now uses the absolute string end for token-prefix detection, ignores wrapped URL spans, validates numeric marker slots against complete restored text, prevents independently protected values becoming decimals, and requires actual value rows when recognizing rating fragments. Numeric value/order, completeness, and checkpoint rules remain enforced.

Meaningful regression tests cover URL/note boundaries, URL reflow, omission rejection, separate footnote values, and ordinary prose mistaken for tables. Focused translation and orchestration checkpoint suites: **108 passed**.

## Rollout and resume

Verified zero running generation/translation/audio workflows before replacement. The only running workflow was new-book IngestPDFWorkflow on queue `light`; its worker was untouched.

Built/deployed `b2c-generation-worker:url-validator-20261010`, image SHA256 `47129093e99ffb7b271088341c98c0a43fdbb4e7934ece69ee3adc7e7ef9e6c2`. Runtime module SHA256 matches source `cc16498d6d71e9ec360bec0e807ba12deb9d1841571132f6fb07d48e39fa7001`. Log confirms queue `external-generation` and model `deepseek/deepseek-v4-flash-0731`. Previous worker image retained as `before-url-validator-20261010`.

Resume POST returned202 for `b2c-translate-3c6dfd79c73a383973a0e2d88c2849d4cc1e333727d597dff1268bdcb88b055e`, `force_regenerate=false`. It reuses immutable checkpoints and sends unfinished/invalid units only.

Initial revalidation exposed one additional defective saved unit `book-main:79:0`: actual Arabic omits source note labels9 and10, which the old URL-prefix bug failed to count. The saved checkpoint was not deleted; it is now retried. Therefore accepted initial baseline is764/776 and retry set is the11 IDs above plus79. This count change is stricter validation, not lost persistence.

## Final live result

At2026-10-09T22:52:46UTC the child completed: **776/776, zero failed, missing, retry or suspect units**. Temporal state is COMPLETED and progress event is result_committed.

Final translation artifact key `f814a576b66d27da64ab1f32eb66f42c86d05391f8015c69eb56f1f6444fe408`, SHA256 `b058582310584fb588fa6beb50de6cc64fbffbe7826ddd1e568b0283276e6c76`. Source/target languages remain English→Arabic with DeepSeek v4-flash0731. All12 required units recovered; immutable prior checkpoints retained. No audio was started by this corrective task.

Read back the final `translation.json` and checked every source/target unit with the corrected completeness/structure validator:776 unique output IDs, exact source coverage, zero validation issues; artifact deterministic checks report776 numeric checks. Thirteen previously documented unclear extracted table tokens are retained verbatim rather than guessed. These automated checks establish completeness/structure; they do not claim a comprehensive semantic human review.
