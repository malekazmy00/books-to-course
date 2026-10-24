# Per-stage model preparation and Settings — source staging

Status: source-only lifecycle/settings slice checked; **not deployed and not a completed execution-selection feature**. No running workflow, service, data store, or APK was replaced. AI Company was not accessed.

The operator API now exposes authenticated model catalog, real readiness checks, asynchronous Ollama loading, per-stage local defaults, and external credential registration against operator-approved provider endpoints. General APK Settings displays these options and polls starting/loading/ready/failed states. The stage execution screens remain unchanged.

## Implemented behavior

- `GET /v1/model-providers`: public capabilities, stages, local/external identity, price metadata, default selection, readiness snapshot. It never returns provider URLs, credential file paths, or raw keys.
- `GET /v1/model-providers/{id}`: refresh actual readiness. `ready` requires the requested model in Ollama `/api/ps` or OpenAI-compatible `/models`, or a service health response of `ok/ready`. Errors are sanitized. A failed load remains failed until a new successful probe/retry.
- `POST /v1/model-providers/{id}/activate`: return immediately, deduplicate requests, load Ollama generation or embedding models with a bounded idle timeout, then confirm model identity/readiness. A stopped llama.cpp/Fasih service reports unsupported activation because no safe service-start adapter exists.
- `PUT /v1/model-providers/{id}/default/{stage}`: asynchronously prepare and validate a local stage default, persist the selection only after readiness and successful atomic write. Selections are independent by stage. Default Ollama models are pinned/refreshed; duplicate defaults sharing one model are coalesced. A temporary alternate never unpins a default. A replaced default gets idle expiry only when no other stage still references it; no forced unload occurs.
- `PUT /v1/model-providers/{id}/credentials`: HTTPS and operator bearer token required; callers choose an existing approved profile and model, never a URL or command. Validate model-list access before replacing the persisted profile. Keys are stored in permission-600 server files, never source, catalog content, app preferences, responses, or logs. Failed validation removes the temporary secret. This checks model availability, **not generation quality, entitlement or free quota**.
- Unknown prices stay unknown. A free-price claim requires an operator source and verification timestamp. Changing a profile's model clears its prior price metadata. Exact prices/capabilities remain metadata rather than an automatic budget or rate limiter.
- Source APK Settings has stage/model selectors, preparation/default actions, a password input for external credentials, and 2-second progress polling. It states that preparation does not switch a running workflow.

## Required server configuration for later deployment

`B2C_MODEL_CATALOG_FILE` is an operator-owned JSON file with approved provider configurations. `B2C_MODEL_PREFERENCES_FILE` points to a file on persistent storage whose parent directory already exists. `B2C_MODEL_SECRETS_DIR` is an operator-owned restricted directory on persistent storage. Existing mobile bearer authentication is mandatory on all management endpoints even if general API auth is disabled. HTTPS proxy handling must preserve the authenticated original request scheme for credential registration.

Example catalog entries (configuration examples, not adopted production defaults):

```json
[
  {
    "id": "gemma-local", "label": "Gemma local candidate",
    "stages": ["questions", "translation", "enrichment"], "kind": "local",
    "adapter": "ollama", "base_url": "http://b2c-ollama:11434",
    "model": "gemma4:e4b", "idle_minutes": 5,
    "capabilities": ["text"], "languages": ["en", "ar"]
  },
  {
    "id": "embedding-local", "label": "Embedding local candidate",
    "stages": ["rag"], "kind": "local", "adapter": "ollama",
    "base_url": "http://b2c-ollama:11434", "model": "embeddinggemma2",
    "load_mode": "embedding", "idle_minutes": 5
  },
  {
    "id": "fasih", "label": "Fasih", "stages": ["audio"], "kind": "local",
    "adapter": "health", "base_url": "http://fasih-tts:3006",
    "model": "Fasih-TTS-V1", "languages": ["ar"]
  },
  {
    "id": "openrouter-profile", "label": "OpenRouter approved profile",
    "stages": ["questions", "translation", "enrichment"], "kind": "external",
    "adapter": "openai", "base_url": "https://openrouter.ai/api/v1",
    "model": "operator-selected-model", "pricing_status": "unknown"
  }
]
```

Do not add the isolated model-lab address to the production catalog or expose Docker sockets to the app/API. This implementation targets the current single API process. Multi-process/distributed deployments need shared durable state and distributed admission/locking before rollout. Ollama residency also depends on sufficient RAM and other clients' keep-alive settings; no automatic eviction/memory admission was added. Existing llama.cpp and Fasih services keep their own residency policy and cannot be unloaded through these management endpoints.

## Remaining execution wiring — release blocker

The current Temporal activities construct their model clients from global worker Settings. The new stage defaults are **preparation defaults**, not frozen execution profiles. `job_switching_supported: false` is returned and displayed. Before presenting these settings as controlling new work, add a validated stage provider-profile reference to new workflow inputs, resolve/freeze it at workflow start, and pass it to each relevant activity/client. Existing workflows must retain their original input/config/checkpoints; provider changes during execution require a checkpoint boundary and a new recorded model version. OCR, translation, RAG embedding and TTS each need their existing specific adapter bound to that profile. Unsupported options must stay disabled until their execution route exists. A protected B2C-only service controller or a lazy standalone Fasih service is required to start stopped services; this code does not execute shell commands or container operations.

## Evidence

- `.venv/bin/python -m pytest tests/unit/api/test_model_lifecycle.py tests/unit/api/test_book_upload.py -q`: **34 passed** (10 lifecycle tests plus 24 existing upload/API regressions). Loading/readiness/failure, retry, deduplication, stage isolation, persistent restore, previous-default preservation, real adapter identity check, embedding loader, and secret handling were exercised with safe mocks/temp files; no live model endpoint was contacted.
- `./gradlew compileDebugJavaWithJavac --no-daemon --offline`: **BUILD SUCCESSFUL** after final Java change. This compiles source only; it does not build/deploy a new APK or prove Android UI E2E behavior. Existing ARMv7-only/SDK warnings remain.
- `.venv/bin/lint-imports`: 5 contracts kept, 1 unrelated existing contract broken: `b2c_pedagogy.lesson_enrichment -> b2c_synthesis.validation` line 25. The model HTTP-access boundary remained kept. No unrelated fix was attempted.
- Ruff is not an installed project tool; no package was installed to add it.

Next safe step: integrate frozen per-stage execution profiles in source with checkpoint-aware tests, then package the APK and deploy only at the agreed safe worker transition. This proposal does not close a project phase.
