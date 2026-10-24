"""Bounded, authenticated API for the installed EmbeddingGemma LiteRT artifact."""
from __future__ import annotations

import hashlib
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

MODEL_ID = "embeddinggemma-2-text-270m"
MODEL_SHA256 = "2d079ee2f6f066b1f368e8d7c819f55214eaef1d0513b312321901f30ab286fb"
DIMENSIONS = 768
POLICY = "task-prefix-768char-weighted-v1"


def windows(text: str, limit: int = 768) -> list[str]:
    parts, current = [], ""
    for word in text.split():
        if len(current) + len(word) + 1 > limit and current:
            parts.append(current)
            current = ""
        if len(word) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(word[index:index + limit] for index in range(0, len(word), limit))
        else:
            current += (" " if current else "") + word
    if current:
        parts.append(current)
    return parts


def combine(vectors: list[tuple[list[float], int]]) -> list[float]:
    if not vectors or any(len(v) != DIMENSIONS or any(not math.isfinite(x) for x in v)
                          for v, _ in vectors):
        raise ValueError("embedding engine returned invalid dimensions or values")
    weight = sum(w for _, w in vectors)
    result = [sum(v[i] * w for v, w in vectors) / weight for i in range(DIMENSIONS)]
    norm = math.sqrt(sum(x * x for x in result))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("embedding engine returned a zero or invalid vector")
    return [x / norm for x in result]


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str = MODEL_ID
    purpose: Literal["query", "document"] = "document"
    dimensions: int | None = Field(default=None, ge=1)

    @field_validator("input")
    @classmethod
    def bounded_inputs(cls, value):
        texts = [value] if isinstance(value, str) else value
        if not texts or len(texts) > 32 or any(not t.strip() or len(t) > 65536 for t in texts):
            raise ValueError("input must contain 1..32 non-empty texts, each at most 65536 characters")
        if sum(len(t) for t in texts) > 262144:
            raise ValueError("batch input exceeds 262144 characters")
        return value


def create_app(engine_factory=None, *, api_key: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.key = api_key or Path(os.environ["B2C_EMBEDDING_API_KEY_FILE"]).read_text().strip()
        if not app.state.key:
            raise RuntimeError("embedding credential is empty")
        if engine_factory:
            app.state.encode = engine_factory()
        else:
            from litert_lm.embedding_engine import EmbeddingEngine, EmbeddingOptions, InputOverflowStrategy
            from litert_lm import interfaces
            path = Path(os.getenv("B2C_EMBED_MODEL_PATH", "/models/embeddinggemma.litertlm"))
            with path.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != MODEL_SHA256:
                    raise RuntimeError("installed embedding artifact checksum mismatch")
            Path("/tmp/litert-cache").mkdir(parents=True, exist_ok=True)
            engine = EmbeddingEngine(str(path), backend=interfaces.Backend.CPU(thread_count=int(
                os.getenv("B2C_EMBED_CPU_THREADS", "4"))), cache_dir="/tmp/litert-cache")
            options = EmbeddingOptions(normalize=True,
                input_overflow_strategy=InputOverflowStrategy.CHUNK_AND_AVERAGE)
            app.state.engine = engine
            app.state.encode = lambda text: engine.compute_embedding(text, options=options).embedding
        app.state.lock = Lock()
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
    app = FastAPI(title="B2C EmbeddingGemma", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": MODEL_ID, "dimensions": DIMENSIONS,
                "policy": POLICY, "sha256": MODEL_SHA256}

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model",
                "owned_by": "local", "dimensions": DIMENSIONS, "policy": POLICY}]}

    @app.post("/v1/embeddings")
    def embeddings(request: EmbeddingRequest, authorization: str | None = Header(default=None)):
        import secrets
        if not authorization or not secrets.compare_digest(authorization, f"Bearer {app.state.key}"):
            raise HTTPException(status_code=401, detail="invalid credentials")
        if request.model != MODEL_ID or request.dimensions not in (None, DIMENSIONS):
            raise HTTPException(status_code=422, detail="unsupported embedding model or dimensions")
        texts = [request.input] if isinstance(request.input, str) else request.input
        prefix = "task: search query | text: " if request.purpose == "query" else "task: search result | text: "
        try:
            with app.state.lock:
                vectors = [combine([(list(app.state.encode(prefix + part)), len(part))
                            for part in windows(text)]) for text in texts]
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="embedding engine returned an invalid vector") from exc
        return {"object": "list", "model": MODEL_ID, "policy": POLICY,
                "data": [{"object": "embedding", "index": i, "embedding": vector}
                         for i, vector in enumerate(vectors)]}
    return app


app = create_app()
