from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from b2c_api.app import create_app
from b2c_api.serving import parse_single_byte_range
from b2c_api.srs import PostgresSRSStore
from b2c_contracts import CourseOutline, Question
from b2c_core import Settings


def course():
    outline = CourseOutline(course_id="demo", spine_book_id="main", supplementary_book_ids=[], requires_review=False, modules=[{
        "module_id":"m1", "title":"One", "order":1, "lessons":[
            {"lesson_id":"l1","title":"First","order":1,"spine_span":{"start_block":"b1","end_block":"b2","pages":[1,2]},"provenance":"headings"},
            {"lesson_id":"l2","title":"Next","order":2,"spine_span":{"start_block":"b3","end_block":"b4","pages":[3,4]},"provenance":"headings","prior_lessons":["l1"]}]}])
    question = Question(qid="q1", lesson_id="l1", purpose="end_of_lesson", type="mcq", bloom="apply", stem="Which?", options=["A","B","C"], answer_index=1, rationale="Because B", evidence_sids=["s1"], validation={})
    qa=SimpleNamespace(lesson_assets=[{"lesson_id":"l1", "questions":[question.model_dump(mode="json")]}], scopes=[])
    return SimpleNamespace(outline=outline, qa=qa, audio_assets={"l1": SimpleNamespace(segments=[])})


class Catalog:
    def __init__(self):
        self.s3=self
        self.payload=b"0123456789"
        self.body=None
    def load(self, course_id):
        if course_id != "demo": raise KeyError(course_id)
        return course()
    def mp3_location(self, course, lesson):
        if lesson not in course.audio_assets: raise KeyError(lesson)
        return "artifacts", "demo/audio/test.mp3"
    def head_object(self, **kwargs): return {"ContentLength":10}
    def get_object(self, **kwargs):
        data=self.payload
        if "Range" in kwargs:
            start,end=parse_single_byte_range(kwargs["Range"],len(data))
            data=data[start:end+1]
        self.body=BytesIO(data)
        return {"Body":self.body}


@pytest.mark.parametrize("value,expected", [("bytes=0-3",(0,3)),("bytes=5-",(5,9)),("bytes=-3",(7,9)),("bytes=0-99",(0,9))])
def test_byte_range_supported(value,expected):
    assert parse_single_byte_range(value,10)==expected


@pytest.mark.parametrize("value", ["bytes=0-1,3-4","bytes=+1-3","bytes=1- 3","bytes=10-","bytes=-0","bytes=4-2","items=0-1","bytes=-"])
def test_byte_range_invalid(value):
    with pytest.raises(ValueError): parse_single_byte_range(value,10)


async def test_audio_range_and_unsatisfiable_response():
    catalog=Catalog()
    app=create_app(settings=Settings(),serving_catalog=catalog)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/v1/lessons/l1/audio?course_id=demo",headers={"Range":"bytes=2-5"})
        assert response.status_code==206
        assert response.content==b"2345"
        assert response.headers["content-range"]=="bytes 2-5/10"
        assert catalog.body.closed
        response=await client.get("/v1/lessons/l1/audio?course_id=demo",headers={"Range":"bytes=10-"})
        assert response.status_code==416
        assert response.headers["content-range"]=="bytes */10"
        assert (await client.get("/v1/lessons/unknown/audio?course_id=demo")).status_code==404


async def test_review_quiz_hides_answers_and_uses_prior_lessons():
    app=create_app(settings=Settings(),serving_catalog=Catalog())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/v1/lessons/l2/review-quiz?course_id=demo")
        assert response.status_code==200
        item=response.json()["questions"][0]
        assert item["qid"]=="q1"
        assert not {"answer_index","answer_text","rationale","validation"}.intersection(item)
        assert (await client.get("/v1/lessons/l1/review-quiz?course_id=demo")).json()["questions"]==[]


async def test_srs_without_trusted_auth_fails_closed():
    app=create_app(settings=Settings(),serving_catalog=Catalog())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/v1/srs/due?course_id=demo",headers={"X-User-Id":"other"})
        assert response.status_code==503


async def test_srs_scopes_to_trusted_identity_and_released_course():
    class Store:
        def due(self,**kwargs):
            assert kwargs["user_id"]=="trusted"
            assert kwargs["course_id"]=="demo"
            return [{"question_id":"q1","due_at":datetime.now(UTC)}, {"question_id":"obsolete","due_at":datetime.now(UTC)}]
    app=create_app(settings=Settings(),serving_catalog=Catalog(),srs_store=Store(),learner_authenticator=lambda request:"trusted")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.get("/v1/srs/due?course_id=demo",headers={"X-User-Id":"spoof"})
        assert response.status_code==200
        assert [item["question_id"] for item in response.json()["items"]]==["q1"]
        assert (await client.get("/v1/srs/due?course_id=unreleased")).status_code==404


def test_srs_lock_identity_is_valid_postgres_text(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,args=None):
            if "pg_advisory" in sql:
                assert "\0" not in args[0]
                assert args[0]=='["a","demo","q1"]'
            return SimpleNamespace(fetchone=lambda:None)
    store=PostgresSRSStore("unused")
    monkeypatch.setattr(store,"_connect",lambda:Connection())
    entry,due=store.review(user_id="a",course_id="demo",question_id="q1",rating=3,reviewed_at=datetime.now(UTC))
    assert entry.user_id=="a"
    assert due.tzinfo is not None


def test_release_catalog_roundtrip_and_scope_rejection():
    import json
    import boto3
    from moto import mock_aws
    from b2c_core import ArtifactStore
    from b2c_api.serving import S3CourseServingCatalog
    with mock_aws():
        store=ArtifactStore(boto3.client("s3",region_name="us-east-1"),"artifacts")
        store.ensure_bucket()
        outline=course().outline
        envelope={"outline":outline.model_dump(mode="json"),"approved_at":datetime.now(UTC).isoformat(),
                  "approval_provenance":{"review_type":"human","reviewer_id":"fixture"}}
        approved=store.put("demo","approve-outline","a"*64,{"approved-outline.json":json.dumps(envelope).encode()})
        qa={"status":"final","expected_lesson_ids":["l1","l2"],"covered_lesson_ids":["l1","l2"],"scopes":[],"model_id":"fixture","prompt_version":"1"}
        qa_ref=store.put("demo","generate-course-qa","b"*64,{"course-qa.json":json.dumps(qa).encode()})
        catalog=S3CourseServingCatalog(store,"artifacts")
        assert catalog.released_course_ids()==[]
        release={"release_id":"r1","course_id":"demo","lesson_versions":{"l1":1,"l2":1},"model_versions":{},
                 "voice":{"provider":"fasih_v1","voice_id":"fixture","cloned":False},"human_reviewed_ratio":0,
                 "book_rights":{"main":"cleared"},"created_at":datetime.now(UTC).isoformat()}
        manifest={"release":release,"approved_outline_ref":approved.model_dump(mode="json"),"course_qa_ref":qa_ref.model_dump(mode="json")}
        store.put("demo","course-release","c"*64,{"serving-manifest.json":json.dumps(manifest).encode()})
        assert catalog.released_course_ids()==["demo"]
        assert catalog.load("demo").outline==outline
        with pytest.raises(ValueError): catalog.load("../demo")
        manifest["course_qa_ref"]["uri"]="s3://artifacts/other/generate-course-qa/"+"b"*64+"/"
        store.put("demo","course-release","d"*64,{"serving-manifest.json":json.dumps(manifest).encode()})
        with pytest.raises(ValueError,match="invalid generate-course-qa"): catalog.load("demo")
