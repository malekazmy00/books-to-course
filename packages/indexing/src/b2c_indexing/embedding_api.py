from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, field_validator
from transformers import AutoModel, AutoTokenizer

MODEL_PATH = os.getenv("B2C_EMBED_MODEL_PATH", "/models/bge-m3")
MODEL_NAME = "BAAI/bge-m3"
_api_key_file = os.getenv("B2C_EMBEDDING_API_KEY_FILE", "").strip()
API_KEY = (Path(_api_key_file).read_text(encoding="utf-8").strip()
           if _api_key_file else os.environ.get("B2C_LIGHTRAG_EMBED_KEY", ""))
if not API_KEY:
    raise RuntimeError("embedding API key is empty")
MAX_LENGTH = int(os.getenv("B2C_EMBED_MAX_LENGTH", "8192"))
MAX_BATCH = int(os.getenv("B2C_EMBED_MAX_BATCH", "64"))
torch.set_num_threads(int(os.getenv("OMP_NUM_THREADS", "4")))

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=False)
model = AutoModel.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=False)
model.eval()

app = FastAPI(title="B2C local BGE-M3 embedding endpoint", docs_url=None, redoc_url=None)


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None

    @field_validator("input")
    @classmethod
    def validate_input(cls, value: str | list[str]) -> str | list[str]:
        texts = [value] if isinstance(value, str) else value
        if not texts or len(texts) > MAX_BATCH or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError(f"input must contain 1..{MAX_BATCH} non-empty strings")
        return value


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/v1/models")
def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"object": "list", "data": [
        {"id": MODEL_NAME, "object": "model", "owned_by": "local"}]}


@app.post("/v1/embeddings")
def embeddings(request: EmbeddingRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid credentials")
    texts = [request.input] if isinstance(request.input, str) else request.input
    encoded = tokenizer(texts, padding=True, truncation=True, max_length=MAX_LENGTH, return_tensors="pt")
    prompt_tokens = int(encoded["attention_mask"].sum().item())
    with torch.inference_mode():
        hidden = model(**encoded).last_hidden_state[:, 0, :]
        vectors = F.normalize(hidden, p=2, dim=1).cpu().tolist()
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)],
        "model": MODEL_NAME,
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }
