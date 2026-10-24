# Phase 6 — Optional HippoRAG 2 comparison

Date: 2026-10-04 (Africa/Cairo)  
Status: isolated security-patched runtime prepared; **live comparison not run / not passed**.

## Scope and guardrails

Phase 6 evaluates whether HippoRAG 2 improves retrieval over the frozen Phase 3 baseline. It is optional and must stay outside the default retrieval path. This implementation adds only a dependency-neutral adapter boundary; it does not install HippoRAG, execute model/provider calls, deserialize pickle artifacts, or enable any service.

## Implemented slice

- `packages/kg/src/b2c_kg/graph_retriever.py` defines `GraphBackend`, `GraphRetriever`, `GraphHit`, explicit `GraphAdapterConfig(enabled=False)`, and a working adapter that resolves graph hits to the caller-provided authorized `StructuredChunk` records.
- The adapter rejects duplicate chunk IDs in its authorization input, rejects blank queries/invalid limits, ignores unknown or duplicate backend hits, and applies book/role filters before returning results. Returned objects retain the original B2C chunk provenance (book, role, pages, block IDs).
- HippoRAG itself is not wired: a pinned and security-reviewed shim must map its supported API to this narrow backend protocol. Keeping that shim external prevents optional package imports and unsafe deserialization from entering the main worker.

## Executed tests

Command: `.venv/bin/pytest tests/unit/kg/test_graph_retriever.py tests/unit/kg/test_concepts.py -q`  
Result: **13 passed**.

- Disabled-by-default mode makes no backend call — PASS.
- Authorized chunk/provenance mapping; unknown hits, out-of-scope role/book, and duplicate hits excluded — PASS.
- Duplicate authorized IDs fail closed — PASS.
- Invalid query and top-k rejected before backend call — PASS.
- Existing Phase 4 concept relation tests — PASS.

## Still required before any Phase 6 gate

- Pin and review HippoRAG 2 release/source and dependencies, licensing, network behavior, and unsafe `eval()`/pickle paths. No install, import, or untrusted artifact load until this review is complete.
- Implement a version-specific isolated backend shim and prove it returns B2C chunk IDs without losing citations/roles.
- Freeze Phase 3 corpus/config and use identical queries, candidate count, and evaluation metrics for baseline versus HippoRAG.
- Measure retrieval gains and regressions, citation/provenance correctness, source-role separation, latency, peak RSS, storage, restart behavior, and tenant/course isolation.
- Keep `enabled=False` in normal configuration; no production route or orchestration wiring exists.

## Gate decision

**NOT RUN / NOT PASSED.** This is an adapter boundary with unit evidence, not an evaluation of HippoRAG 2 and not a recommendation to adopt it. Next action is a source/security review followed by a version-pinned isolated shim only if that review is acceptable.

## Pinned source, isolated bridge, and dependency recheck (2026-10-04)

The complete WSL checkout was located at `/root/projects/book2course`; the earlier Windows-visible `C:\root\projects\book2course` was only a partial mirror. Canonical Phase 6 requirements were read from `docs/05-PHASES.md`, `docs/06-DELIVERY-PLAN.md`, `docs/PROJECT-STATUS.md`, and `docs/02-CONTRACTS.md`. Phase 6 is optional, compares against the frozen Phase 3 baseline, and must stay behind `GraphRetriever`; it is not a required production route. The existing two-book, 37-chunk corpus and 50 automatically labeled qrels are on the mounted B2C data disk. Those labels remain diagnostic/non-gold.

### Pinned upstream source and static review

- Pinned official repository commit: `2bfd831417202b49cda9da7973e141a456e16872`; source `pyproject.toml` reports `2.0.0a5`. Codeload archive SHA-256: `cd8b241c3d8e1ccca69b47618a651cb38dcac1a0b84ec495794c69686278f64d`. The archive is preserved at `/mnt/wsl/b2cdata/phase6-assets/hipporag-2.0.0a5-2bfd831417202b49cda9da7973e141a456e16872.tar.gz`; the pin record is `eval/suites/phase6/upstream-lock.json`. PyPI latest published prerelease remains `2.0.0a4`; the selected a5 source is a commit-pinned unreleased development snapshot, not a published wheel.
- The exact snapshot includes an MIT license. A static scan of its tracked Python source found no direct built-in `eval()` or `exec()` call; `self.embedding_model.eval()` is the normal model-evaluation-mode call. This resolves the documented `eval()` question for this one pinned source snapshot only.
- The graph persistence risk is confirmed: `HippoRAG.initialize_graph()` calls `ig.Graph.Read_Pickle()` on `graph.pickle`. The exact upstream dependency pins `python-igraph==0.11.8`; official `python-igraph` 0.11.8 documentation identifies this as Python pickled format. Treat the graph as executable untrusted input: only create/load a fresh private artifact in a disposable isolated run; never accept or mount a user/shared graph pickle.

