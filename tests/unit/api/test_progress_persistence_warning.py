"""Book-progress persistence failures are reported, and only storage errors are tolerated."""
from __future__ import annotations

import httpx
import pytest
from botocore.exceptions import ClientError

from b2c_api.app import create_app
from b2c_core import Settings

from tests.unit.api.test_book_upload import FakeProgressS3, FakeStore, FakeTemporal, _resolved, post_upload


class FailingProgressS3(FakeProgressS3):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        if "/book-progress/" in Key:
            raise self.error
        super().put_object(Bucket=Bucket, Key=Key, Body=Body, **kwargs)


def app_with(error):
    store = FakeStore()
    store.s3 = FailingProgressS3(error)
    temporal = FakeTemporal()
    return create_app(settings=Settings(), artifact_store=store,
                      temporal_connect=lambda settings: _resolved(temporal)), temporal


@pytest.mark.asyncio
async def test_storage_failure_is_surfaced_as_a_warning():
    error = ClientError({"Error": {"Code": "SlowDown", "Message": "busy"},
                         "ResponseMetadata": {"HTTPStatusCode": 503}}, "PutObject")
    app, temporal = app_with(error)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client)
    assert response.status_code == 202, response.text
    assert len(temporal.starts) == 1
    assert response.json()["progress_warning"] == "book progress could not be saved; the workflow was started"


@pytest.mark.asyncio
async def test_successful_progress_has_no_warning():
    store = FakeStore()
    app = create_app(settings=Settings(), artifact_store=store,
                     temporal_connect=lambda settings: _resolved(FakeTemporal()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await post_upload(client)
    assert response.status_code == 202
    assert "progress_warning" not in response.json()


@pytest.mark.asyncio
async def test_programming_errors_are_not_swallowed():
    app, _ = app_with(TypeError("bug in progress code"))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await post_upload(client)
    assert response.status_code == 500
