# Gemma 4 readiness and task controls — 2026-10-09

Scope: existing local Gemma4:e4b and B2C source/evidence only. This review ran no inference, downloads, or service operations. Existing results are preserved.

## Verified local model and results

The tested model is registered in the isolated Ollama Model Lab at `127.0.0.1:11435`, with files under `E:\B2C-Model-Lab` (`/mnt/e/B2C-Model-Lab`). `STATUS.md` records verified model digest `dc35e8d9c6061baa6f0fa870975ab6932e2542b579b13ea0f199fa4bb7300c9c`. Saved runtime metadata in `reports/gemma4-installed-capabilities.json` reports GGUF Q4_K_M, gemma4 architecture, 7,518,069,290 parameters, 131,072 advertised context, and completion/vision/audio/tools/thinking capabilities. These advertised capabilities are not all tested integrations: audio transport and function-tool execution were not demonstrated by the inspected reports.

Evidence:
- `reports/gemma4-expanded-summary.json`: 12 cases, six QA cases/twelve questions; 6/6 valid JSON, 12/12 exact source quotations and IDs, warm mean 39.42 s per two-question case. Five controlled short translations averaged 11.28 s. A real-book passage reversed subjective into objective; source fidelity cannot be inferred from JSON success.
- `reports/gemma4-glossary-retry.json`: separate retry with mandatory terms subjective approach = المنهج الذاتي, frequentist approach = المنهج التكراري, base rates = المعدلات الأساسية corrected the observed reversal in one case. It used 640 output-token allowance; 323 output tokens, stop termination, 213.72 s wall / 62.96 s decoding. Original failure retained.
- `reports/flores200-gemma4-controlled/gemma4-e4b.json`: 100/100 deterministic length-stratified FLORES-200 devtest EN->AR sentences completed, zero request errors/truncated outputs, chrF++ 56.77696, warm mean 14.76 s, ~4.35 decode tokens/s. Public reference chrF++ does not establish semantic completeness or book-sized quality.
- `benchmarks/qa-grounded-v1/manifest.json` prepares 45 scopes/90 questions per model with strict schemas and an independent judge. Its saved manifest says prepared-not-run; do not report that expanded course-QA evaluation as completed merely because runner code exists.

## Tested settings that can be reused

| Task | Demonstrated configuration | Evidence and limit |
|---|---|---|
| Grounded QA | Ollama `/api/generate`; `format=json`; temperature 0; seed 42; think false; num_ctx 4096; num_predict 320; num_thread 4; concurrency one via lab lock; keep_alive 5m | `runtime/gemma4_expanded.py`; explicit source-only prompt, exactly two questions, copied evidence and exact supplied IDs. JSON mode was tested, not strict JSON-schema generation or tool calls. |
| Short translation | temperature 0; seed 42; think false; 4096 context; 320-token allowance; source-only faithful-translation prompt preserving numbers/negation/uncertainty | `runtime/gemma4_expanded.py`; one real-book meaning reversal establishes need for stronger term/semantic checks. |
| Glossary retry | mandatory small scoped glossary; 4096 context; 640-token allowance; temperature 0; seed 42; think false | `runtime/gemma4_glossary_retry.py`; correction demonstrated in one case only. |
| Reference translation evaluation | 8192 context; 320-token allowance; four threads; temperature 0; seed 20261008; think false; keep_alive 5m | `runtime/gemma4_flores_eval.py` and completed 100-sentence controlled report. This budget fits short sentences and must not be copied unchanged to full pages. |

Four threads were the test setting while production continued, not proof of optimal hardware use. Cold loads affected some wall times; compare warm decode/prefill/load separately. Keeping one model warm avoids repeated cold loads, while alternative models may be activated on demand.

## Existing B2C controls to connect

`packages/llm_gateway/src/b2c_llm_gateway/client.py` already implements structured responses from Pydantic JSON schema with validation/retries. `packages/pedagogy/src/b2c_pedagogy/qa_generation.py` has exact-count candidate/judge schemas, bounded source packing, quote options, source ID checks, duplicate rejection, and source-entailment judge calls. These project controls exist; their end-to-end Gemma route is not established by the Ollama JSON-mode diagnostics.

`packages/translation/src/b2c_translation/generation.py` accepts a glossary, page/lesson-bounded batches, numeric placeholders/restoration, exact returned segment IDs/cardinality, non-empty output, omission triage, and checkpoint resume. Its severe-compression ratio gate is conservative triage, not proof that all meaning survived. Provider routing must retain these checks when Gemma is selected, rather than using the bare lab prompt as the application pipeline.

