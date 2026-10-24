import json
import io
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest
from b2c_contracts import ArtifactRef
from b2c_translation import SourceSegment
from b2c_orchestration.translation import TranslateContentInput, TranslationActivities
class SelectiveGateway:
    model_id='test-model'
    def __init__(self,failing):
        self.failing=failing; self.ids=[]
    def structured(self,messages,schema,**kwargs):
        entries=json.loads(messages[1]['content'])['segments']
        self.ids.extend(x['segment_id'] for x in entries)
        return schema(segments=[{'segment_id':x['segment_id'],'text':'wrong 6' if x['segment_id'] in self.failing else 'correct 5'} for x in entries])

class Store:
    bucket='artifacts'
    def __init__(self):
        self.s3=self; self.objects={}; self.artifacts={}
    def put_object(self,**args): self.objects[args['Key']]=args['Body']
    def list_objects_v2(self,**args):
        return {'Contents':[{'Key':k} for k in self.objects if k.startswith(args['Prefix'])],'IsTruncated':False}
    def get_object(self,**args): return {'Body':io.BytesIO(self.objects[args['Key']])}
    def get_or_compute(self,course,stage,key,compute):
        hit=key in self.artifacts
        if not hit:self.artifacts[key]=compute()
        return ArtifactRef(stage=stage,key=key,uri=f's3://artifacts/{course}/{stage}/{key}',sha256=key,created_at=datetime.now(UTC)),hit


class InterruptedStore(Store):
    fail_once = True
    partial_progress = None
    def put_object(self, **args):
        if '/translation-progress/' in args['Key']:
            payload=json.loads(args['Body'])
            if payload.get('event')=='batch_partial_completed':
                self.partial_progress=payload
                if self.fail_once:
                    self.fail_once=False
                    raise OSError('simulated progress write interruption after checkpoint')
        super().put_object(**args)


def test_partial_original_checkpoint_survives_progress_failure_and_resumes_only_bad(monkeypatch):
    import b2c_orchestration.translation as mod
    sources=[SourceSegment(segment_id=sid,text='The value is 5.',citations=['book:p1']) for sid in ('good','bad')]
    monkeypatch.setattr(mod,'artifact_source_identity',lambda store, ref: {'source_parsed_sha256':ref.sha256, 'source_file_sha256':None})
    monkeypatch.setattr(mod,'translation_source_segments',lambda *_:sources)
    monkeypatch.setattr(mod,'translation_manual_overrides',lambda *_:[])
    wid='b2c-translate-'+'4'*64
    monkeypatch.setattr(mod.activity,'info',lambda:SimpleNamespace(workflow_id=wid))
    monkeypatch.setattr(mod.activity,'heartbeat',lambda *_:None)
    ref=ArtifactRef(stage='ingest-parse-pdf',key='source',uri='s3://artifacts/c/source',sha256='a'*64,created_at=datetime.now(UTC))
    inp=TranslateContentInput(course_id='c',source_artifact_ref=ref.model_dump(mode='json'),source_kind='book',source_language='en',target_language='ar')
    store=InterruptedStore()
    first=SelectiveGateway({'bad'})
    with pytest.raises(Exception):
        TranslationActivities(store,first).translate(inp)
    assert first.ids==['good','bad'], 'storage failure must not trigger further inference'
    checkpoint=[json.loads(v) for k,v in store.objects.items() if '/translation-checkpoints/' in k]
    assert len(checkpoint)==1 and [x['segment_id'] for x in checkpoint[0]['segments']]==['good']
    assert store.partial_progress['successful_segments']==1
    assert store.partial_progress['checkpointed_segments']==1
    assert store.partial_progress['completed_batches']==[], 'one good item is not a completed batch'
    second=SelectiveGateway(set())
    result=TranslationActivities(store,second).translate(inp)
    assert second.ids==['bad']
    assert result['status']=='completed' and result['segment_count']==2


