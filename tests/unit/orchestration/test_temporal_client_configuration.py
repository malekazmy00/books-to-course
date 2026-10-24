from __future__ import annotations

import pytest
from b2c_api import app as api_module
from b2c_core import Settings
from b2c_orchestration import worker as worker_module
from temporalio.contrib.pydantic import pydantic_data_converter


@pytest.mark.asyncio
async def test_api_temporal_client_uses_pydantic_converter(monkeypatch):
    captured = {}

    async def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api_module.Client, "connect", fake_connect)
    await api_module._connect_temporal(Settings())
    assert captured["data_converter"] is pydantic_data_converter


@pytest.mark.asyncio
async def test_worker_temporal_client_uses_pydantic_converter_and_tracing(monkeypatch):
    captured = {}

    async def fake_connect(*args, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(worker_module.Client, "connect", fake_connect)
    await worker_module.connect(Settings())
    assert captured["data_converter"] is pydantic_data_converter
    assert len(captured["interceptors"]) == 1
