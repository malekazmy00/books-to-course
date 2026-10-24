"""Non-ASCII bearer tokens are rejected with 401, not a 500 from compare_digest."""
from __future__ import annotations

import httpx
import pytest

from b2c_api.app import create_app
from b2c_core import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/courses/c/books", "/v1/model-providers"])
async def test_non_ascii_authorization_is_401(monkeypatch, path):
    monkeypatch.setenv("B2C_API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("B2C_APP_API_TOKEN", "secret-token")
    app = create_app(settings=Settings(), artifact_store=object())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path, headers={"authorization": b"Bearer \xe9t\xe9"})
        good = await client.get("/health")
    assert response.status_code == 401
    assert good.status_code == 200


@pytest.mark.asyncio
async def test_non_ascii_operator_token_is_401(monkeypatch):
    monkeypatch.delenv("B2C_API_AUTH_REQUIRED", raising=False)
    monkeypatch.setenv("B2C_APP_API_TOKEN", "secret-token")
    app = create_app(settings=Settings(), artifact_store=object())
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/model-providers", headers={"authorization": b"Bearer \xe9"})
    assert response.status_code == 401