### Dependency security result

- `pip-audit` resolved the upstream `requirements.txt` in an ephemeral Python 3.12 container (source package itself was not installed or imported). It exited 1 with **95 advisory rows across three direct pinned packages**: `litellm==1.73.1` (30 rows), `torch==2.5.1` (22), and `transformers==4.45.2` (43). The rows include aliases/overlap and are not 95 distinct exploit paths. The exact report and hash are on the data disk: `/mnt/wsl/b2cdata/phase6-assets/pip-audit-columns.txt`, SHA-256 `ae07fda14a2987f31a5aa84fa546b4fcb526cc67211889ad3348b6929b7983fb`.
- The host's Python 3.14 cannot resolve the upstream Torch pin, which is why the scan was performed in Python 3.12. Upstream also lacks a complete transitive dependency lock; several requirements are unpinned. No dependencies were installed into the B2C environment. **Do not execute this upstream snapshot with its declared pins.** A separate compatibility-tested patched dependency set and complete lock are required before an isolated HippoRAG run.

### B2C bridge implementation and checks

- `eval/suites/phase6/hipporag2_bridge.py` is a version-specific bridge in the isolated evaluation harness, outside the default worker package. It converts frozen B2C chunks to HippoRAG `Chunk` objects only when the caller supplies the pinned type, using opaque source IDs that bind course/book/role/chunk. It carries page/block/section provenance and verifies returned text and metadata before yielding chunk IDs; it never invents provenance from matching text.
- Its optional runtime loader requires explicit LLM and embedding model IDs and endpoints, named secret environment variables, a new scratch directory below a dedicated isolated data root, and the exact `2.0.0a5` package version. It sets both rebuild-from-scratch flags and imports HippoRAG only after validation. This guards accidental use of the default OpenAI route and reuse of untrusted pickle state. The process still must run only inside the disposable Phase 6 environment.
- `GraphHit` and `GraphAdapterConfig` now carry an optional fixed scope. A scoped backend requires a matching scope-bound adapter, and results from another scope are dropped. Existing behavior remains disabled by default.
- `eval/suites/phase6/run_hipporag2.py` now provides a reproducible isolated ranking runner compatible with the Phase 4 comparison schema. It passes questions only (never qrels) to the retriever, requires existing frozen input files and dedicated output/scratch roots, rejects output/input symlinks and invalid top-k before indexing, and publishes immutable result artifacts atomically. It records commit/model IDs, input hashes, query/index timing, and peak RSS. It is prepared but deliberately not executed until a patched, audited dependency environment exists.
- The frozen 37-chunk benchmark was converted through the bridge without installing/importing HippoRAG. All 37 rows lack `block_ids`; the bridge represents that source limitation as an empty block-provenance list instead of fabricating IDs. Page and section provenance remain carried and checked.
- Added bridge and runner unit tests; command `.venv/bin/pytest tests/unit/eval/test_phase6_bridge.py tests/unit/eval/test_phase6_runner.py tests/unit/kg/test_graph_retriever.py tests/unit/kg/test_concepts.py -q` returned **26 passed**. This covers exact chunk/provenance mapping, missing block provenance, duplicate-content rejection, version/config guards, wrong scope/role/page rejection, questions-only runner selection, deterministic corpus hashing, top-k validation, and composition with the B2C `GraphRetrieverAdapter`. These are adapter/runner contract tests, not an execution of HippoRAG.

### Current Phase 6 decision and exact remaining gates

**NOT RUN / NOT PASSED.** Useful preparation and the B2C bridge are implemented, but the upstream dependency set currently fails security review. The HippoRAG source/package was not installed/imported/executed; no model endpoint was called and no container or LightRAG service was restarted or changed. Phase 4 assets/reports were only read for the comparison contract; the separate AI Company project was not accessed.

Before a live comparison: (1) choose patched versions for the three vulnerable packages and prove API compatibility with this commit; (2) produce a complete hash-locked Python 3.12 environment and rerun dependency audit; (3) build a dedicated container with no production mounts/secrets and only an explicitly configured no-cost model route; (4) run the bridge smoke on the frozen 37 chunks, then generate rankings for the same 50-query set and compare to the Phase 3 baseline; (5) record latency, peak RSS, disk growth, restart, and course-isolation evidence. Even if the automated diagnostic metrics improve, the current auto labels are not gold and cannot support a gold-quality adoption claim.

### Primary source references

- HippoRAG pinned source: https://github.com/OSU-NLP-Group/HippoRAG/tree/2bfd831417202b49cda9da7973e141a456e16872
- HippoRAG pinned graph deserialization call: https://github.com/OSU-NLP-Group/HippoRAG/blob/2bfd831417202b49cda9da7973e141a456e16872/src/hipporag/HippoRAG.py
- python-igraph 0.11.8 API and tutorial: https://github.com/igraph/python-igraph/blob/0.11.8/src/igraph/__init__.py and https://github.com/igraph/python-igraph/blob/0.11.8/doc/source/tutorial.rst
- Python pickle security warning: https://docs.python.org/3/library/pickle.html