## Proposed task equipment, not yet proven in this model route

1. Connect Gemma to the project's actual gateway with strict candidate/translation schemas. Verify transport compatibility and refusal/length behavior against the saved fixtures before marking the operational option ready; JSON mode alone does not prove schema conformance.
2. Keep book/source retrieval outside unconstrained model access: give each lesson only authorized spine/supplementary chunks, quote options and relevant glossary terms. Preserve source IDs, page references, selected model and prompt/schema versions in persisted output.
3. For translation, allocate output tokens from source size and preserve paragraph/segment boundaries. Reject length termination; split only failing oversized units and retry those units while reusing accepted checkpoints. The advertised 128K context is not a recommendation to send whole books or allocate that KV cache on this CPU host.
4. Add term-aware completeness checks: mandatory glossary consistency, numbers/entities, negation/modality, source-to-target paragraph alignment and independent adequacy flags. Never treat character ratio or same-model self-judgment alone as semantic approval.
5. For QA, combine deterministic schema/quote/duplicate checks with the existing independent judge boundary. Cache validated items per lesson and retry shortages/failures without re-creating accepted questions. A strict schema guarantees shape, not answer truth.
6. Function calling is an advertised runtime capability but no inspected local tool-call execution test exists. For these tasks, the first practical equipment is bounded retrieval, deterministic validators, glossary and strict schemas. Optional tools should have explicit schemas and authorization/source scopes before execution; do not claim arbitrary tools are already attached.
7. Evaluate thread count, context/batch size and keep-alive under fixed workload before declaring speed improvements. Preserve the established settings as baseline. Audio input capability does not mean TTS generation; Fasih remains the separately implemented speech path.

Readiness decision: existing Gemma4 model and meaningful local QA/translation evidence are present. It is suitable as a prepared selectable experimental route once gateway/worker connections pass focused checks. Existing evidence does not justify claiming course-scale QA completion, perfect translation, or validated tool execution. No new models were added to an operational selector by this review.

## B2C operator-app preparation — 2026-10-09

- Full-disk inventory found the previously evaluated Gemma4 E4B Q4_K_M under `E:\B2C-Model-Lab\ollama-models` (`/mnt/e/B2C-Model-Lab/ollama-models`), with the exact `gemma4:e4b` manifest and previously recorded digest. No second model was downloaded or copied.
- The API catalog exposes the existing DeepSeek/OpenRouter route and Gemma4 as selectable worker routes. DeepSeek selection performs only a read-only `/models` check; Gemma4 is marked `limited_test` and is advertised only when `B2C_GEMMA4_ENABLED=true`. The base deployment keeps the model preference unset and Gemma disabled.
- `docker/compose.gemma4.yml` adds an opt-in, isolated Ollama runtime and generation worker. The tested store is mounted read-only. The Gemma worker has its own Temporal queue and internal-only networking; it has no external API key or external network. This makes model switching a route to a different worker, rather than relabeling the currently configured worker.
- Model defaults are stored under the B2C data disk at `state/model-preferences/model-preferences.json`; the empty initial file is owned by API UID 10001. The APK settings screen shows model type, test status, caveats, and enables default selection only when the API reports a connected task route. Java compilation passed; the APK has not been rebuilt or distributed.
- Verification: Python source compilation passed; `tests/unit/api/test_model_lifecycle.py` passed 12/12; `docker compose ... --profile gemma4 config -q` passed with B2C project name and mounted data disk. No services or models were started, and the ongoing book workflow was not touched.
- **Not yet runtime-verified:** container can read the Ollama manifest and load the model; OpenAI-compatible gateway requests with the project's strict output schema; one real QA/translation task through the dedicated queue; persistence of a selected default after API restart. Keep Gemma4 marked experimental until those checks pass after the active book run is safe to pause.

## Current RAG choice boundary

The production default remains lexical retrieval. The BGE-M3/hybrid and EmbeddingGemma2 results are evaluation evidence, but their runtime endpoints are not connected in the deployed worker. LightRAG remains an isolated study with an incomplete persistent index, and HippoRAG is not a production route. These options may be shown with their evaluated status and an explicit disconnected state; they must not be presented as operational choices or change the active route without a separate integration and canary.