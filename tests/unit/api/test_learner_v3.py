"""Learner API v3 (/v3/learner): accepted artifacts only, clean empty states, answers hidden (R0).

Fixtures register artifacts in the stage-output registry (fake Postgres) as accepted or candidate
and keep their files in a dict-backed store. Answers in the episode script carry sentinels; no GET
response may contain one.
"""
from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from b2c_contracts import ArtifactRef, GateResult, StageOutputRecord
from b2c_core import Settings
from b2c_core.stage_registry import StageOutputRegistry
from fastapi.routing import APIRoute

from b2c_api.app import create_app
from b2c_api.learner.openapi import learner_openapi, learner_openapi_text
from b2c_api.learner.router import learner_router
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
SHA = "a" * 40
BUCKET = "artifacts"
SENTINELS = ("ANSWER-SENTINEL-1", "ANSWER-SENTINEL-2", "SPOKEN-ANSWER-SENTINEL")
FORBIDDEN_KEYS = {"answer", "answer_index", "answer_text", "correct", "rationale"}
MP3 = bytes(range(256)) * 40  # 10240 bytes


def bid(seq: int) -> str:
    return f"bk:b{seq:05d}"


def outline_json(course_id: str = "c1") -> dict:
    def lesson(lid, a, b, order, episode, title):
        return dict(lesson_id=lid, order=order, level=2, kind="section", source="pdf_outline", source_title=title,
                    display_title=title, title_source="pdf_outline", anchor_block=bid(a),
                    spine_span=dict(start_block=bid(a), end_block=bid(b), pages=[1, 2]), word_count=900,
                    size_status="ok", episode=episode, episode_title=title)
    return dict(format="course-outline/2", course_id=course_id, spine_book_id="bk", block_count=6,
                builder={"name": "b2c-outline-v2", "version": "1"}, structure_source="pdf_outline",
                size_policy=dict(policy_id="words-600-4000", min_words=600, max_words=4000),
                course_intro_episode=True,
                modules=[dict(module_id="ch01-abc123", order=1, level=1, source="pdf_outline",
                              source_title="Chapter 1", display_title="Chapter 1", title_source="pdf_outline",
                              anchor_block=bid(0), lessons=[lesson("ch01-abc123.l1", 0, 2, 1, 1, "Hegemony"),
                                                            lesson("ch01-abc123.l2", 3, 5, 2, 2, "Consent")])])


def script_json(episode: int = 1) -> dict:
    return {
        "episode": episode, "title": "الهيمنة", "book_title": "Prison Notebooks", "language": "ar",
        "lesson_id": "ch01-abc123.l1",
        "sections": [
            {"section_id": "intro", "kind": "intro", "segments": [
                {"segment_id": "i1", "voice": "course", "text": "الحلقة الأولى: الهيمنة", "start_s": 0, "end_s": 5}]},
            {"section_id": "book", "kind": "book_text", "segments": [
                {"segment_id": "b1", "voice": "author", "text": "نص المؤلف عن الهيمنة.", "start_s": 5, "end_s": 30,
                 "pages": [12]},
                {"segment_id": "b2", "voice": "course", "role": "note", "text": "ملاحظة من الكورس: المصطلح الأصلي hegemony",
                 "start_s": 30, "end_s": 40},
                {"segment_id": "b3", "voice": "external", "text": "بحسب مصدر خارجي: ...",
                 "source_label": "Stanford Encyclopedia", "start_s": 40, "end_s": 50}]},
            {"section_id": "sum", "kind": "summary", "segments": [
                {"segment_id": "s1", "voice": "course", "text": "ملخص الدرس.", "start_s": 50, "end_s": 60}]},
            {"section_id": "terms", "kind": "key_terms", "segments": [
                {"segment_id": "t1", "voice": "course", "text": "الهيمنة (hegemony)", "start_s": 60, "end_s": 66}]},
            {"section_id": "qa", "kind": "questions", "segments": [
                {"segment_id": "q1s", "voice": "course", "role": "question", "text": "ما الهيمنة؟", "start_s": 66,
                 "end_s": 70},
                {"segment_id": "q1a", "voice": "course", "role": "answer", "text": "SPOKEN-ANSWER-SENTINEL",
                 "start_s": 75, "end_s": 80},
                {"segment_id": "q2s", "voice": "course", "role": "question", "text": "اختر التعريف", "start_s": 80,
                 "end_s": 84},
                {"segment_id": "q2a", "voice": "course", "text": "الإجابة: قيادة بالرضا ANSWER-SENTINEL-2", "start_s": 88,
                 "end_s": 92}]},
        ],
        "key_terms": [{"term": "الهيمنة", "original": "hegemony", "definition": "قيادة بالرضا",
                       "concept_id": "cpt_000000000001"}],
        "questions": [
            {"question_id": "q1", "prompt": "ما الهيمنة؟", "answer": "ANSWER-SENTINEL-1",
             "question_segment_id": "q1s", "answer_segment_id": "q1a"},
            {"question_id": "q2", "prompt": "اختر التعريف", "options": ["قسر", "قيادة بالرضا"], "answer_index": 1,
             "question_segment_id": "q2s"},
        ],
    }


