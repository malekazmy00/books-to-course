# LightRAG / HippoRAG operational wiring evidence — 2026-10-09

## Implemented

- Canonical scoped adapters; administrator-configured HTTP router with pre-inference book/role filtering.
- Authenticated graph facade with ensure/status/query; course, generation and exact candidate-content isolation.
- Durable JSON sources and atomically committed ready manifests; native runtime profile participates in storage identity.
- Pinned native LightRAG 1.5.7 and HippoRAG 2.0.0a5 factories; separate-process graph libraries.
- Explicit internal HippoRAG dense-only fallback detection and failure; no silent lexical replacement.
- Unique source tags avoid upstream merging identical passages from different books. User-visible text remains canonical.
- Bounded two-index in-memory cache, serialized graph operations and client cleanup.
- Fresh empty native attempt on process restart/cache eviction; no old pickle deserialization.
- Separate graph Dockerfiles and optional B2C graph-services Compose overlay, with no published host ports.

## Executed evidence

- Focused adapters + runtime/facade/router + existing query retrieval tests: **53 PASS**, one existing Starlette testclient deprecation warning.
- Python compile: PASS for graph API/native/router/runtime.
- Full B2C base + observability + retrieval-hybrid + graph-services Compose config: PASS.
- First image builds: PASS for b2c-lightrag-service:20261009 and b2c-hipporag-service:20261009. Both run pip check and pinned-package/import validation during build.
- Final cached rebuild is required after the last source edits (client close, HTTP extra-field rejection, credential repr protection); final outcome is recorded below when complete.

Tests use actual FastAPI HTTP request validation/authentication and control flow with fake native backends; they do not claim a live graph inference quality result. No service was started or restarted, no model download or inference was sent by this work. Existing book data and AI Company were untouched.

## Operational limits

1. Graph reconstruction after a service restart/cache eviction incurs fresh graph-build work. Canonical source/checkpoint persistence survives, but authenticated graph binary restoration is not implemented.
2. Native graph calls currently use upstream OpenAI clients; they are not included in the production QA gateway's token/cost ledger. Before an unrestricted paid book-scale graph run, route through a budgeted project gateway or integrate a separate graph usage/budget ledger. A small explicitly bounded smoke run or local LLM route can validate runtime behavior first.
3. Source-tag influence on ranking and full-book latency require a live paired evaluation. This report does not adopt either graph as the highest-quality default.
4. Private output roots must exist on the B2C data disk, owned by UID/GID 10001, without group/world write access. Secret files must be readable by that runtime user.
5. HippoRAG uses the existing local patched source build context. Neither an uploaded source tree nor uploaded graph serialization is accepted by the API.

Detailed contract and configuration: docs/proposals/2026-10-09-graph-adapter-contract.md.

## Final build outcome

Final cached rebuild **PASS** for both services after all source edits. Image IDs:

- `b2c-lightrag-service:20261009`: `sha256:f2c405e9cab00a23d0f8a4b9c1705af2ae65a7b0a769cd41a7d6e7ba49b99028`
- `b2c-hipporag-service:20261009`: `sha256:c81b64940cb71e0c320b8c21b42a08b41a8a4612bb85e761db2b638a37ddc7f7`

Build/import/metadata/pip-check passed without inference. Logs are `/tmp/b2c-graph-build-20261009.log` and `/tmp/b2c-graph-build-final-20261009.log` on the WSL host. Final Compose config passed after adding new-service dependency on healthy BGE. Root coordinator owns deployment and live acceptance tests; this agent did not start any service.