## Patched isolated runtime and live-run readiness (2026-10-04)

### Dependency remediation and evidence

The upstream pins were replaced only in the disposable Phase 6 copy; the B2C runtime lock and service images were not changed. The affected direct pins were `litellm==1.73.1`, `torch==2.5.1`, and `transformers==4.45.2`, with **95 pip-audit advisory rows** (30/22/43 respectively; alias/overlap rows, not 95 distinct vulnerabilities). The scan report is `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/upstream-pip-audit.json`.

The isolated compatibility set now uses LiteLLM 1.104.0, Transformers 5.18.0, Torch 2.14.1 CPU, OpenAI 2.54.0, and Sentence Transformers 6.1.0. Its locked graph contains 101 packages and hashes; the lock is `eval/suites/phase6/requirements-py312-linux.lock` (SHA-256 `78fc83e8f696d29c42fc5d28201ebe63cd5cbf030747162ff276b7ca1288c2c2`). The wheelhouse contains 88 Linux wheels (404 MiB); its per-wheel SHA-256 manifest is `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/wheelhouse-py312-linux.sha256` (manifest SHA-256 `2c3c20891bd03ba39d73bae9e33bb00d639fb80f38b30f9f8b689ab28e17866a`).

The patched candidate dependency resolution completed `pip-audit` with **no known vulnerabilities and no ignored findings** across 105 resolved packages. The saved report is `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/patched-pip-audit-shadow.json` (SHA-256 `85bfa6e596d01059490f72d429b6a0f72be772fd5e8b8c07922f139d03965bf3`). Audit caveat: pip-audit cannot resolve the official PyTorch CPU local-version wheel (`2.14.1+cpu`) through PyPI, so the clean audit was run against the same upstream release version `torch==2.14.1` from PyPI. The actual installed CPU wheel was fetched from the official PyTorch index and hash-pinned in the lock. No claim is made that the exact CPU wheel was itself scanned by pip-audit.

**Exploitability triage for the disposable intended path:** the LiteLLM advisory text includes proxy/admin surfaces such as `/config/update`, proxy authentication/role management, and proxy request routing; the Phase 6 harness does not start a LiteLLM proxy listener and calls the OpenAI-compatible client against one explicitly configured model endpoint. Several Torch/Transformers advisories concern deserializing attacker-controlled serialized model/index artifacts or loading remote/custom model code. The sandbox mounts the known local BGE-M3 directory read-only, enables offline mode, and creates a fresh private HippoRAG graph in scratch; it never imports a user-provided graph pickle. The BGE checkpoint includes `pytorch_model.bin`, so this is a narrowed/trusted-artifact use assumption, not proof that pickle parsing is intrinsically safe. No individual CVE is declared universally unexploitable from this review. All three direct packages were upgraded and the resolved candidate graph was audited clean rather than relying on reachability assumptions.

The 66 unique upstream advisory identifiers from the 95 rows were: LiteLLM — CVE-2026-35029, CVE-2026-35030, CVE-2026-49468, CVE-2026-47102, CVE-2026-47101, CVE-2026-12795, CVE-2026-59820, CVE-2026-59822, CVE-2026-59819, CVE-2026-59821, CVE-2026-84377, CVE-2026-37004, CVE-2026-12798, CVE-2026-12773, CVE-2026-59823, CVE-2026-12799, CVE-2026-12772, CVE-2026-12771, CVE-2026-12796, CVE-2026-12797, GHSA-69x8-hrgq-fjj8; Torch — CVE-2025-2953, CVE-2025-3730, CVE-2025-32434, CVE-2025-55553, CVE-2025-55554, CVE-2025-55557, CVE-2025-55552, CVE-2025-55560, CVE-2025-55558, CVE-2025-46148, CVE-2025-55551, CVE-2026-4538, CVE-2025-2148, CVE-2025-2149, CVE-2025-2998, CVE-2025-2999, CVE-2025-3000, CVE-2025-3001, CVE-2026-24747; Transformers — CVE-2025-14929, CVE-2024-12720, CVE-2025-1194, CVE-2024-11392, CVE-2024-11394, CVE-2024-11393, CVE-2025-2099, CVE-2025-3263, CVE-2025-3264, CVE-2025-3777, CVE-2025-3933, CVE-2025-5197, CVE-2025-6638, CVE-2025-6051, CVE-2025-6921, CVE-2026-1839, CVE-2025-14926, CVE-2025-14930, CVE-2025-14920, CVE-2025-14921, CVE-2025-14924, CVE-2025-14927, CVE-2025-14928, CVE-2026-4372, CVE-2026-5241, CVE-2026-9856.


### OpenRouter canary and request-policy pin

