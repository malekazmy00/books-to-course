"""The API requires a bearer token unless auth is explicitly disabled."""
import pytest

from b2c_api.app import create_app


def test_auth_is_required_by_default(monkeypatch):
    monkeypatch.delenv("B2C_API_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("B2C_APP_API_TOKEN", raising=False)
    monkeypatch.delenv("B2C_APP_API_TOKEN_FILE", raising=False)
    with pytest.raises(RuntimeError, match="auth is required"):
        create_app()
