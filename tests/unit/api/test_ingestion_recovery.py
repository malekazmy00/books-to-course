import io,json
from datetime import UTC,datetime
from types import SimpleNamespace
import httpx,pytest
from b2c_api.app import create_app
from b2c_contracts import ArtifactRef,ParsedBook,Block,PageRef
from b2c_core import Settings
from temporalio.service import RPCError,RPCStatusCode

WF='b2c-ingest-'+'1'*64
class Store:
 def __init__(self, matched=True, committed=True, wrong_book=False):
  self.bucket='artifacts'; self.s3=self; self.committed=committed
  self.ref=ArtifactRef(stage='ingest-parse-pdf',key='c'*64,uri='s3://artifacts/course-1/ingest-parse-pdf/'+('c'*64)+'/',sha256='d'*64,created_at=datetime.now(UTC))
  self.record={'course_id':'course-1','book_id':'book-main','stages':{'ingest':{'workflow_id':WF if matched else 'other','parsed_book_ref':self.ref.model_dump(mode='json')}}}
  self.book=ParsedBook(book_id='wrong' if wrong_book else 'book-main',title='Saved book',role='spine',page_count=1,toc_source='headings',blocks=[Block(block_id='book-main:1:0',type='paragraph',text='Saved extraction',refs=[PageRef(book_id='book-main',page_no=1)],section_path=[],ocr=True)],parser={'name':'fixture','version':'1','options_hash':'e'*64})
 def get_paginator(self,_): return self
 def paginate(self,**kw):return [{'Contents':[{'Key':'course-1/book-progress/book.json'}]}]
 def get_object(self,**kw):return {'Body':io.BytesIO(json.dumps(self.record).encode())}
 def exists(self,*args):return self.ref if self.committed else None
 def read(self,*args):return self.book.model_dump_json().encode()
class Temporal:
 def __init__(self,status=RPCStatusCode.NOT_FOUND):self.status=status
 def get_workflow_handle(self,_):
  async def describe():raise RPCError('fixture',self.status,b'')
  return SimpleNamespace(describe=describe)
async def connected(value):return value
async def request(store,route,status=RPCStatusCode.NOT_FOUND):
 app=create_app(settings=Settings(),artifact_store=store,temporal_connect=lambda _:connected(Temporal(status)))
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:return await c.get(route)
async def test_expired_history_reads_committed_ocr_status_and_text():
 st=Store(); r=await request(st,'/v1/ingestions/'+WF)
 assert r.status_code==200
 assert r.json()['status']=='COMPLETED' and r.json()['recovered_from_artifact']
 assert r.json()['completion_scope']=='extraction'
 r=await request(st,'/v1/ingestions/'+WF+'/text?course_id=course-1')
 assert r.status_code==200 and r.json()['blocks'][0]['text']=='Saved extraction'
async def test_unassociated_history_is_not_reported_as_completed():
 assert (await request(Store(matched=False),'/v1/ingestions/'+WF)).status_code==404
async def test_uncommitted_artifact_does_not_fake_completion():
 assert (await request(Store(committed=False),'/v1/ingestions/'+WF)).status_code==503
async def test_wrong_book_cannot_be_recovered():
 assert (await request(Store(wrong_book=True),'/v1/ingestions/'+WF)).status_code==503
async def test_wrong_course_cannot_view_extraction():
 assert (await request(Store(),'/v1/ingestions/'+WF+'/text?course_id=other')).status_code==404
async def test_real_temporal_outage_is_still_503():
 assert (await request(Store(),'/v1/ingestions/'+WF,RPCStatusCode.UNAVAILABLE)).status_code==503

async def test_legacy_registry_recovers_exact_index_input_reference():
 from b2c_api.ingestion_recovery import recover_extraction
 st=Store(); del st.record['stages']['ingest']['parsed_book_ref']
 st.record['stages']['index']={'workflow_id':'saved-index'}
 class Decoder:
  async def decode(self,_):return [{'course_id':'course-1','parsed_ref':st.ref.model_dump(mode='json')}]
 class LegacyTemporal:
  data_converter=Decoder()
  def get_workflow_handle(self,wid):
   assert wid=='saved-index'
   async def fetch_history():
    event=SimpleNamespace(HasField=lambda _:True,workflow_execution_started_event_attributes=SimpleNamespace(input=SimpleNamespace(payloads=[])))
    return SimpleNamespace(events=[event])
   return SimpleNamespace(fetch_history=fetch_history)
 r=await recover_extraction(st,LegacyTemporal(),WF,course_id='course-1')
 assert r['parsed_book']['ref']['key']==st.ref.key
 assert r['parsed_book']['block_count']==1

async def test_alias_recovers_immutable_legacy_book_after_history_expiry():
 st=Store()
 st.record['book_id']='book-'+('a'*24)
 st.record['artifact_book_id']='book-main'
 st.record['source_file_sha256']='a'*64
 r=await request(st,'/v1/ingestions/'+WF)
 assert r.status_code==200
 assert r.json()['result']['parsed_book']['ref']['sha256']==st.ref.sha256
 r=await request(st,'/v1/ingestions/'+WF+'/text?course_id=course-1')
 assert r.status_code==200 and r.json()['book_id']=='book-main'