def test_translation_pause_occurs_after_saved_batch_before_next_provider_request(monkeypatch):
    import time
    import b2c_orchestration.translation as mod
    from b2c_orchestration.controls import control_key
    sources = [SourceSegment(segment_id=f's{i}', text='The value is 5.') for i in range(8)]
    monkeypatch.setattr(mod, 'artifact_source_identity', lambda store, ref: {'source_parsed_sha256':ref.sha256, 'source_file_sha256':None})
    monkeypatch.setattr(mod, 'translation_source_segments', lambda *_: sources)
    monkeypatch.setattr(mod, 'translation_manual_overrides', lambda *_: [])
    wid = 'b2c-translate-' + '7' * 64
    monkeypatch.setattr(mod.activity, 'info', lambda: SimpleNamespace(workflow_id=wid))
    monkeypatch.setattr(mod.activity, 'heartbeat', lambda *_: None)
    store = Store(); gateway = SelectiveGateway(set())
    key = control_key(wid)
    store.objects[key] = json.dumps({'workflow_id':wid, 'action':'pause_after_checkpoint', 'state':'PAUSE_REQUESTED'}).encode()
    ref = ArtifactRef(stage='ingest-parse-pdf', key='source', uri='s3://artifacts/c/source', sha256='a'*64, created_at=datetime.now(UTC))
    inp = TranslateContentInput(course_id='c', source_artifact_ref=ref.model_dump(mode='json'), source_kind='book', source_language='en', target_language='ar')
    from temporalio.exceptions import ApplicationError
    with pytest.raises(ApplicationError) as paused:
        TranslationActivities(store, gateway).translate(inp)
    assert paused.value.type == 'StageCheckpointPaused'
    assert paused.value.non_retryable
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.api.failure.v1 import Failure
    failure = Failure()
    DefaultFailureConverter().to_failure(paused.value, DefaultPayloadConverter(), failure)
    assert failure.application_failure_info.type == 'StageCheckpointPaused'
    assert failure.application_failure_info.non_retryable
    assert gateway.ids == ['s0']
    saved = [json.loads(v) for k,v in store.objects.items() if '/translation-checkpoints/' in k]
    assert len(saved) == 1 and len(saved[0]['segments']) == 1
    record = json.loads(store.objects[key]); record.update(action='resume', state='ACTIVE')
    store.objects[key] = json.dumps(record).encode()
    result = TranslationActivities(store, gateway).translate(inp)
    assert gateway.ids == [f's{i}' for i in range(8)]
    assert result['segment_count'] == 8


def test_translation_heartbeats_continue_during_blocking_provider_call(monkeypatch):
    import contextvars
    import time
    import b2c_orchestration.translation as mod

    activity_marker = contextvars.ContextVar("activity_marker", default=None)
    activity_marker.set("inside-temporal-activity")
    received = []
    monkeypatch.setattr(mod.activity, "heartbeat", lambda detail: received.append((detail, activity_marker.get())))

    stop, thread = mod._start_activity_heartbeat_pump("workflow-test", interval_seconds=0.01)
    try:
        # Simulates a synchronous provider request that does not call the
        # normal progress callback until the request has returned.
        time.sleep(0.06)
    finally:
        stop.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert received
    assert all(context == "inside-temporal-activity" for _, context in received)
    assert all(detail == {"workflow_id": "workflow-test", "kind": "translation_liveness"}
               for detail, _ in received)


@pytest.mark.parametrize(
    "patches,expected",
    [
        ([True, False, False], {"heartbeat": timedelta(minutes=40), "start": timedelta(hours=24), "schedule": timedelta(hours=48)}),
        ([True, True, True], {"heartbeat": timedelta(minutes=2), "start": timedelta(hours=72), "schedule": timedelta(days=7)}),
        ([False, False, False], {"heartbeat": None, "start": timedelta(hours=24), "schedule": timedelta(hours=48)}),
    ],
)
def test_translation_workflow_timeout_patches_preserve_old_histories(monkeypatch, patches, expected):
    import asyncio
    import b2c_orchestration.translation as mod

    outcomes = iter(patches)
    captured = {}

    async def execute(owner, name, inp, **options):
        captured.update(options)
        return options

    monkeypatch.setattr(mod.workflow, "patched", lambda _: next(outcomes))
    monkeypatch.setattr(mod, "execute_checkpointed", execute)
    inp = TranslateContentInput(
        course_id="c", source_artifact_ref={}, source_kind="book",
        source_language="en", target_language="ar")
    asyncio.run(mod.TranslateContentWorkflow().run(inp))

    assert captured["heartbeat_timeout"] == expected["heartbeat"] if expected["heartbeat"] else "heartbeat_timeout" not in captured
    assert captured["start_to_close_timeout"] == expected["start"]
    assert captured["schedule_to_close_timeout"] == expected["schedule"]
