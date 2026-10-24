# EmbeddingGemma local service — 2026-10-09

Prepared a separate reproducible CPU endpoint using the existing 164,626,432-byte LiteRT model at `/mnt/e/B2C-Model-Lab/embeddings/embeddinggemma-2-text-270m.litertlm`. The model is not bundled in the image or downloaded. Startup verifies SHA-256 `2d079ee2f6f066b1f368e8d7c819f55214eaef1d0513b312321901f30ab286fb`.

- Image: `b2c-embeddinggemma:0.18.0`; manifest list `sha256:f37b4fa38da2fb17107d6abc7d6ea0c94335b86e99b5bd25092d7d1cf3a07028`.
- Base image is digest pinned. `requirements.in` is compiled to a complete hash-locked `requirements.txt`; install uses `--require-hashes`. `litert-lm-api==0.18.0` supplies inference bindings without the heavyweight builder/CLI package. No external virtual environment is used.
- Endpoint: `http://embeddinggemma:8000/v1/embeddings`; model ID `embeddinggemma-2-text-270m`; dimensions 768. Optional purpose defaults to document; send purpose=query for query prefix and purpose=document for passage prefix. Authentication uses a mounted secret file.
- Policy `task-prefix-768char-weighted-v1` reproduces the aligned Model Lab test policy: bounded whitespace windows, long-word splitting, normalized segment vectors, character-weighted average and final renormalization. All content is retained up to request bounds. Bounds: 32 texts/batch, 65,536 chars/text, 262,144 chars/batch. One CPU engine is serialized by a lock; four CPU threads.
- Health and model-list endpoints expose ID/dimensions/policy. Invalid model/dimension inputs fail 422; invalid credentials fail 401; invalid vector shape/numbers fail 502 without exposing engine details.
- Compose mounts model read-only, uses read-only container root, 512MB tmpfs cache, 2GB memory, internal network only; no published port.

Executed evidence: Docker build succeeded; import of EmbeddingEngine/EmbeddingOptions/InputOverflowStrategy and CPU backend in built image succeeded without network or inference; 3 focused API tests passed (prefixes/auth/model/dimension/batch validation, invalid vectors, long-input preservation); Compose config validation passed. No B2C service was started/restarted by this work. Actual loaded-model endpoint inference and pipeline integration remain to be checked during coordinated deployment.