def concept_layer_json() -> dict:
    span = {"block_id": "bk:b00001", "pages": [3], "quote": "Hegemony is consent armoured by coercion."}
    return {"format": "b2c.concept-layer/1", "book_id": "bk", "language": "en", "created_at": NOW.isoformat(),
            "termbase_basis": "approved", "concepts": [
                {"concept_id": "cpt_000000000001", "term": "hegemony", "kind": "term", "termbase_status": "approved",
                 "introduced_at": span, "first_lesson_id": "ch01-abc123.l1", "occurrences": 4,
                 "author_definition": {"spans": [span], "gloss": "leadership by consent"},
                 "relations": [{"target_id": "cpt_000000000002", "relation": "prerequisite", "evidence": [span]}]},
                {"concept_id": "cpt_000000000002", "term": "consent", "kind": "term", "termbase_status": "approved",
                 "introduced_at": span, "first_lesson_id": "ch01-abc123.l2", "occurrences": 2}],
            "lessons": []}


class Body:
    def __init__(self, data: bytes):
        self._io = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._io.read(n)

    def close(self) -> None:
        pass


class Store:
    """Dict-backed artifact store with the S3 calls the learner routes make."""
    bucket = BUCKET

    def __init__(self):
        self.s3 = self
        self.objects: dict[str, bytes] = {}

    def put_files(self, ref: ArtifactRef, files: dict[str, bytes]) -> None:
        prefix = ref.uri.removeprefix(f"s3://{BUCKET}/")
        for name, data in files.items():
            self.objects[prefix + name] = data

    def read_json(self, ref: ArtifactRef, name: str):
        key = ref.uri.removeprefix(f"s3://{BUCKET}/") + name
        if key not in self.objects:
            raise KeyError(key)
        return json.loads(self.objects[key])

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[Key]), "ETag": '"mp3etag"'}

    def get_object(self, Bucket, Key, Range=None):
        data = self.objects[Key]
        if Range:
            a, b = Range.removeprefix("bytes=").split("-")
            data = data[int(a):int(b) + 1]
        return {"Body": Body(data)}


class World:
    def __init__(self):
        clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
        self.registry = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect,
                                            clock=lambda: next(clock))
        self.store = Store()
        self.n = 0

    def add(self, course_id: str, stage: str, files: dict[str, bytes], *, accepted: bool = True,
            config: dict | None = None, created: datetime = NOW) -> ArtifactRef:
        self.n += 1
        key = f"{self.n:064x}"
        ref = ArtifactRef(stage=stage, key=key, uri=f"s3://{BUCKET}/{course_id}/{stage}/{key}/", sha256="c" * 64,
                          created_at=created + timedelta(minutes=self.n))
        self.store.put_files(ref, files)
        gate = GateResult(stage=stage, artifact=ref, gate_id="g.one", gate_version="v1", kind="deterministic",
                          passed=True, decider="system", decided_by="policy", decided_at=NOW)
        self.registry.register(StageOutputRecord(
            course_id=course_id, artifact=ref, code_revision=SHA, required_gates=["g.one"], gate_results=[gate],
            config=config or {}, status="candidate", status_changed_at=NOW, status_changed_by="worker"))
        if accepted:
            self.registry.transition(course_id, stage, key, "accepted", actor="worker", code_revision=SHA)
        return ref


