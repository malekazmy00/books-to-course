from types import SimpleNamespace as NS
from datetime import datetime, timezone
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from b2c_api.workflow_resume import workflow_resume_router
from b2c_orchestration.audio import GenerateMP3Input

ID='b2c-mp3-'+'a'*64

def setup(status='FAILED', owner=True, name='GenerateMP3Workflow'):
    inp=GenerateMP3Input(course_id='c',source_artifact_ref={'stage':'translate-content','uri':'s3://artifacts/c/t','sha256':'a'*64,'key':'k','created_at':datetime.now(timezone.utc).isoformat()},source_kind='book',language='ar')
    calls=[]
    attrs=NS(workflow_type=NS(name=name),task_queue=NS(name='heavy'),input=NS(payloads=['saved']))
    event=NS(workflow_execution_started_event_attributes=attrs,HasField=lambda _:True)
    class Handle:
        async def describe(self):return NS(status=NS(name=status))
        async def fetch_history(self):return NS(events=[event])
    class Converter:
        async def decode(self,payloads,types):
            assert payloads==['saved'] and types==[GenerateMP3Input]
            return [inp]
    class Client:
        data_converter=Converter()
        def get_workflow_handle(self,wid):assert wid==ID;return Handle()
        async def start_workflow(self,run,arg,**kwargs):calls.append((arg,kwargs))
    async def client():return Client()
    async def verify(course,book,stage,old):
        if not owner:raise HTTPException(409,'wrong book')
        assert (course,book,stage,old)==('c','b','audio',ID)
    async def remember(*args):calls.append(args)
    app=FastAPI();app.include_router(workflow_resume_router(client,verify,remember))
    return TestClient(app),calls,inp

def test_failed_audio_reuses_exact_sources_and_queue_deterministically():
    client,calls,inp=setup()
    first=client.post('/v1/workflows/'+ID+'/resume',json={'book_id':'b'})
    second=client.post('/v1/workflows/'+ID+'/resume',json={'book_id':'b'})
    assert first.status_code==202 and first.json()==second.json()
    assert calls[0][0] is inp and calls[0][1]['task_queue']=='heavy'
    assert first.json()['resumption_mode']=='reuse_saved_tracks'

@pytest.mark.parametrize('status',['RUNNING','COMPLETED'])
def test_live_or_successful_stage_cannot_be_restarted(status):
    client,calls,_=setup(status)
    assert not client.get('/v1/workflows/'+ID+'/resume-capability').json()['resume_available']
    assert client.post('/v1/workflows/'+ID+'/resume',json={'book_id':'b'}).status_code==409
    assert not calls

def test_wrong_book_and_workflow_type_are_rejected():
    for kwargs in ({'owner':False},{'name':'OtherWorkflow'}):
        client,calls,_=setup(**kwargs)
        assert client.post('/v1/workflows/'+ID+'/resume',json={'book_id':'b'}).status_code==409
        assert not calls

def test_unrelated_workflow_is_not_supported():
    client,calls,_=setup()
    assert client.get('/v1/workflows/ai-company/resume-capability').status_code==404
    assert not calls
