# Targeted translation retry feedback — 2026-10-13

## Decision

Use validator-guided, segment-scoped correction on the existing tencent/Hy-MT2-1.8B route. A failed validation sends the same source segment, the rejected draft, a fixed machine-readable reason code, and only evidence relevant to that reason. The retry prompt asks for the smallest correction and explicitly preserves the rest. Segments already accepted and checkpointed are excluded from the retry payload.

This is an automatic post-edit/retry of an observed defect, not a second general translation pass. Unknown provider errors do not become invented correction instructions. Arbitrary reason text and client-supplied instructions are not trusted.

## Research and model fit

- Tencent's official Hy-MT2 model documentation describes instruction-following translation and the 1.8B/7B/30B family, with chat-template prompting. The existing local route is tencent/Hy-MT2-1.8B, so constrained correction can use the same local model and does not add a provider, download, or independent inference service: https://github.com/Tencent-Hunyuan/Hy-MT2/blob/main/README_CN.md and https://huggingface.co/tencent/Hy-MT2-1.8B
- Research on error-annotated MT post-editing finds that annotation can improve post-edit quality, with results depending on annotation granularity; its recommendation supports concise, typed defects rather than generic “improve this” prompts: https://aclanthology.org/2024.findings-naacl.265/
- Quality-estimation-assisted automatic post-editing highlights the risk of over-correction. The implementation therefore gates correction on deterministic rejection and constrains the edit to the observed category: https://aclanthology.org/2023.findings-emnlp.115/
- MQM separates accuracy, terminology, and linguistic convention defects, which informs the separate reason codes and evidence: https://new.themqm.org/mqm-pillars/typology/
- ISO 5060 explicitly distinguishes evaluation of unedited MT output and post-edited MT output; this pipeline retains validator outcomes and the corrected artifact as separate observable outputs: https://www.iso.org/standard/80701.html

## Implementation

1. Fixed allowlist of reason codes; no free-form validator exception text is sent as an instruction.
2. Per-segment retry_feedback contains a version, reason code, bounded prior draft, and bounded typed evidence. Examples: exact ordered number/marker sequences, selected glossary equivalents, or source/draft bracket counts.
3. The adapter maps reason codes to fixed instructions. Unknown codes, unversioned feedback, and arbitrary evidence keys are ignored.
4. The prompt labels the prior draft as untrusted data, treats source as authoritative, asks for the minimum correction, and forbids rewriting correct meaning or adding information.
5. Existing per-item checkpointing remains in force; a good companion segment remains committed and is not sent on a retry.
6. Existing deterministic validators run again after correction. Invalid items remain deferred/failed rather than being accepted simply because the model was prompted.
7. This does not claim semantic truth or perfect grammar: existing deterministic checks still do not prove every Arabic agreement or discourse choice correct. The feedback loop only narrows machine-detected defects.

## Focused validation

Command:
pytest -q tests/unit/translation/test_generation_adaptive.py tests/unit/translation/test_hy_retry_prompt.py tests/unit/translation/test_generation_resume.py tests/unit/translation/test_contextual_glossary_and_editing.py tests/unit/translation/test_pipeline.py tests/unit/orchestration/test_translation_partial_checkpoint_durability.py tests/unit/orchestration/test_stage_pause_controls.py tests/unit/api/test_stage_pause_controls.py

Result at implementation checkpoint: 135 passed. This includes a regression showing a rejected body segment alone is retried while its accepted title companion stays saved, a deferred retry carries the same reason/draft, and arbitrary injected instruction fields are discarded by the HY adapter.

## Deployment result

- The three live translation workflows were paused at committed batch checkpoints; Temporal showed zero pending activities for each before the worker swap.
- Rebuilt only the B2C HY translation worker and HY adapter, then replaced those two containers. No API, database, storage, audio, or non-B2C container was recreated.
- A stale/zombie old adapter process initially blocked Compose replacement. It exited during the scoped replacement attempt; the failed temporary container alone was removed, and the normal Compose replacement then completed.
- Cold HY startup exceeded the prior 180-second health start period while loading the 3.9 GiB model file. The compose start period is now 900 seconds for future cold starts. The measured cold model startup was about 9m10s; the current container became healthy without another restart.
- HY /health returned ready for tencent/Hy-MT2-1.8B; API /health returned HTTP 200; the translation worker started on the HY task queue with the expected model.
- Resumed the same three workflow IDs by their existing durable controls/signals. Progress after resume: Book 1 body 79/776; Book 1 appendix 131/888; Book 2 body 74/2620 with seven previously deferred batches. The Book 2 body showed an active segment after resume; no saved segment was regenerated solely for deployment. A fourth workflow disappeared from Temporal during the safe-pause check and was not recreated.
- No standalone model inference was used as a deployment smoke test. Automated tests validate the feedback path; the live translations will use it only when a deterministic validator rejects a segment. The current progress snapshot still reports an earlier numeric-marker failure; that does not prove a post-deployment correction has completed yet.
