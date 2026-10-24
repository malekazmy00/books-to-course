# Hy-MT2 local route — 2026-10-10

B2C only. No model download, deployed-service restart, live book inference, or default-model change.

Weights verified at /mnt/wsl/b2cdata/models/phase7-translation/hy-mt2-1.8b: safetensors file 4,077,072,784 bytes, header opens with 355 tensors. Existing eval runtime Transformers 5.6.2 / Torch 2.6.0+cpu loads AutoConfig as HunYuanDenseV1Config. This is config/header compatibility evidence only.

Implemented docker/hy-translation/server.py and Dockerfile: offline CPU weights, translation-only OpenAI API, individually generated translations wrapped into JSON with original segment IDs by code, glossary and immutable-marker prompts, serialized inference, context cap and length termination detection. Unloaded model cannot pass readiness.

Implemented docker/compose.hy-translation.yml: opt-in service internal port 3007 and dedicated hy-translation generation queue, read-only existing weights, 16GiB service memory limit, four Torch threads. Requires base plus observability compose. Compose config --quiet passed.

API TranslationRequest.provider_id now selects route explicitly and validates endpoint plus workflow/activity pollers. Resume preserves resume_from_workflow_id while switching model and queue without changing defaults. ModelLifecycle Hy status/default additionally requires the route_ready async callback; create_app now wires it to Temporal workflow/activity DescribeTaskQueue pollers. Evaluation remains not_tested until a real inference smoke succeeds.

Focused tests: test_hy_adapter.py, test_hy_translation_route.py and test_model_lifecycle.py: 27 passed. Covers invalid/nontranslation requests, duplicate IDs, unloaded status, catalog disablement, missing worker pollers, exact selected model/queue, and resume checkpoint identity.

Pending deployment: build/start only Hy model service and worker; verify /v1/models and both pollers; disposable short Arabic translation with numeric markers through actual gateway. No readiness or tested claim before this succeeds.

Follow-up: create_app assigns model_worker_queue_ready to lifecycle.route_ready. Catalog status and default readiness require both Hy workflow/activity pollers. Explicit translation selection uses that same callback, avoiding duplicate queue probes. Four focused cases cover absent workflow poller, absent activity poller, neither, and both. No service deployment or restart.
