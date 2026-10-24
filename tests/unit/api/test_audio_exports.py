import io
import json
import zipfile
from types import SimpleNamespace
import boto3
import httpx
import pytest
from moto import mock_aws
from b2c_api.app import create_app
from b2c_api.audio_exports import audio_progress, ordered_audio_tracks, audio_zip, validate_audio_manifest
from b2c_core import ArtifactStore, Settings

WID = 'b2c-mp3-'+'a'*64

class Temporal:
    def __init__(self, state='RUNNING'): self.state=state
    def get_workflow_handle(self, wid): return self
    async def describe(self): return SimpleNamespace(status=SimpleNamespace(name=self.state))
    async def result(self): raise RuntimeError('failed')

@pytest.fixture
def environment():
    with mock_aws():
        store=ArtifactStore(boto3.client('s3', region_name='us-east-1')); store.ensure_bucket()
        tracks=[]
        for number,(lesson,kind) in enumerate([('lesson2','lesson_attachment'),('lesson1','lesson_source_text'),('lesson2','lesson_source_text')]):
            ref=store.put('revolution','generate-mp3-track',str(number+1)*64,{'track.mp3':b'ID3-audio-'+str(number).encode()})
            tracks.append({'track_id':lesson+'-'+kind,'lesson_id':lesson,'kind':kind,'artifact_ref':ref.model_dump(mode='json')})
        record={'workflow_id':WID,'course_id':'revolution','tracks':tracks,'track_count':4,
                'lesson_order':['lesson1','lesson2'], 'failed_tracks':1,'failed_track_ids':['failed'],
                'current_track':{'lesson_id':'lesson2','title':'Title','completed_segments':8,'remaining_segments':2,'failed_segments':1},
                'updated_at':'2026-10-11T20:00:00Z'}
        store.s3.put_object(Bucket=store.bucket,Key=f'_system/audio-progress/{WID}.json',Body=json.dumps(record).encode())
        store.s3.put_object(Bucket=store.bucket,Key='revolution/course-settings.json',Body=json.dumps({'name':'Rev'}).encode())
        temporal=Temporal()
        async def connect(_):return temporal
        app=create_app(settings=Settings(),artifact_store=store,temporal_connect=connect)
        yield store,record,app,temporal

@pytest.mark.asyncio
async def test_live_and_failed_status_project_persistent_segment_progress(environment):
    store,record,app,temporal=environment
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        value=(await client.get(f'/v1/audio/{WID}')).json()
        assert value['progress']['current_track']['completed_segments']==8
        assert value['progress']['remaining_tracks']==1
        assert value['progress']['failed_tracks']==1
        temporal.state='FAILED'
        value=(await client.get(f'/v1/audio/{WID}')).json()
        assert value['progress']['completed_tracks']==3
        assert value['progress']['current_track']['remaining_segments']==2

@pytest.mark.asyncio
async def test_course_list_download_scope_and_complete_zip(environment):
    store,record,app,_=environment
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        listing=(await client.get('/v1/courses/revolution/audio')).json()
        assert listing['course_name']=='Rev'
        assert [t['filename'] for t in listing['workflows'][0]['tracks']]==['Rev1.mp3','Rev2.mp3','Att-Rev2.mp3']
        assert (await client.get('/v1/courses/other/audio')).json()['workflows']==[]
        assert (await client.get(f'/v1/courses/other/audio/zip?workflow_id={WID}')).status_code==404
        assert (await client.get(f'/v1/courses/revolution/audio/zip?workflow_id={WID}')).status_code==409
        record['track_count']=3
        store.s3.put_object(Bucket=store.bucket,Key=f'_system/audio-progress/{WID}.json',Body=json.dumps(record).encode())
        response=await client.get(f'/v1/courses/revolution/audio/zip?workflow_id={WID}')
        assert response.status_code==200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist()==['Rev1.mp3','Rev2.mp3','Att-Rev2.mp3','manifest.json']
            assert archive.read('Rev1.mp3')==b'ID3-audio-1'
        track=await client.get(f'/v1/audio/{WID}/tracks/lesson2-lesson_attachment')
        assert track.content==b'ID3-audio-0'
        assert 'Att-Rev2.mp3' in track.headers['content-disposition']

