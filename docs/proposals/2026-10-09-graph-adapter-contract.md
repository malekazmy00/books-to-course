# Scoped graph adapter contract — 2026-10-09

`b2c_indexing.graph_adapters` provides retrieval-only LightRAG 1.5.7 and HippoRAG 2.0.0a5 boundaries. It does not start services, import graph packages, load pickle, create indexes, or change existing workflows.

## Binding an index

Construct `RetrievalScope(course_id, generation_id, tuple(canonical_chunks))`. Provision a dedicated backend index with exactly those chunks. Every upstream source identifier must equal `scope.source_id(chunk)`; for LightRAG this is the document `file_path`. Persist the trusted manifest with `scope.manifest(engine, version)` only after independent complete-index verification. The manifest includes full canonical chunk content/metadata hash, source IDs, generation, course, pinned engine version and ready state.

A ready manifest is supplied by trusted provisioning, not accepted from a user or fabricated by the adapter. LightRAG's upstream API does not attest a B2C course/generation. Therefore deployment must guarantee a dedicated endpoint/workspace containing only this scope, and verify its completed source set against the manifest. Passing a mixed upstream workspace with a matching local manifest is unsafe and is not prevented by output filtering. The adapter refuses candidate subsets and allowlists excluding any indexed source before the query is sent. A lesson-specific request consequently requires a lesson-specific dedicated index or a future backend with independently verified pre-inference filtering. Do not solve this by post-filtering a course-global graph.

## Retrieval

`search_candidates(query, candidates, top_k=10, allowed_book_ids=None, allowed_roles=None)` returns canonical `RetrievalHit` records. Provider-generated text is never substituted for the saved source. Unknown source IDs, non-finite scores, incompatible metadata, incomplete manifest, timeout and bad response fail explicitly with `GraphRetrievalError`. No lexical fallback occurs. LightRAG uses `/query/data`, `mode=mix`, `only_need_context=true` and rerank disabled. Rank-derived reciprocal scores describe the source order, not model confidence.

The HTTP transport disallows redirects to avoid forwarding the API key, bounds response size to 8 MiB and suppresses provider exception details. Transport injection is available for tests; real credential values must come from project secret handling.

## HippoRAG service boundary

A `ScopedHippoBackend` is a separately provisioned trusted facade. Its `retrieve(query, course_id=..., generation_id=..., corpus_sha256=..., top_k=...)` must return an object containing those exact scope fields, `version=2.0.0a5`, `method=hipporag`, `fallback=false` and `hits=[{source_id,score}, ...]`. A dense/lexical internal fallback must be reported explicitly and is rejected. The worker never imports the evaluation bridge or loads upstream serialization. Service provisioning must keep duplicate passages under distinct canonical source IDs; merging duplicate content from different books is not acceptable. The existing evaluation one-shot bridge is not a durable service implementation.

## Verification evidence

`tests/unit/indexing/test_graph_adapters.py`: 20 passed. Fake transport/backend tests demonstrate canonical source restoration, scope/allowlist checking before calls, manifest pinning, course/generation mismatch rejection, duplicate text identity preservation, unknown source rejection, fallback rejection, invalid scores and redacted timeout/errors. These do not claim live service readiness or a completed pipeline deployment.

## Operational integration prepared — 2026-10-09

The implementation now includes `graph_router.py`, `graph_api.py`, `graph_runtime.py` and `graph_native.py`. Router endpoints are administrator-owned configuration; HTTP inputs cannot choose endpoints, credentials or runtime directories. Before any inference, the router filters allowed books/roles and hashes the exact canonical candidate set into a separate index generation. The facade authenticates ensure/status/query. It atomically persists canonical source JSON and a trusted manifest after all native indexing succeeds. Native libraries live in separate service processes and are never imported by the production QA worker.

`GraphRetrievalRouter(course_id, generation_id, GraphProviderConfig.from_env())` implements `search_candidates(query, chunks, mode="lightrag"|"hipporag", top_k=..., allowed_book_ids=..., allowed_roles=...)`. Catalog aliases `lightrag-157` and `hipporag-2` are accepted. Worker environment is `B2C_LIGHTRAG_SERVICE_URL/KEY_FILE` and `B2C_HIPPORAG_SERVICE_URL/KEY_FILE`.

The facade is started through `uvicorn b2c_indexing.graph_api:create_app --factory --port 9630 --workers 1`. Native factory configuration uses `B2C_GRAPH_ENGINE`, `B2C_GRAPH_SERVICE_KEY_FILE`, `B2C_GRAPH_INDEX_ROOT`, `B2C_GRAPH_LLM_BASE_URL/MODEL/KEY_FILE`, `B2C_GRAPH_EMBEDDING_BASE_URL/MODEL/KEY_FILE`, `B2C_GRAPH_EMBEDDING_DIM` and `B2C_GRAPH_TIMEOUT_S`. `/health` checks installed pinned package versions and publishes package readiness and a runtime profile fingerprint; it does not claim a book index is ready. Authenticated `/v1/index/status` reports `not_started`, `building`, `failed`, `ready` or `rebuild_required` for the exact generation.

The registry uses a bounded two-index cache and serializes native operations. Restarts and evictions rebuild graphs from canonical JSON into newly generated empty directories; previous upstream files, including HippoRAG pickle, are never restored. This incurs graph reconstruction cost. Source/checkpoint persistence survives, but graph binary restoration is deliberately not supported until authenticated serialization can be independently validated. Native model/endpoint changes produce different storage keys through the runtime profile fingerprint. A workflow generation must also include its frozen retrieval/embedding selections; a changed administrator service configuration must not silently change a resumed job's retrieval assumptions.

HippoRAG's internal dense-only fallback is detected through the pinned result's empty `graph_seeds` and fails explicitly. Both native runtimes tag upstream content with unique canonical source identities so identical text from different books does not collapse under upstream content hashing. Those source tags are retrieval metadata only; user-visible lesson content and QA evidence remain canonical source text. The effect of source tags on ranking requires a live paired evaluation before claiming improved retrieval quality.

`docker/compose.graph-services.yml` creates separate optional `lightrag-service` and `hipporag-service` behind the `graph-engines` profile. It depends on administrator preparation of private graph root directories owned by UID/GID 10001 and readable project secret mounts. The LightRAG Dockerfile pins the existing upstream image digest; HippoRAG uses the existing isolated patched runtime and copies only reviewed source from the local `hipporag_src` build context, never evaluation state. No model download is performed by these files. Both services run with one process and no host API port.

Verification: 53 focused tests pass across adapters/runtime/query service; facade tests exercise real FastAPI HTTP validation/authentication/ensure/query with fake native backends, including course/role isolation, canonical-source restoration, idempotence, concurrent requests, restart, failed build retry, bounded cache, profile change and pinned health checks. Python compilation and full B2C Compose configuration validation passed. Native graph inference and end-to-end book runs remain unexecuted; these tests do not constitute retrieval quality acceptance.

Upstream API signatures consulted: https://github.com/HKUDS/LightRAG/tree/v1.5.7 (LightRAG ainsert/aquery_data/QueryParam/EmbeddingFunc and workspace lifecycle). HippoRAG implementation inspected from the existing pinned patched local source. No evaluation bridge was imported into the production runtime.
