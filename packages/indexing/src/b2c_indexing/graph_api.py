"""Authenticated graph facade. Run as its own service, never on the QA worker."""
from __future__ import annotations
import hmac
import os
from pathlib import Path
from collections.abc import Callable
from typing import Literal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from b2c_contracts.models import StructuredChunk
from .graph_adapters import RetrievalScope, GraphRetrievalError
from .graph_runtime import GraphIndexRegistry, NativeGraph

class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["lightrag", "hipporag"]
    course_id: str = Field(min_length=1, max_length=256)
    generation_id: str = Field(min_length=1, max_length=512)
    chunks: list[StructuredChunk] = Field(min_length=1, max_length=5000)

class QueryRequest(IndexRequest):
    query: str = Field(min_length=1, max_length=32000)
    top_k: int = Field(default=10, ge=1, le=100)


def create_graph_app(root: Path, api_key: str, factory: Callable[[str, Path], NativeGraph]) -> FastAPI:
    if not api_key.strip(): raise ValueError("graph service credential required")
    registry = GraphIndexRegistry(root, factory)
    app = FastAPI(title="B2C scoped graph service", docs_url=None, redoc_url=None)
    app.state.graph_registry = registry
    def authorize(value):
        if not isinstance(value, str) or not hmac.compare_digest(value.encode(), ("Bearer " + api_key).encode()):
            raise HTTPException(status_code=401, detail="invalid credentials")
    def scope(request):
        if sum(len(c.text) for c in request.chunks) > 16_000_000:
            raise HTTPException(status_code=413, detail="graph source scope exceeds limit")
        try: return RetrievalScope(request.course_id, request.generation_id, tuple(request.chunks))
        except ValueError: raise HTTPException(status_code=422, detail="invalid canonical source scope") from None
    @app.get("/health")
    def health():
        inspect = getattr(factory, "health", None)
        payload = inspect() if inspect else {"status": "ok"}
        if payload.get("status") != "ok":
            from fastapi.responses import JSONResponse
            return JSONResponse(payload, status_code=503)
        return payload
    @app.post("/v1/index/ensure")
    def ensure(request: IndexRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try: return registry.ensure(request.engine, scope(request))
        except GraphRetrievalError: raise HTTPException(status_code=503, detail="graph index provisioning failed") from None
    @app.post("/v1/index/status")
    def status(request: IndexRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try: return registry.status(request.engine, scope(request))
        except GraphRetrievalError: raise HTTPException(status_code=503, detail="graph index state unavailable") from None
    @app.post("/v1/index/query")
    def query(request: QueryRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try: return registry.retrieve(request.engine, scope(request), request.query, request.top_k)
        except GraphRetrievalError: raise HTTPException(status_code=503, detail="graph index unavailable or retrieval failed") from None
    return app


def create_app() -> FastAPI:
    from .graph_native import NativeRuntimeFactory, NativeRuntimeConfig
    key = Path(os.environ["B2C_GRAPH_SERVICE_KEY_FILE"]).read_text(encoding="utf-8").strip()
    return create_graph_app(Path(os.environ["B2C_GRAPH_INDEX_ROOT"]), key,
                            NativeRuntimeFactory(NativeRuntimeConfig.from_env(), enabled_engine=os.environ.get("B2C_GRAPH_ENGINE")))
