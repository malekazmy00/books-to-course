import io
import json
from types import SimpleNamespace

import pytest
from b2c_orchestration import controls

class Store:
    bucket = 'artifacts'
    def __init__(self):
        self.s3 = self
        self.objects = {}
    def get_object(self, **kw):
        return {'Body': io.BytesIO(self.objects[kw['Key']])}
    def put_object(self, **kw):
        self.objects[kw['Key']] = kw['Body']


def test_pause_acknowledged_only_at_checkpoint_and_resume_same_activity(monkeypatch):
    store = Store()
    wid = 'b2c-translate-' + '1' * 64
    key = controls.control_key(wid)
    store.objects[key] = json.dumps({'workflow_id': wid, 'action': 'pause_after_checkpoint', 'state': 'PAUSE_REQUESTED'}).encode()
    from temporalio.exceptions import ApplicationError
    with pytest.raises(ApplicationError) as error:
        controls.wait_at_checkpoint(store, wid, 'saved-batch:2')
    assert error.value.type == 'StageCheckpointPaused' and error.value.non_retryable
    record = json.loads(store.objects[key])
    assert record['state'] == 'PAUSED' and record['checkpoint'] == 'saved-batch:2'
    record.update(action='resume', state='ACTIVE')
    store.objects[key] = json.dumps(record).encode()
    controls.wait_at_checkpoint(store, wid, 'saved-batch:2')


def test_missing_control_does_not_pause_or_write():
    store = Store()
    controls.wait_at_checkpoint(store, 'b2c-index-'+'2'*64, 'book:a')
    assert store.objects == {}


def test_storage_error_prevents_next_unit():
    store = Store()
    store.get_object = lambda **kw: (_ for _ in ()).throw(OSError('storage unavailable'))
    with pytest.raises(OSError):
        controls.wait_at_checkpoint(store, 'b2c-mp3-'+'3'*64, 'track:lesson1')

@pytest.mark.asyncio
async def test_workflow_wait_releases_activity_then_restarts_from_checkpoint(monkeypatch):
    from temporalio import workflow
    from temporalio.exceptions import ActivityError, ApplicationError
    owner = SimpleNamespace()
    events = []
    async def execute(name, inp, **kw):
        events.append('activity')
        if len(events) == 1:
            error = ActivityError('paused', scheduled_event_id=1, started_event_id=2,
                identity='test', activity_type=name, activity_id='1', retry_state=None)
            error.__cause__ = ApplicationError('paused', type='StageCheckpointPaused', non_retryable=True)
            raise error
        return {'completed': True}
    async def wait(condition):
        assert condition() is False
        events.append('workflow_wait')
        owner._stage_resume_requested = True
        assert condition() is True
    monkeypatch.setattr(workflow, 'execute_activity', execute)
    monkeypatch.setattr(workflow, 'wait_condition', wait)
    result = await controls.execute_checkpointed(owner, 'translate_content', {'checkpoint':'saved'})
    assert result == {'completed': True}
    assert events == ['activity', 'workflow_wait', 'activity']
