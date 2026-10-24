"""Concurrent first requests share one Temporal connection."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from b2c_api.app import create_app
from b2c_core import Settings


class Temporal:
    def get_workflow_handle(self, _):
        return self

    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name="RUNNING"), start_time=None, close_time=None)


@pytest.mark.asyncio
async def test_lazy_client_is_connected_once_under_concurrency():
    connects = []

    async def connect(_):
        connects.append(1)
        await asyncio.sleep(0.05)
        return Temporal()

    app = create_app(settings=Settings(), artifact_store=object(), temporal_connect=connect)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(*[client.get("/v1/ingestions/b2c-ingest-" + str(n) * 64)
                                           for n in range(1, 6)])
    assert [r.status_code for r in responses] == [200] * 5
    assert len(connects) == 1
