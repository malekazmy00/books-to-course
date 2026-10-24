from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from b2c_api.app import create_app
from b2c_core import Settings
from b2c_pedagogy.qa_generation import GroundedQA, ScopeResult

WORKFLOW_ID = 'b2c-qa-' + '1' * 64


class CheckpointStore:
    bucket = 'artifacts'

    def __init__(self) -> None:
        self.s3 = self
        scope = ScopeResult(
            scope_id='lesson-1', kind='lesson', lesson_ids=['lesson-1'], status='final',
            items=[GroundedQA(
                qa_id='qa-1', question='What is the key idea?', answer='The key idea.',
                evidence_chunk_ids=['chunk-1'], book_id='book-main', role='spine', pages=[1],
                evidence_quote='A source statement.', model_id='test-model', prompt_version='v1',
                automated_checks={'answer_supported': True},
            )],
        )
        self.key = f'_system/qa-checkpoints/{WORKFLOW_ID}/fingerprint/scope.json'
        self.payload = json.dumps({'scope_result': scope.model_dump(mode='json'), 'identity': {},
                                   'saved_at': datetime.now(UTC).isoformat()}).encode()

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **kwargs: object) -> dict:
        assert Bucket == self.bucket
        return {'Contents': [{'Key': self.key}], 'IsTruncated': False}

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        assert Bucket == self.bucket and Key == self.key
        return {'Body': io.BytesIO(self.payload)}


@pytest.mark.asyncio
async def test_checkpoint_endpoint_returns_saved_lesson_questions() -> None:
    app = create_app(settings=Settings(), artifact_store=CheckpointStore())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(f'/v1/course-qa/{WORKFLOW_ID}/checkpoints')

    assert response.status_code == 200
    body = response.json()
    assert body['scope_count'] == 1
    assert body['scopes'][0]['scope_result']['items'][0]['question'] == 'What is the key idea?'