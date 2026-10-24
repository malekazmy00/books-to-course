import io
import json
from types import SimpleNamespace
import httpx
import pytest
from b2c_api.app import create_app
from b2c_core import Settings

class Store:
    bucket = 'artifacts'
    def __init__(self): self.s3 = self; self.objects = {}
    def get_object(self, **kw): return {'Body': io.BytesIO(self.objects[kw['Key']])}
    def put_object(self, **kw): self.objects[kw['Key']] = kw['Body']
class Temporal:
    def __init__(self, status='RUNNING'): self.status = status
    def get_workflow_handle(self, wid): return self
    async def describe(self): return SimpleNamespace(status=SimpleNamespace(name=self.status))
    async def signal(self, name): assert name == 'resume_stage'

@pytest.mark.asyncio
async def test_pause_status_resume_use_same_workflow_record():
    store = Store(); temporal = Temporal()
    async def connect(_): return temporal
    app = create_app(settings=Settings(), artifact_store=store, temporal_connect=connect)
    wid = 'b2c-translate-'+'a'*64
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(f'/v1/workflows/{wid}/resume')).status_code == 409
        result = await client.post(f'/v1/workflows/{wid}/pause')
        assert result.status_code == 200 and result.json()['status'] == 'PAUSE_REQUESTED'
        key = f'_system/workflow-controls/{wid}.json'
        record = json.loads(store.objects[key]); record.update(state='PAUSED', checkpoint='batch:2')
        store.objects[key] = json.dumps(record).encode()
        status = await client.get(f'/v1/workflows/{wid}/control')
        assert status.json()['status'] == 'PAUSED' and status.json()['checkpoint'] == 'batch:2'
        assert (await client.post(f'/v1/workflows/{wid}/resume')).status_code == 200
        assert json.loads(store.objects[key])['action'] == 'resume'
        temporal.status = 'COMPLETED'
        assert (await client.post(f'/v1/workflows/{wid}/pause')).status_code == 409
        assert (await client.post('/v1/workflows/unrelated/pause')).status_code == 404