The Phase 4 canary returned model `deepseek/deepseek-v4-flash-0731` using 561 prompt tokens and 578 completion tokens (0 reasoning tokens); it completed in 2.86s with OpenRouter-reported cost `$0.0007483672`. Nine of nine extracted entities matched exact spans; four of five relations validated. The request used `max_tokens=4096`, `reasoning_effort=none`, provider `sort=price`, `allow_fallbacks=false`, and `max_price` ceilings of `$0.0152/M prompt` and `$1.28/M completion` tokens. OpenRouter did not include a provider name in the Chat Completions usage fields; therefore the policy pins price sorting and disables fallbacks, rather than claiming one named provider. The exact non-secret request policy is `eval/suites/phase6/openrouter-deepseek-v4-flash-0731.json` (SHA-256 `b35f233d39e22c466c0018b60887478a75a073b45596969a160f2d5c17b630ad`). The mounted read-only BGE-M3 files were hashed into `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/bge-m3-model.sha256` (manifest SHA-256 `4aee6f85056b5b9c0185aa4fca16eb28e47b6b1c2b03599f70df01953fbede3c`). The sanitized canary record is `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/phase6-model-config-canary.json` (SHA-256 `0cb751ab708a3a506d3b41cfd5175d077785f1be3d011a090f84e5ec01077b33`). Neither artifact contains the API key. A mock-transport smoke of the actual isolated SDK plus usage recorder reproduced those prompt/completion/cost values without contacting OpenRouter. The canary cost is only a unit observation; it does not predict Phase 6's total cost.

### Runtime verification and readiness

- Disposable container: `b2c-phase6-hipporag-sandbox`, on the B2C internal network, bounded to 16 CPUs and 48 GiB RAM. It has no production secrets; only frozen source/corpus and read-only BGE-M3 are mounted. The source and model mounts are read-only; new graph/pickle state is created under a dedicated scratch mount.
- Offline hash-verified install succeeded; `pip check` reports no broken requirements. Imports succeeded for HippoRAG 2.0.0a5, LiteLLM 1.104.0, Torch 2.14.1+cpu, Transformers 5.18.0, and Sentence Transformers 6.1.0. The local BGE-M3 model loaded (391/391 tensors) and HippoRAG initialized without an inference call.
- Earlier test snapshot before the Top-Tools policy update: **30 passed**, including pinned OpenRouter policy validation, provider-body serialization, and token/cost capture. A separate mock-transport check against the sandbox's installed OpenAI SDK confirmed `provider`, `reasoning_effort`, and `max_tokens` are serialized at the request top level; it sent no live request.
- The paired lexical baseline ranking was prepared from the frozen two-book/37-chunk corpus and all 50 development queries: `/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/phase6-baseline-lexical-50.json`. Corpus fingerprint: `73e325e08b1b523a6cd0ecb335d83ac59ea8007da75ee2d4a08a0c8217637815`; query dataset SHA-256: `4361df96655fb0a2ba70466ca16936c67fc409c74368fbc44925270bd19d8b99`. Rescoring this prepared ranking exactly matched all saved Phase 3 lexical metrics at k=1/3/5/10. Existing labels are automatic, diagnostic, and not gold.

### Historical decision (superseded)

The earlier OpenRouter DeepSeek canary and the decision to wait for a shared paid account are historical. The active Phase 6 route is Top-Tools-Ai under the policy below; no paid provider fallback is configured, and HippoRAG indexing/retrieval has not started. Phase 6 will wait for the root coordinator to release Top-Tools after Phase 3/5 work, then run the frozen 37 chunks and 50 questions and compare with the prepared baseline. No production adoption or quality claim is justified from automatic labels.

## Top-Tools-Ai route prepared (2026-10-04; live Phase 5 run has priority)