def enc(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def world():
    w = World()
    w.add("c1", "build-outline-v2", {"course-outline-v2.json": enc(outline_json())},
          config={"book_title": "Prison Notebooks"})
    w.add("c1", "episode-script", {"episode-script.json": enc(script_json(1))}, config={"episode": 1})
    # A candidate (not accepted) script for episode 2 must stay invisible.
    w.add("c1", "episode-script", {"episode-script.json": enc(script_json(2) | {"title": "CANDIDATE"})},
          accepted=False, config={"episode": 2})
    w.add("c1", "episode-audio", {"episode.mp3": MP3, "timings.json": enc({"duration_s": 95.5, "segments": [
        {"segment_id": "b1", "start_s": 6.0, "end_s": 31.0}]})}, config={"episode": 1, "size_bytes": len(MP3)})
    w.add("c1", "episode-audio", {"episode.mp3": b"CANDIDATE-AUDIO"}, accepted=False, config={"episode": 2})
    w.add("c1", "concept-layer", {"concept-layer.json": enc(concept_layer_json())})
    # A course whose outline is only a candidate is not a learner course.
    w.add("c2", "build-outline-v2", {"course-outline-v2.json": enc(outline_json("c2"))}, accepted=False)
    return w


def client(world):
    app = create_app(settings=Settings(), artifact_store=world.store, stage_registry=world.registry)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _leaks(value, path="$"):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS:
                found.append(f"{path}.{key}")
            found += _leaks(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += _leaks(item, f"{path}[{i}]")
    elif isinstance(value, str):
        found += [f"{path}={s}" for s in SENTINELS if s in value]
    return found


GET_REQUESTS = [
    ("/v3/learner/meta", "/v3/learner/meta"),
    ("/v3/learner/courses", "/v3/learner/courses"),
    ("/v3/learner/courses/{course_id}", "/v3/learner/courses/c1"),
    ("/v3/learner/courses/{course_id}/episodes/{number}", "/v3/learner/courses/c1/episodes/1"),
    ("/v3/learner/courses/{course_id}/concepts", "/v3/learner/courses/c1/concepts"),
]
OTHER_ROUTES = {("GET", "/v3/learner/courses/{course_id}/episodes/{number}/audio"),
                ("POST", "/v3/learner/courses/{course_id}/episodes/{number}/questions/{question_id}/reveal")}


def test_every_learner_v3_route_is_walked():
    from types import SimpleNamespace
    routes = {(m, r.path) for r in learner_router(SimpleNamespace()).routes if isinstance(r, APIRoute)
              for m in r.methods}
    assert routes == {("GET", t) for t, _ in GET_REQUESTS} | OTHER_ROUTES


async def test_no_get_route_reveals_an_answer(world):
    async with client(world) as c:
        for _template, path in GET_REQUESTS:
            r = await c.get(path)
            assert r.status_code == 200, (path, r.text)
            assert _leaks(r.json()) == [], (path, _leaks(r.json()))
            assert not [s for s in SENTINELS if s in r.text], path


async def test_meta_names_sources_and_versions(world):
    async with client(world) as c:
        meta = (await c.get("/v3/learner/meta")).json()
    assert meta["api_version"] == "3.0.0" and meta["min_client_version"] == 1
    assert meta["sources"]["episodes"] == "build-outline-v2"


async def test_course_list_contains_only_courses_with_an_accepted_outline(world):
    async with client(world) as c:
        body = (await c.get("/v3/learner/courses")).json()
    assert [(x["course_id"], x["title"], x["episode_count"]) for x in body["courses"]] == [
        ("c1", "Prison Notebooks", 3)]


async def test_course_detail_lists_zero_padded_episodes_with_accepted_availability(world):
    async with client(world) as c:
        body = (await c.get("/v3/learner/courses/c1")).json()
        missing = await c.get("/v3/learner/courses/c2")
        invalid = await c.get("/v3/learner/courses/..bad")
    episodes = body["episodes"]
    assert [(e["number"], e["label"], e["kind"]) for e in episodes] == [
        (0, "00", "course_intro"), (1, "01", "lesson"), (2, "02", "lesson")]
    assert episodes[0]["title"] == "مقدمة الكورس" and episodes[1]["title"] == "Hegemony"
    assert [(e["script_available"], e["audio_available"]) for e in episodes] == [
        (False, False), (True, True), (False, False)]
    assert body["concepts_available"] is True
    assert missing.status_code == 404 and invalid.status_code == 422


async def test_episode_without_accepted_script_is_a_clean_empty_state(world):
    async with client(world) as c:
        for n in (0, 2):  # no script at all; only a candidate script
            body = (await c.get(f"/v3/learner/courses/c1/episodes/{n}")).json()
            assert body["status"] == "script_pending"
            assert body["sections"] == [] and body["questions"] == [] and body["key_terms"] == []
            assert body["audio"]["available"] is False
            assert "CANDIDATE" not in json.dumps(body)
        assert (await c.get("/v3/learner/courses/c1/episodes/9")).status_code == 404


async def test_episode_sections_voices_timings_and_hidden_answers(world):
    async with client(world) as c:
        body = (await c.get("/v3/learner/courses/c1/episodes/1")).json()
    assert body["status"] == "ready" and body["label"] == "01" and body["title"] == "الهيمنة"
    assert [s["kind"] for s in body["sections"]] == ["intro", "book_text", "summary", "key_terms", "questions"]
    book = body["sections"][1]
    assert [seg["voice"] for seg in book["segments"]] == ["author", "course", "external"]
    assert book["segments"][2]["source_label"] == "Stanford Encyclopedia"
    assert book["segments"][0]["start_s"] == 6.0 and book["start_s"] == 6.0  # audio timings win
    qa = {seg["segment_id"]: seg for seg in body["sections"][4]["segments"]}
    assert qa["q1s"]["text"] == "ما الهيمنة؟" and qa["q1s"]["hidden"] is False
    assert qa["q1a"] == {**qa["q1a"], "text": None, "hidden": True, "role": "answer"}
    assert qa["q2a"]["text"] is None and qa["q2a"]["hidden"] is True  # matched by answer text
    assert [q["question_id"] for q in body["questions"]] == ["q1", "q2"]
    assert body["questions"][1]["options"] == ["قسر", "قيادة بالرضا"]
    assert body["key_terms"][0]["original"] == "hegemony"
    assert body["audio"] == {"available": True, "path": "/v3/learner/courses/c1/episodes/1/audio",
                             "duration_s": 95.5, "size_bytes": len(MP3)}


async def test_mcq_options_are_learning_material_but_the_index_is_not(world):
    async with client(world) as c:
        q2 = (await c.get("/v3/learner/courses/c1/episodes/1")).json()["questions"][1]
    assert set(q2) == {"question_id", "prompt", "options", "question_segment_id", "answer_segment_id"}


async def test_reveal_returns_the_answer_only_on_request(world):
    async with client(world) as c:
        q1 = (await c.post("/v3/learner/courses/c1/episodes/1/questions/q1/reveal", json={})).json()
        right = (await c.post("/v3/learner/courses/c1/episodes/1/questions/q2/reveal",
                              json={"selected_option": 1})).json()
        wrong = (await c.post("/v3/learner/courses/c1/episodes/1/questions/q2/reveal",
                              json={"selected_option": 0})).json()
        unknown = await c.post("/v3/learner/courses/c1/episodes/1/questions/q9/reveal", json={})
        candidate = await c.post("/v3/learner/courses/c1/episodes/2/questions/q1/reveal", json={})
        extra = await c.post("/v3/learner/courses/c1/episodes/1/questions/q1/reveal", json={"x": 1})
    assert q1 == {"question_id": "q1", "answer": "ANSWER-SENTINEL-1", "correct": None, "answer_segment_id": "q1a",
                  "answer_section_id": "qa"}
    assert right["answer"] == "قيادة بالرضا" and right["correct"] is True and wrong["correct"] is False
    assert unknown.status_code == 404 and candidate.status_code == 404 and extra.status_code == 422


async def test_audio_streams_accepted_mp3_with_ranges_and_never_a_candidate(world):
    async with client(world) as c:
        full = await c.get("/v3/learner/courses/c1/episodes/1/audio")
        part = await c.get("/v3/learner/courses/c1/episodes/1/audio", headers={"Range": "bytes=10-19"})
        bad = await c.get("/v3/learner/courses/c1/episodes/1/audio", headers={"Range": "bytes=99999-"})
        candidate = await c.get("/v3/learner/courses/c1/episodes/2/audio")
    assert full.status_code == 200 and full.content == MP3 and full.headers["accept-ranges"] == "bytes"
    assert part.status_code == 206 and part.content == MP3[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{len(MP3)}"
    assert bad.status_code == 416
    assert candidate.status_code == 404 and b"CANDIDATE" not in candidate.content


async def test_concepts_from_accepted_layer_with_three_voices_kept_apart(world):
    async with client(world) as c:
        body = (await c.get("/v3/learner/courses/c1/concepts")).json()
    assert body["status"] == "ready" and body["language"] == "en"
    hegemony, consent = body["concepts"]
    assert (hegemony["term"], hegemony["first_episode"], consent["first_episode"]) == ("hegemony", 1, 2)
    assert hegemony["gloss"] == "leadership by consent"
    assert hegemony["author_quotes"] == [{"quote": "Hegemony is consent armoured by coercion.", "pages": [3]}]
    assert hegemony["relations"] == [{"concept_id": "cpt_000000000002", "term": "consent",
                                      "relation": "prerequisite"}]


async def test_concepts_pending_when_no_layer_is_accepted():
    w = World()
    w.add("c1", "build-outline-v2", {"course-outline-v2.json": enc(outline_json())})
    w.add("c1", "concept-layer", {"concept-layer.json": enc(concept_layer_json())}, accepted=False)
    async with client(w) as c:
        body = (await c.get("/v3/learner/courses/c1/concepts")).json()
        course = (await c.get("/v3/learner/courses/c1")).json()
    assert body == {"course_id": "c1", "language": "en", "status": "pending", "concepts": []}
    assert course["concepts_available"] is False and course["title"] == "c1"


async def test_registry_failure_is_503_not_an_open_door(world):
    class Broken:
        def list(self, **_):
            raise RuntimeError("db down")
    app = create_app(settings=Settings(), artifact_store=world.store, stage_registry=Broken())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/v3/learner/courses")).status_code == 503
        assert (await c.get("/v3/learner/courses/c1/episodes/1")).status_code == 503


async def test_etag_revalidation_returns_304(world):
    async with client(world) as c:
        first = await c.get("/v3/learner/courses/c1")
        again = await c.get("/v3/learner/courses/c1", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


def test_committed_openapi_matches_the_code():
    path = Path(__file__).resolve().parents[3] / "docs" / "api" / "learner-v3.openapi.json"
    assert path.read_text(encoding="utf-8") == learner_openapi_text(), (
        "docs/api/learner-v3.openapi.json is stale: run `uv run python scripts/export_learner_openapi.py`")


def test_no_get_response_schema_has_an_answer_field():
    doc = learner_openapi()
    schemas = doc["components"]["schemas"]

    def refs(schema, seen):
        if isinstance(schema, dict):
            if "$ref" in schema:
                name = schema["$ref"].rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    refs(schemas[name], seen)
            for value in schema.values():
                refs(value, seen)
        elif isinstance(schema, list):
            for value in schema:
                refs(value, seen)
        return seen

    for path, ops in doc["paths"].items():
        if "get" not in ops:
            continue
        for name in refs(ops["get"]["responses"], set()):
            assert not FORBIDDEN_KEYS & set(schemas[name].get("properties", {})), (path, name)
