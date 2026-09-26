"""Runtime configuration (pydantic-settings). All variables are prefixed with B2C_.

Local models only (D-004): there are deliberately no cloud-provider key fields here.
"""
from __future__ import annotations

from urllib.parse import quote, urlparse

from pydantic import field_validator
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

    # orchestration
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"

    # telemetry
    otel_endpoint: str = "http://otel-collector:4318"
    service_name: str = "b2c"

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