@pytest.mark.asyncio
async def test_cross_course_corrupt_track_is_rejected(environment):
    store,record,app,_=environment
    record['tracks'][0]['artifact_ref']['uri']=record['tracks'][0]['artifact_ref']['uri'].replace('/revolution/','/other/')
    store.s3.put_object(Bucket=store.bucket,Key=f'_system/audio-progress/{WID}.json',Body=json.dumps(record).encode())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get(f'/v1/audio/{WID}/manifest')).status_code==503
        assert (await client.get('/v1/courses/revolution/audio')).status_code==503


def test_regenerated_single_lesson_retains_outline_number():
    record={'tracks':[{'track_id':'selected','lesson_id':'lesson20','kind':'lesson_source_text'}],
            'lesson_order':['lesson'+str(n) for n in range(1,21)]}
    assert ordered_audio_tracks(record,'Rev')[0]['filename']=='Rev20.mp3'


def test_safe_archive_names_and_duplicate_track_rejection(environment):
    store,record,_,_=environment
    names=[t['filename'] for t in ordered_audio_tracks(record,'../../Rev\r\n"')]
    assert names==['Rev1.mp3','Rev2.mp3','Att-Rev2.mp3']
    record['tracks'].append(record['tracks'][0])
    with pytest.raises(ValueError,match='duplicate'):
        validate_audio_manifest(record,store.bucket,'revolution')

@pytest.mark.asyncio
async def test_regenerate_rejects_multiple_lessons_before_queuing(environment):
    _,record,app,_=environment
    ref=record['tracks'][0]['artifact_ref']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post('/v1/courses/revolution/audio/regenerate',json={
            'source_artifact_ref':ref,'source_kind':'course','language':'ar',
            'lesson_ids':['a','b'],'include_extra_scopes':False})
        assert result.status_code==422

@pytest.mark.asyncio
async def test_original_request_resolves_saved_input_without_operator_refs(environment, monkeypatch):
    from b2c_orchestration.audio import GenerateMP3Input
    import b2c_api.routers.audio as module
    store,record,app,_=environment
    ref=record['tracks'][0]['artifact_ref']
    inp=GenerateMP3Input(course_id='revolution',source_artifact_ref=ref,source_kind='course',language='ar')
    store.s3.put_object(Bucket=store.bucket,Key=f'_system/audio-inputs/{WID}.json',Body=inp.model_dump_json().encode())
    monkeypatch.setattr(module,'validate_mp3_input_lineage',lambda store,inp:None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        value=await client.get(f'/v1/audio/{WID}/request')
        assert value.status_code==200
        assert value.json()['course_id']=='revolution'
        assert value.json()['request']['source_artifact_ref']==ref
        assert 'activity_task_queue' not in value.json()['request']
        assert value.json()['source_version']=='original_validated'

@pytest.mark.asyncio
async def test_single_lesson_regeneration_delegates_exact_source_scoped_request(environment):
    _,record,app,_=environment
    route=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/v1/courses/{course_id}/audio/regenerate')
    captured=[]
    async def fake_generate(cid,request):
        captured.append((cid,request.model_dump(mode='json')))
        return {'course_id':cid,'workflow_id':WID}
    cells=dict(zip(route.__code__.co_freevars,route.__closure__))
    cells['generate_course_audio'].cell_contents=fake_generate
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        result=await client.post('/v1/courses/revolution/audio/regenerate',json={
            'source_artifact_ref':record['tracks'][0]['artifact_ref'], 'source_kind':'course','language':'ar',
            'lesson_ids':['lesson2'],'include_extra_scopes':False})
        assert result.status_code==202
        assert captured[0][0]=='revolution'
        assert captured[0][1]['lesson_ids']==['lesson2']
        assert captured[0][1]['include_extra_scopes'] is False
        assert captured[0][1]['source_artifact_ref']==record['tracks'][0]['artifact_ref']