Phase 6's next run is configured to use the exact Top-Tools-Ai route requested for the current work, rather than the earlier OpenRouter canary. The sanitized policy is eval/suites/phase6/top-tools-ai-policy.json (SHA-256 5f69d64379fb5989f7e30a852951aec34d8712aa239a0de7567dded61f9c5b46): base URL https://top-tools-ai.com/api/v1, model ID Top-Tools-Ai, max_tokens=4096, temperature 0, no fallback, and an 8,000,000-token daily ceiling with prior shared usage included, leaving a buffer below the provider's documented 10,000,000-token daily cap. It intentionally contains no secret, reasoning_effort, or OpenRouter-specific provider body. The provider documentation is [Top Tools AI Docs](https://top-tools-ai.com/docs).

The gateway canary was separately reported successful: structured JSON call, 244 input/52 output tokens, parse passed, 12.6 seconds; simple chat 247 total tokens/9.39 seconds. This is transport/configuration evidence only, not HippoRAG quality evidence. The provider's response did not report cost, so the Phase 6 usage recorder stores cost_usd: null and does not infer a price. The API key remains outside the repository at /root/.config/book2course/top-tools-ai-api-key (mode 0400); the dedicated sandbox mounts it read-only, and no process has read or used it for a request here.

run_hipporag2.py now validates this provider policy separately, strips OpenRouter-only routing/reasoning fields, sets the requested top-level max_tokens and temperature, records tokens and unknown cost, and conservatively reserves prompt/output quota before each call. A request that cannot fit under the provider's 8,000,000-token daily ceiling including prior shared use is stopped before network access and the runner emits a quota_stopped artifact with completed work and usage metadata. Every successful response must also return the exact pinned model ID; a model mismatch stops the run and is recorded as an error, preventing silent paid-model substitution. A persistent per-provider ledger records prior usage, actual prompt/completion tokens, outstanding reservations, and remaining budget. Top-Tools is seeded with 543 known canary tokens (296 structured plus 247 simple-chat); update this with any additional same-day Top-Tools calls before release. Mercury prior usage is currently seeded at 367,074 tokens for Phase 5 failed-call reservations (reported API usage was zero); root must add any earlier Mercury canary usage and the final Phase 4 aggregate (44/50 labels; inference stopped at 2M cap) before release. The root coordinator must seed it with already-reported Phase 3/4/5 usage before the first Phase 6 request; the runner rejects a lower prior-usage value on later retries. A provider quota/rate response also stops the run, records a sanitized stop category/status, and never triggers fallback. Phase 6 issues queries serially and caps HippoRAG OpenIE indexing workers at four, below the provider's published five-concurrent-request limit.

Validation after the persistent quota-ledger change: 41 passed across the Phase 6 runner, isolated bridge, graph retriever, and graph concept tests. Added checks cover exact Top-Tools policy application, rejection of fallback/nonzero temperature, persistent cross-run token accounting, crash-safe conservative reservations (including provider 400 failures), monotonic prior-usage enforcement, pre-call quota stop, pinned response-model enforcement, and missing-cost serialization. A no-network mock of the installed OpenAI SDK also verified the actual JSON body contains model=Top-Tools-Ai, max_tokens=4096, temperature=0, and omits reasoning_effort/provider fields. Container DNS resolution to top-tools-ai.com succeeds. A separate idle container now carries the patched Python 3.12 runtime; pip check is clean, the BGE-M3 model loads offline, HippoRAG 2.0.0a5/OpenAI 2.54.0/Torch 2.14.1+cpu imports pass, and the secret file is mounted read-only at /run/secrets/top_tools_ai_api_key; no process has read it and no Top-Tools API call has been sent by Phase 6. A launch script is staged outside the repository at /mnt/wsl/b2cdata/phase6-assets/runtime-state/top-tools-scratch/launch-phase6-toptools.py (SHA-256 c8e25a025a06b4b9ab6c2737b490881d6586f9814ae288db3ff1841c70891bde); it selects the frozen 37 chunks and all 50 development questions, uses the dedicated scratch/output roots, reads the secret only at execution time, and compares completed retrievals to the copied frozen lexical baseline at k=1/3/5/10. Its current SHA-256 is c8e25a025a06b4b9ab6c2737b490881d6586f9814ae288db3ff1841c70891bde.

Current execution state: Phase 4 stopped T-IDX-02 inference at its 2M-token hard cap before query 45 (44/50 labels emitted) and is evaluating offline; Phase 5 stopped retries after HTTP 400 while diagnosing the route. Phase 6 has done independent preparations only; no HippoRAG inference/indexing has begun. Wait for Phase 3/5 work and an explicit Top-Tools quota/endpoint release from the root coordinator; then run the frozen corpus and query set and compare against the saved lexical baseline. Because all current qrels are automatic/non-gold, that comparison remains diagnostic and cannot be represented as a human-gold acceptance result.


## Inception Mercury-2 free-route preparation (2026-10-04)

A second, fully separate policy is staged for the free Inception Mercury-2 API route in eval/suites/phase6/inception-mercury-2-policy.json (SHA-256 1c54fcdaa90df1a06398f24efe5de63f47dd15ff78a1b07f8897497f56b6f29a). It pins model mercury-2, base URL https://api.inceptionlabs.ai/v1, max_tokens=4096, temperature=0, no fallback, and a 10,000,000-token cumulative ceiling, including all prior provider usage. The official [Inception models page](https://www.inceptionlabs.ai/models) documents the OpenAI-compatible endpoint/model and free tokens for new API keys; the harness uses the requested conservative 10M ceiling and never treats absent cost data as zero. No request was sent.

The Mercury key remains outside the repository at /root/.config/book2course/inception-labs-api-key (mode 0400) and is mounted read-only only in a dedicated idle Phase 6 container. Its patched runtime imports the same audited HippoRAG/Python stack. A separate launch script is staged outside the repository at /mnt/wsl/b2cdata/phase6-assets/runtime-state/mercury-scratch/launch-phase6-mercury.py (SHA-256 ccebc12ebffa8d03431dbf8711cdb95692c13c914967996f1c7be9dd06fa5318); it reads the secret only at execution and compares completed retrievals to the copied lexical baseline at k=1/3/5/10. Top-Tools and Mercury policies/keys/output stores remain separate, and neither is an automatic paid fallback for the other. Use remains paused until the root coordinator releases the shared evaluation window after Phase 3/5 work.

## Phase 6 DeepSeek comparative run staged (2026-10-05)

Phase 4 released its first Mercury evaluation at 44/50 auto-judged questions; reported Mercury usage was 1,619,080 tokens, with 1,989,089 conservatively accounted against its 2,000,000 cap. Its 44-query retrieval diagnostic on the frozen 37-chunk corpus reported lexical R@10/MRR/nDCG 0.9767/0.6875/0.7589, dense 0.9535/0.8036/0.8398, hybrid 1.0000/0.7929/0.8440, and hybrid+reranker 1.0000/0.8605/0.8962. These are automatic-label diagnostics, not human-gold results.

A separate Phase 6 DeepSeek V4 Flash policy is now staged at `eval/suites/phase6/openrouter-deepseek-v4-flash-0731-capped.json` (SHA-256 `b6ffccbd22ccbeb99652a20f1c877ce63861da79fc72c12ba4916a6516fafe51`). It pins `deepseek/deepseek-v4-flash-0731`, OpenRouter, `reasoning_effort=none`, 2,048 max output tokens, price sorting, disabled fallbacks, and ceilings of $0.0152/M prompt and $1.28/M completion tokens. The persistent token ledger is limited to 190,000 tokens, including the recorded 1,139-token DeepSeek canary. Charging every token at the larger completion ceiling gives a conservative maximum of $0.2432, below the Phase 6 $0.25 cap. A request reservation is charged before each call; over-limit requests stop before network access. Provider-reported cost is recorded when returned; missing cost remains `null`.

The run is prepared on the same frozen 37-chunk corpus and Phase 4's 44-query system-judged set. Dataset SHA-256 is `ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65`; 6 of the original 50 questions remain unjudged. The baseline for these 44 queries was filtered from the frozen 50-query lexical ranking and rescored; its R@10/MRR/nDCG exactly reproduce Phase 4's lexical result. A Phase 6-only comparator accepts the system-evaluated provenance while keeping `gold_eligible=false`; self-comparison validates the 44-query/corpus linkage. A second baseline adapts Phase 4 LightRAG's captured citation order for the same 44 IDs (880 citation slots, all within the frozen corpus); no Phase 4 data/service was changed.

A dedicated container `b2c-phase6-hipporag-deepseek` is ready on the isolated `b2c-phase6-openrouter-egress` network, limited to 16 CPUs/48 GiB. Source, corpus, BGE-M3, and repository mounts are read-only; the OpenRouter secret is mounted read-only at `/run/secrets/openrouter_api_key` and is read only when the launcher executes. The container has not sent a Phase 6 provider request. Python 3.12.14, HippoRAG 2.0.0a5, OpenAI 2.54.0, Torch 2.14.1+cpu, Transformers 5.18.0, `pip check`, and OpenRouter DNS all passed. Focused tests: **42 passed**; `git diff --check` clean.

The launcher and its SHA-256 (`66f87aadb1b6e4900dd3f9f0d651995e64c3bbb1d25391c0c5771008900aa7d0`) are outside the repository at `/mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-scratch/launch-phase6-deepseek.py`. This was the staged state before the live run recorded below.

### Live isolated run and outcome (2026-10-05)

The isolated container completed HippoRAG indexing over the frozen 37 chunks and produced 44/44 rankings for the matching auto/system-evaluated query set. Indexing took 193.68 s, mean retrieval latency was 2.112 s/query, and peak RSS was 2,783.3 MiB. The index reported 37 passage nodes, 406 phrase nodes, 395 extracted triples, and 1,065 total graph triples. Source and page attribution integrity were 1.0 at all evaluated cutoffs.

The first live attempt failed when two NER responses were malformed. A bounded strict-JSON retry repaired those two from an independent recovery cache; a later triple response was truncated at 4,096 tokens and repeated the same die entities. The runner now retries only malformed/truncated or excessively repetitive triples once, with a strict JSON response capped at 1,024 tokens and 12 distinct triples. It never fabricates entities or relations. On the completed run, the two strict triple retries yielded one valid empty result and eight explicit triples; all 37 passages were indexed. Focused Phase 6, bridge, and graph adapter tests: **42 passed**; `git diff --check` clean.

At k=10 on the 44-query diagnostic set, HippoRAG scored R@10 0.9767 / MRR 0.7209 / nDCG 0.7844. The lexical baseline scored 0.9767 / 0.6875 / 0.7589; Phase 4 LightRAG citation replay scored 0.9535 / 0.8036 / 0.8398. HippoRAG improved MRR/nDCG over lexical by 0.0335/0.0254, but was below LightRAG by 0.0827/0.0555 despite +0.0233 recall. One of the 44 queries is unanswerable; every system retrieved something for it, so the measured false-positive rate is 1/1 and is not generalizable. All metrics are marked `diagnostic_only`, `gold_eligible=false` because labels are model-generated.

**Phase 6 remains not passed/not adopted.** During retrieval, the persistent conservative token ledger blocked some reranker requests before network access as it approached its ceiling; HippoRAG internally fell back to dense passage retrieval for those cases. Therefore, although all 44 queries have rankings, this is not a uniform full-graph/reranker comparison and the metrics cannot support an adoption decision. The completed run itself used 84,014 prompt + 1,621 completion tokens (85,635 total), 30 provider responses, and $0.0033518928 reported cost. Across the incomplete attempts, recovery, completed run, and 1,139-token canary, the ledger accounts for 177,831/190,000 tokens (12,169 remaining; no pending reservation); accumulated provider-reported cost including canary is approximately $0.021414. Do not spend the remaining token allowance without revising the conservative reservation method under the same $0.25 hard cost cap. The isolated container is idle and the shared OpenRouter endpoint has been released.

Artifacts: `phase6-hipporag-deepseek-44-retry-03.json` and `phase6-hipporag-deepseek-44-retry-vs-{lexical,lightrag}-k{1,3,5,10}.json` under `/mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-results/`. Dataset SHA-256 remains `ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65`; all comparator artifacts retain automatic/non-gold provenance.

### Paired Phase 4 citation replay (offline sanity check)

Using the 44 matching query IDs, I adapted Phase 4 LightRAG's captured citation order into the evaluator's ranking schema; all 880 citation slots map to the frozen corpus. Replaying those citations against the system-judged qrels reproduces Phase 4's dense metrics exactly: R@10 0.9535, MRR 0.8036, nDCG 0.8398. Against lexical, the replay delta is +0.1161 MRR and +0.0809 nDCG at k=10, while recall@10 is -0.0233. This confirms the same-query/corpus adapter, not a new live LightRAG run; labels remain automatic/non-gold and the single unanswerable query gives a non-generalizable false-positive rate of 1/1.


## Final Phase 6 research disposition (2026-10-05)

The conservative quota stop was caused by accounting every prompt token at the maximum completion-token price. The DeepSeek route bills prompt and completion tokens at different ceilings, so this rejected work that still fit comfortably inside the authorized $0.25 cost cap. The Phase 6 runner now uses a persistent USD ledger: prompt reservations are conservatively based on serialized prompt bytes at the prompt ceiling, outputs reserve max_tokens at the completion ceiling, provider usage is accounted by token class, and abandoned reservations remain charged. The policy caps output at 1,024 tokens, pins the exact model, disables fallback, and cannot exceed $0.25. Focused verification: tests/unit/eval/test_phase6_runner.py, tests/unit/eval/test_phase6_bridge.py, and tests/unit/kg/test_graph_retriever.py ; 35 passed; git diff --check passed.

A uniform configured HippoRAG run then completed the same frozen 37-chunk corpus and all 44 judged queries. Its exact dataset and corpus hashes match the saved Phase 3 baseline. Result artifact: /mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-results/phase6-hipporag-deepseek-44-cost-run-02.json; comparison artifacts: the adjacent phase6-hipporag-deepseek-44-cost-run-02-vs-lexical-k{1,3,5,10}.json. The final replay used the response cache from a first complete run; it sent no additional model requests. That first run recorded 46,362 prompt and 718 completion tokens. At the pinned maximum prices, these requests account conservatively for $0.001631; combined with previously recorded Phase 6 usage/canary, the cost ledger accounts for $0.023039 of the $0.25 cap and leaves $0.226961. This is ceiling-based cost accounting, not a provider-reported invoice.

### Paired result and decision

- Run status: complete; 44/44 query rankings, each with 10 valid source chunk IDs; source and page attribution integrity: 1.0.
- HippoRAG @10: Recall 1.0000, MRR 0.7054, nDCG 0.7791; index time 203.77 s; mean query time 0.714 s; peak RSS 2,783 MiB.
- On the same 43 answerable queries, Phase 3 lexical scored 0.9767 / 0.6875 / 0.7589; hybrid RRF scored 1.0000 / 0.7929 / 0.8440; RRF plus BGE reranker scored 1.0000 / 0.8605 / 0.8962 for Recall/MRR/nDCG@10. The Phase 3 report records mean latency 0.108 s for RRF and 11.126 s for RRF+BGE reranking. HippoRAG ties RRF recall but ranks evidence worse, and trails RRF+BGE on both ranking metrics. Its mean query latency is below RRF+BGE but requires a 203.77 s graph build and does not produce a retrieval-quality gain over RRF.
- By source role, HippoRAG Recall@10 is 1.000 for spine and supplementary queries; MRR/nDCG are 0.6616/0.7458 for spine and 0.8500/0.8893 for supplementary. Provenance integrity remains 1.0.
- The single unanswerable question is still retrieved by every compared system (1/1 false positive); this sample cannot estimate general no-answer behavior. All relevance judgments remain automatic and gold_eligible=false.

Decision: close the bounded Phase 6 research comparison as NO-GO for adopting HippoRAG in B2C's production retrieval path. The full 44-query rerun removes the earlier quota-fallback confound, but the graph does not improve over the existing RRF hybrid baseline and ranks below the measured RRF+BGE reranker. Keep HippoRAG optional and isolated. Phase 6's bounded research decision is closed; its product/production acceptance gate is NOT PASSED because the benchmark is diagnostic/non-gold and shows no adoption benefit. Reopen only with independently validated labels or a justified workload where measured gains offset the graph build/maintenance cost.


## Phase 6 verification refresh — LightRAG paired evidence (2026-10-05)

### What was benchmarked
A prior completed LightRAG 1.5.7 `mix` retrieval-only run is available at `/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261004/data/phase4-lightrag-mix-final.json`. It contains 50/50 HTTP 200 query records. Its SHA-256 is `fb3dd1056adc6fd1bea21ffd3405ed229536c5a7de5c4c918c23a2196a908fb8`; the 44-query capture artifact records the same source hash. All 44 Phase 6 development query IDs occur in the 50-query run, and their top-20 citation rankings match the capture artifact exactly. The corpus fingerprint is `73e325e08b1b523a6cd0ecb335d83ac59ea8007da75ee2d4a08a0c8217637815`, and the query-set SHA-256 is `ad7daf03370471e517802f20f70c115421faeae8afec246b48a4217e3b5b7f65`.

This is an offline paired re-score of a previously completed service run, not a new live 44-query LightRAG execution. It made zero inference requests. Fresh comparison outputs are `/mnt/wsl/b2cdata/phase6-assets/runtime-state/deepseek-results/phase6-lightrag-vs-lexical-verified-20261005.json` and `phase6-hippo-vs-lightrag-verified-20261005.json`; the derived ranking artifact is `phase6-lightrag-live-artifact-verified-44-20261005.json`.

On the same 43 answerable queries (one unanswerable query is separately counted as a false positive by all systems), Recall/MRR/nDCG@10 were:

| Retriever | Recall@10 | MRR@10 | nDCG@10 | Evidence |
|---|---:|---:|---:|---|
| Lexical | 0.9767 | 0.6875 | 0.7589 | Re-scored from frozen rankings |
| LightRAG 1.5.7 mix | 0.9535 | 0.8036 | 0.8398 | Prior actual 50-query retrieval-only run; matched 44-query subset |
| Hybrid RRF (lexical + dense) | 1.0000 | 0.7929 | 0.8440 | Saved Phase 3 run, same dataset/corpus |
| Hybrid RRF + BGE reranker | 1.0000 | 0.8605 | 0.8962 | Saved Phase 3 run, same dataset/corpus |
| HippoRAG 2 | 1.0000 | 0.7054 | 0.7791 | Phase 6 Run 02 |

LightRAG improves ranking over lexical but misses recall versus both hybrid configurations, and its MRR/nDCG are below the reranked hybrid. HippoRAG matches neither the hybrid ranking quality nor a production benefit. All labels are automatic pseudo-qrels (`gold_eligible=false`): these are diagnostic retrieval benchmarks, not product acceptance tests or quality gates. No quality gate passed from these scores.

### Current service readiness (read-only check)
The current `b2c-lightrag` container exposes its API only on the B2C internal network; its container health status alone did not establish application readiness. An authenticated read-only documents listing showed 37 stored chunk records: 2 `processed`, 35 `failed`, and 0 in progress. The sampled failed record's error text was `User cancelled`. No query, write, retry, reindex, or inference request was sent to this current service. Its present index state is therefore not eligible for a live paired retrieval benchmark; no underlying initiator for the cancellation is inferred from that status text.

### Decision
Keep LightRAG out of production wiring. The historical paired retrieval evidence is weaker than the current hybrid+reranker baseline on this exact diagnostic set, and the current service has only 2/37 records processed. The retrieval research comparison remains **NO-GO for graph adoption**. The product quality gate remains **NOT PASSED** because the qrels are not human gold. A future live service comparison requires a verified complete 37/37 index and a permitted retrieval-only run; no such repair or reindex is claimed here.

## Owner disposition update (2026-10-05)
The owner accepts Phase 6 as closed for the current build scope and confirms the optional HippoRAG decision is **NO-GO for adoption**. Continue with existing RAG/hybrid retrieval. The diagnostic research gate is closed; no HippoRAG runtime is required. This does not claim retrieval production acceptance.
