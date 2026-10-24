# Automatic translation unit recovery — 2026-10-09

Scope: B2C generation.py only; accepted original segment IDs, citations, saved checkpoints and final coverage checks are retained. No workflow, live service, inference call or artifact-store object was mutated during this preparation.

## Actual fresh cached responses

Read-only sample of 20 outstanding parent IDs against workflow `b2c-translate-a48866ff04a0e80a73c6be7232bda355cf5cbea7a93709b905c77d00f9b623b3`: progress snapshot 635/776; 24 cache responses and 11 misses. Response-level classification counted 38 item outputs. Existing validator: 13 marker failures, 6 numeric sequence failures, 2 omission flags, 17 valid. Candidate: 16 numeric mismatches, 2 marker order failures, 2 omission flags, 18 valid. No cache miss invoked inference; no diagnostic output was adopted or checkpointed. This sample is not a corpus-wide error distribution.

The root agent personally compared two complete source/target pairs in a local private file (mode 0600):
- `book-main:34:0`, page34: source1370 characters titled AUDIENCE FOR THIS BOOK; model emitted only translated title (16 chars), 30 output tokens with max8192.
- `book-main:128:0`, page128: source1787 characters titled OVERVIEW OF TECHNIQUES; model emitted only translated title (22 chars), 34 output tokens with max8192.

Those outputs omit the body. Output token use is far below the configured cap. Cached `finish_reason=stop` is synthetic: cache-hit reconstruction does not retain original provider finish reason, so it is not independent original termination evidence. Neither source had replacement characters or hyphenated line breaks; wrapped source lines alone are not evidence of corrupted extraction.

`book-main:144:1`, page144: Arabic conjunction attached to a raw numeral (`و10`) caused an otherwise exact ten-number sequence to fail the original word-boundary parser. The candidate recognizes Arabic letter prefixes while preserving English alphanumeric exclusions and the existing suffix policy. Candidate accepted this saved response in offline validation. This does not accept missing, duplicated or reordered numbers.

## Automatic recovery implementation

Mixed markers/raw numerals are restored only with exact full ordered equality. Each surviving marker must match its original numeric slot, including equal repeated numeric values; duplicate, malformed, unexpected and reordered markers remain rejected.

A short uppercase first-line heading is separated before the initial singleton request. Long singleton units are divided before generation. Each source piece preserves every original character; split positions prefer sentence/paragraph boundaries near the middle, with whitespace fallback for wrapped text. Preliminary pieces are at most1200 source characters; failed pieces split further with depth limited to6. Long exhausted ordinary batches receive the same automatic fallback. Translation pieces retain the original public ID but use distinct adaptive cache request namespaces. No operator action is required to split/reassemble.

Every piece must pass numeric and omission checks. Only a reassembled full original unit that passes full-source numeric sequence, completeness and model-size validation can be saved. Independent recovered originals are checkpointed; unsalvageable originals remain explicit failed/missing units. Existing resumed accepted units are not submitted again.

Progress emits fixed error reason codes without source text/provider error payloads.

## Evidence and rollout

Focused command: `/root/.local/bin/uv run pytest tests/unit/translation tests/unit/orchestration/test_translation_partial_result.py tests/unit/orchestration/test_translation_checkpoint_selection.py -q` —59 passed. Tests cover numeric identity/order/mixed markers/Arabic attachment/Latin exclusion, automatic heading separation, bounded proactive/adaptive splitting, rejected numeric/content omissions, original-ID checkpoints and monotonic resume counters.

Isolated candidate: `outputs/translation-adaptive-candidate-20261009/generation.py` SHA256 `0722b4c42307f8845c59ac51e377655fca70c630ce105885f9eecce94fbb404a`.
Runtime baseline SHA256 `ac89f37c59519d624201904bf6c0ae2483b3dc98bef57dc6b1e0ea967a73fd62`.
Base runtime image `sha256:5ae537efdd53568cc9a8090bc8ed7d9e3a97748be8cc4c3df3dd4a18405ffd40`.
Only generation.py is copied into the thin image. Deployment/recovery remains root-coordinated. These checks prove automatic mechanics and strict deterministic rejection; they do not establish expert semantic quality or completion of all776 live units.
