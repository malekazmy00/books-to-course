import io
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from b2c_api.course_library import course_library_router

class Store:
    def __init__(self):
        self.objects={}
        self.s3=self
    def list_objects_v2(self, **args):
        if "ContinuationToken" not in args:
            return {"CommonPrefixes":[{"Prefix":"alpha/"}],"NextContinuationToken":"two"}
        return {"CommonPrefixes":[{"Prefix":"beta/"}]}
    def get_object(self, **args):
        return {"Body":io.BytesIO(self.objects[args["Key"]])}
    def put_object(self, **args):
        self.objects[args["Key"]]=args["Body"]

@pytest.mark.asyncio
async def test_global_search_and_reusable_course_memberships():
    store=Store()
    book={"book_id":"book-a","file_sha256":"a"*64,"title":"Intelligence"}
    async def books(cid):
        return {"books":[dict(book,course_id=cid)]}
    app=FastAPI()
    app.include_router(course_library_router(lambda:store,"artifacts",books))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        result=(await client.get("/v1/library/books?q=intelligence")).json()
        assert len(result["books"])==1
        assert result["books"][0]["available_in_courses"]==["alpha","beta"]
        assert (await client.get("/v1/library/books?q=absent")).json()["books"]==[]
        created=(await client.post("/v1/courses",json={"name":"Wrong name"})).json()
        cid=created["course_id"]
        attached=await client.put(f"/v1/courses/{cid}/memberships/book-a",
            json={"source_course_id":"alpha","book_id":"book-a","role":"supplementary"})
        assert attached.status_code==200
        before=attached.json()["books"]
        renamed=await client.patch(f"/v1/courses/{cid}",json={"name":"Correct name"})
        assert renamed.json()["course_id"]==cid
        assert renamed.json()["books"]==before
        assert store.objects.get("alpha/course-settings.json") is None
        invalid=await client.put(f"/v1/courses/{cid}/memberships/book-b",
            json={"source_course_id":"alpha","book_id":"book-b","role":"spine"})
        assert invalid.status_code==404

@pytest.mark.asyncio
async def test_promoting_spine_preserves_other_courses_and_sources():
    store=Store()
    async def books(cid):
        return {"books":[{"book_id":"a","title":"A"},{"book_id":"b","title":"B"}]}
    app=FastAPI()
    app.include_router(course_library_router(lambda:store,"artifacts",books))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        for bid in ("a","b"):
            response=await client.put(f"/v1/courses/new/memberships/{bid}",
                json={"source_course_id":"alpha","book_id":bid,"role":"spine"})
            assert response.status_code==200
        members=response.json()["books"]
        assert [(m["book_id"],m["role"]) for m in members]==[("a","supplementary"),("b","spine")]
        assert all(m["source_course_id"]=="alpha" for m in members)
        assert (await client.patch("/v1/courses/new",json={"name":"   "})).status_code==422
        assert "alpha/course-settings.json" not in store.objects

@pytest.mark.asyncio
async def test_put_course_name_and_content_bound_book_name_preserve_progress():
    from b2c_api.course_library import apply_book_names
    store = Store()
    original = {"book_id":"book-a", "file_sha256":"a"*64, "title":"Original",
        "workflow_id":"live-ingest", "source_artifact_ref":{"key":"immutable-source"}}
    other = dict(original, book_id="book-b", file_sha256="b"*64, title="Other")
    async def books(cid):
        records = [dict(original), dict(other)]
        return {"books":apply_book_names(store, "artifacts", cid, records)}
    app = FastAPI()
    app.include_router(course_library_router(lambda:store, "artifacts", books))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        store.objects["alpha/course-settings.json"] = json.dumps({
            "course_id":"alpha", "name":"Old", "books":[dict(original, source_course_id="alpha", role="spine")]}).encode()
        response = await client.put("/v1/courses/alpha", json={"name":"المقرر الجديد"})
        assert response.status_code == 200
        assert response.json()["books"][0]["book_id"] == "book-a"
        response = await client.put("/v1/courses/alpha/books/book-a/name", json={"name":"الكتاب الجديد"})
        assert response.status_code == 200
        for cid in ("alpha", "beta"):
            result = await books(cid)
            assert result["books"][0]["title"] == "الكتاب الجديد"
            assert result["books"][0]["workflow_id"] == "live-ingest"
            assert result["books"][0]["source_artifact_ref"] == original["source_artifact_ref"]
            assert result["books"][0]["file_sha256"] == original["file_sha256"]
            assert result["books"][1]["title"] == "Other"
        members = (await client.get("/v1/courses/alpha/memberships")).json()
        assert members["books"][0]["title"] == "الكتاب الجديد"
        assert members["name"] == "المقرر الجديد"
        assert (await client.put("/v1/courses/alpha/books/missing/name", json={"name":"X"})).status_code == 404
        assert (await client.put("/v1/courses/alpha/books/book-a/name", json={"name":"   "})).status_code == 422
        assert (await client.put("/v1/courses/alpha", json={"name":"   "})).status_code == 422
        assert original["title"] == "Original"
