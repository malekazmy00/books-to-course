"""Runtime configuration (pydantic-settings). All variables are prefixed with B2C_.

Local models are the default; temporary external test providers require explicit opt-in and a separate secret file.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="B2C_", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"

    # postgres (application database)
    pg_host: str = "postgres"
    pg_port: int = 5432
    pg_db: str = "b2c"
    pg_user: str = "b2c"
    pg_password: str = ""

    # S3-compatible object store
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = "artifacts"

    # local LLM (llama-server, OpenAI-compatible)
    llm_base_url: str = "http://llm:8080/v1"
    llm_model_id: str = "local"
    llm_timeout_s: float = 600.0
    llm_reasoning_effort: str | None = None
    openrouter_free_daily_limit: int = Field(default=40, ge=1, le=50)
    openrouter_free_min_interval_seconds: float = Field(default=6.0, ge=0)
    openrouter_max_pages_per_request: int = Field(default=10, ge=1, le=50)
    openrouter_max_input_chars: int = Field(default=24000, ge=1024)
    openrouter_ocr_max_tokens: int = Field(default=12000, ge=1024)
    inception_free_token_limit: int = Field(default=10_000_000, ge=1)
    llm_allow_remote: bool = False
    llm_api_key_file: str = ""

    # Query-time RAG for course QA. Hybrid is opt-in until the local embedding
    # endpoint and its secret are configured in the B2C worker environment.
    retrieval_mode: Literal["lexical", "hybrid"] = "lexical"
    retrieval_top_k: int = Field(default=20, ge=1, le=100)
    embedding_base_url: str = ""
    embedding_model_id: str = "BAAI/bge-m3"
    embedding_api_key_file: str = ""
    embedding_timeout_s: float = Field(default=30.0, gt=0, le=120)

    # Optional temporary file-backed Gemini vision credential; never put the key in env values.
    gemini_api_key_file: str = ""

    # orchestration
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    indexing_task_queue: str = "light"

    # telemetry
    otel_endpoint: str = "http://otel-collector:4318"
    service_name: str = "b2c"

    # Multipart upload limit; uploads are buffered only up to this bounded amount.
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)

    @field_validator("embedding_base_url")
    @classmethod
    def validate_embedding_base_url(cls, value: str) -> str:
        value = value.strip()
        if value and urlparse(value).scheme not in {"http", "https"}:
            raise ValueError("embedding_base_url must be http(s)")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        if urlparse(v).scheme not in ("http", "https"):
            raise ValueError("llm_base_url must be http(s)")
        return v.rstrip("/")

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{quote(self.pg_user, safe='')}:{quote(self.pg_password, safe='')}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
        )

    @property
    def embedding_api_key(self) -> str | None:
        if not self.embedding_api_key_file:
            return None
        return Path(self.embedding_api_key_file).read_text(encoding="utf-8").strip()

    @property
    def llm_api_key(self) -> str | None:
        if not self.llm_api_key_file:
            return None
        return Path(self.llm_api_key_file).read_text(encoding="utf-8").strip()
