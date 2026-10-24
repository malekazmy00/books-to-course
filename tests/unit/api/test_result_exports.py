import io
import json
import zipfile
from datetime import datetime, timezone
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from b2c_contracts import ArtifactRef
from b2c_api.result_exports import result_exports_router, scoped_ref, build_result_archive

class Store:
    def __init__(self):
        self.s3 = self
        self.objects = {}
    def get_object(self, **kw):
        return {"Body": io.BytesIO(self.objects[kw["Key"]])}
    def list_objects_v2(self, **kw):
        return {"Contents": [{"Key": k} for k in self.objects if k.startswith(kw["Prefix"])]}
    def list_files(self, ref):
        prefix = ref.uri.removeprefix("s3://artifacts/")
        return [k[len(prefix):] for k in self.objects if k.startswith(prefix) and not k.endswith("_manifest.json")]
    def read(self, ref, name):
        return self.objects[ref.uri.removeprefix("s3://artifacts/") + name]
    def add(self, course, stage, key, files):
        prefix = f"{course}/{stage}/{key}/"
        ref = ArtifactRef(stage=stage, key=key, uri="s3://artifacts/" + prefix,
            sha256="b"*64, created_at=datetime.now(timezone.utc))
        self.objects[prefix+"_manifest.json"] = ref.model_dump_json().encode()
        for name, data in files.items():
            self.objects[prefix+name] = data
        return ref

@pytest.mark.asyncio
async def test_discovery_real_download_and_course_isolation():
    store = Store()
    ref = store.add("alpha", "generate-course-qa", "a"*64,
        {"course-qa.json": json.dumps({"lessons": [{"lesson_id": "l1", "questions": ["Q"]}]}).encode()})
    store.add("beta", "translate-content", "c"*64, {"translation.json": b'{"text":"private"}'})
    store.objects["alpha/course-settings.json"] = b'{"secret":"never-export"}'
    app = FastAPI()
    app.include_router(result_exports_router(lambda: store, "artifacts"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/courses/alpha/results")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        identity = response.json()["results"][0]["result_id"]
        preview = await client.get(f"/v1/courses/alpha/results/{identity}/file", params={"name":"course-qa.json", "preview":True})
        assert preview.json()["lessons"][0]["lesson_id"] == "l1"
        assert preview.headers["content-disposition"].startswith("inline;")
        export = await client.post("/v1/courses/alpha/results/export", json={"result_ids":[identity]})
        assert export.status_code == 200
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            assert not any("beta" in name or "settings" in name for name in archive.namelist())
            lesson_name = next(n for n in archive.namelist() if n.endswith("separate-units/lesson-0001.json"))
            lesson = json.loads(archive.read(lesson_name))
            assert lesson["course_id"] == "alpha"
            markdown = archive.read(lesson_name.replace('.json', '.md')).decode()
            assert '# l1' in markdown and 'Source SHA-256:' in markdown
            assert lesson["artifact_ref"]["sha256"] == ref.sha256
        units = await client.get(f"/v1/courses/alpha/results/{identity}/units", params={"name":"course-qa.json"})
        assert units.json()["total"] == 1
        single = await client.get(f"/v1/courses/alpha/results/{identity}/units/lesson-0001/file", params={"name":"course-qa.json"})
        assert single.status_code == 200
        assert single.json()["content"]["lesson_id"] == "l1"
        assert single.headers["content-disposition"].startswith("attachment;")
        assert (await client.get(f"/v1/courses/beta/results/{identity}/file", params={"name":"course-qa.json"})).status_code == 404
        assert (await client.get(f"/v1/courses/alpha/results/{identity}/file", params={"name":"../course-settings.json"})).status_code == 404
        assert (await client.post("/v1/courses/alpha/results/export", json={"result_ids":["translate-content:"+"c"*64]})).status_code == 404


def test_forged_manifest_cannot_escape_course_and_empty_course_has_no_fake_zip():
    store = Store()
    ref = store.add("alpha", "translate-content", "a"*64, {"translation.json": b"{}"})
    store.objects["alpha/translate-content/" + "a"*64 + "/_manifest.json"] = ref.model_copy(update={"uri": "s3://artifacts/beta/translate-content/"+"a"*64+"/"}).model_dump_json().encode()
    with pytest.raises(HTTPException) as failed:
        scoped_ref(store, "artifacts", "alpha", "translate-content:"+"a"*64)
    assert failed.value.status_code == 503
    with pytest.raises(HTTPException) as empty:
        build_result_archive(store, "artifacts", "empty", [])
    assert empty.value.status_code == 404


