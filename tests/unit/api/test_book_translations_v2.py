"""API of the rebuilt book translation path (D-028 part D): start, status, review queue,
human review and re-assembly. Temporal is faked; the store is the in-memory Postgres fake;
artifacts live in moto S3."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import httpx
import pytest
from b2c_contracts import EngineIdentity, QAIssue, UnitTranslation
from b2c_core import ArtifactStore, Settings
from moto import mock_aws
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from b2c_api.app import create_app
from b2c_orchestration.queues import TRANSLATE_BOOK
from b2c_orchestration.translate_book import (AssembleBookTranslationWorkflow, TranslateBookActivities,
                                              TranslateBookWorkflow, translate_book_workflow_id)
from b2c_orchestration.translation_profiles import load_translation_profiles
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB
from tests.unit.orchestration.test_translate_book_activities import parsed_book

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
ENGINE = EngineIdentity(provider="fake", model_id="m", revision="r", prompt_version="p@1")
GOOD = "ارتفع المحصول بنسبة 12 في المئة في العقد الأول. 1"


class Handle:
    def __init__(self, status="RUNNING"):
        self.status = status

    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name=self.status))


class FakeTemporal:
    def __init__(self):
        self.started: dict[str, tuple] = {}

    async def start_workflow(self, run, inp, *, id, task_queue, **kwargs):
        if id in self.started:
            raise WorkflowAlreadyStartedError(id, "TranslateBookWorkflow")
        self.started[id] = (run, inp, task_queue, kwargs)

    def get_workflow_handle(self, workflow_id):
        return Handle()


class FakeMemory:
    def __init__(self):
        self.stored = []

    def store(self, unit, translation, **kwargs):
        self.stored.append((unit.unit_id, translation.origin))


@pytest.fixture
def env():
    with mock_aws():
        artifacts = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        artifacts.ensure_bucket()
        clock = iter(NOW + timedelta(seconds=i) for i in range(10_000))
        results = TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect,
                                         clock=lambda: next(clock))
        temporal = FakeTemporal()

        async def connect(_settings):
            return temporal
        settings = Settings()
        app = create_app(settings=settings, artifact_store=artifacts, temporal_connect=connect)
        app.state.translation_result_store = results
        app.state.translation_memory = FakeMemory()
        app.state.translation_profiles = load_translation_profiles(settings, env={})
        parsed = artifacts.put("c1", "ingest-parse-pdf", "b" * 64,
                               {"parsed-book.json": parsed_book().model_dump_json().encode()})
        yield SimpleNamespace(app=app, artifacts=artifacts, results=results, temporal=temporal, parsed=parsed)


def client(env):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test")


def start_body(env, **kw):
    body = {"source_artifact_ref": env.parsed.model_dump(mode="json"), "source_language": "en",
            "target_language": "ar", "engine_profile": "hy-mt2"}
    body.update(kw)
    return body


async def start(env, http, **kw):
    response = await http.post("/v2/courses/c1/book-translations", json=start_body(env, **kw))
    assert response.status_code == 202, response.text
    return response.json()


def prepare_and_seed(env, run_id):
    """Run the real prepare activity, then seed results as the batch activity would."""
    from b2c_orchestration.translate_book import PrepareUnitsInput, TranslateBookInput
    started = env.temporal.started[translate_book_workflow_id(run_id)]
    inp: TranslateBookInput = started[1]
    acts = TranslateBookActivities(artifact_store=env.artifacts, result_store=env.results, runtime=None)
    acts.prepare(PrepareUnitsInput(run_id=run_id, inp=inp))
    issue = QAIssue(check_id="qe", category="meaning_error", severity="major", origin="qe")
    env.results.upsert(run_id, "bk", UnitTranslation(
        unit_id="bk:u00002", target_text=GOOD, origin="engine", engine=ENGINE, attempts=1, issues=[issue],
        decision="needs_review", decided_by="policy", decided_at=NOW),
        drafts=[{"lever": "initial", "text": GOOD, "issues": [issue.model_dump(mode="json")]}])
    env.results.upsert(run_id, "bk", UnitTranslation(
        unit_id="bk:u00004", target_text="", origin="rule", decision="dropped", decided_by="policy", decided_at=NOW))
    env.results.upsert(run_id, "bk", UnitTranslation(
        unit_id="bk:u00003", target_text="", origin="engine", engine=ENGINE, attempts=1, issues=[issue],
        decision="rejected", decided_by="policy", decided_at=NOW), drafts=[])


# ------------------------------------------------------------------ start
async def test_start_is_idempotent_and_uses_the_new_workflow_and_queue(env):
    async with client(env) as http:
        first = await start(env, http)
        again = await start(env, http, batch_size=10)  # batching does not change the run
    assert first["run_id"] == again["run_id"] and not first["existing"] and again["existing"]
    run, inp, queue, kwargs = env.temporal.started[first["workflow_id"]]
    assert run == TranslateBookWorkflow.run and queue == TRANSLATE_BOOK
    assert kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
    assert inp.engine_profile == "hy-mt2" and inp.qe_profile is None
    assert inp.profile_fingerprint == env.app.state.translation_profiles.fingerprint("hy-mt2", None)
    assert first["workflow_id"] == translate_book_workflow_id(first["run_id"])
    assert env.results.get_run(first["run_id"]).course_id == "c1"


@pytest.mark.parametrize("change, status", [
    ({"engine_profile": "nope"}, 422),
    ({"qe_profile": "nope"}, 422),
    ({"target_language": "en"}, 422),
    ({"max_unit_chars": 5}, 422),
    ({"unexpected": 1}, 422),
])
async def test_start_validates_the_request(env, change, status):
    async with client(env) as http:
        response = await http.post("/v2/courses/c1/book-translations", json=start_body(env, **change))
    assert response.status_code == status
    assert env.temporal.started == {}


async def test_start_refuses_foreign_or_uncommitted_sources(env):
    foreign = env.artifacts.put("c2", "ingest-parse-pdf", "b" * 64, {"parsed-book.json": b"{}"})
    forged = env.parsed.model_copy(update={"sha256": "0" * 64})
    async with client(env) as http:
        r1 = await http.post("/v2/courses/c1/book-translations",
                             json=start_body(env, source_artifact_ref=foreign.model_dump(mode="json")))
        r2 = await http.post("/v2/courses/c1/book-translations",
                             json=start_body(env, source_artifact_ref=forged.model_dump(mode="json")))
        r3 = await http.post("/v2/courses/bad..id/book-translations", json=start_body(env))
    assert r1.status_code == r2.status_code == r3.status_code == 422
    assert env.temporal.started == {}


# ------------------------------------------------------------------ status and queue
async def test_status_reports_counts_per_decision(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        before = (await http.get(f"/v2/book-translations/{run_id}")).json()
        prepare_and_seed(env, run_id)
        after = (await http.get(f"/v2/book-translations/{run_id}")).json()
    assert before["counts"] == {"accepted": 0, "needs_review": 0, "rejected": 0, "dropped": 0, "pending": None}
    assert after["counts"] == {"accepted": 0, "needs_review": 1, "rejected": 1, "dropped": 1, "pending": 2}
    assert after["unit_count"] == 5 and after["workflow_status"] == "RUNNING"
    assert after["units_ref"]["stage"] == "translation-units" and after["assembly"] is None


async def test_unknown_or_malformed_run_ids_are_404(env):
    async with client(env) as http:
        for path in (f"/v2/book-translations/{'f' * 64}", "/v2/book-translations/xyz",
                     f"/v2/book-translations/{'F' * 64}/review-queue",
                     f"/v2/book-translations/{'f' * 64}/assemblies"):
            method = http.post if path.endswith("assemblies") else http.get
            assert (await method(path)).status_code == 404, path


async def test_review_queue_lists_needs_review_and_rejected_with_source_and_history(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        queue_before = await http.get(f"/v2/book-translations/{run_id}/review-queue")
        prepare_and_seed(env, run_id)
        queue = (await http.get(f"/v2/book-translations/{run_id}/review-queue")).json()
        only_rejected = (await http.get(f"/v2/book-translations/{run_id}/review-queue",
                                        params={"decision": "rejected"})).json()
        bad = await http.get(f"/v2/book-translations/{run_id}/review-queue", params={"decision": "accepted"})
        bad_limit = await http.get(f"/v2/book-translations/{run_id}/review-queue", params={"limit": 0})
    assert queue_before.status_code == 409  # units not prepared yet
    assert queue["total"] == 2 and [i["unit_id"] for i in queue["items"]] == ["bk:u00002", "bk:u00003"]
    item = queue["items"][0]
    assert item["source_text"] == "The yield rose by 12 percent in the first decade."
    assert item["footnote_markers"] == ["1"] and item["decision"] == "needs_review"
    assert item["latest_draft"]["text"] == GOOD and item["issues"][0]["check_id"] == "qe"
    assert item["drafts"] == [item["latest_draft"]] and item["engine"]["model_id"] == "m"
    assert [i["unit_id"] for i in only_rejected["items"]] == ["bk:u00003"]
    assert bad.status_code == bad_limit.status_code == 422


# ------------------------------------------------------------------ human review
async def test_human_edit_is_stored_and_written_to_memory(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        prepare_and_seed(env, run_id)
        response = await http.post(f"/v2/book-translations/{run_id}/units/bk:u00002/review",
                                   json={"action": "edit", "text": GOOD, "reviewer_id": "rev-1"})
        queue = (await http.get(f"/v2/book-translations/{run_id}/review-queue")).json()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["decision"] == "accepted" and body["origin"] == "human" and body["decided_by"] == "human"
    assert env.app.state.translation_memory.stored == [("bk:u00002", "human")]
    assert [i["unit_id"] for i in queue["items"]] == ["bk:u00003"]


async def test_human_edit_with_leak_or_lost_marker_is_422_with_issues(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        prepare_and_seed(env, run_id)
        url = f"/v2/book-translations/{run_id}/units/bk:u00002/review"
        leak = await http.post(url, json={"action": "edit", "reviewer_id": "rev-1",
                                          "text": "Translate the following text into Arabic: " + GOOD})
        marker = await http.post(url, json={"action": "edit", "reviewer_id": "rev-1",
                                            "text": "ارتفع المحصول بنسبة 12 في المئة في العقد الأول."})
    assert leak.status_code == marker.status_code == 422
    assert leak.json()["detail"]["issues"][0]["check_id"] == "prompt_leakage"
    assert marker.json()["detail"]["issues"][0]["check_id"] == "footnote_markers"
    assert env.results.fetch(run_id, "bk:u00002").translation.origin == "engine"
    assert env.app.state.translation_memory.stored == []


async def test_review_validates_ids_state_and_body(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        prepare_and_seed(env, run_id)
        base = f"/v2/book-translations/{run_id}/units"
        ok = {"action": "edit", "text": GOOD, "reviewer_id": "rev-1"}
        unknown_unit = await http.post(f"{base}/bk:u09999/review", json=ok)
        bad_unit = await http.post(f"{base}/bk:x1/review", json=ok)
        unknown_run = await http.post(f"/v2/book-translations/{'f' * 64}/units/bk:u00002/review", json=ok)
        no_result = await http.post(f"{base}/bk:u00001/review", json=ok)
        dropped = await http.post(f"{base}/bk:u00004/review", json=ok)
        nothing = await http.post(f"{base}/bk:u00003/review", json={"action": "accept", "reviewer_id": "rev-1"})
        bad_reviewer = await http.post(f"{base}/bk:u00002/review", json={**ok, "reviewer_id": "a b"})
        blank = await http.post(f"{base}/bk:u00002/review", json={**ok, "text": "  "})
        accept = await http.post(f"{base}/bk:u00002/review", json={"action": "accept", "reviewer_id": "rev-2"})
    assert unknown_unit.status_code == bad_unit.status_code == unknown_run.status_code == 404
    assert no_result.status_code == dropped.status_code == nothing.status_code == 409
    assert bad_reviewer.status_code == blank.status_code == 422
    assert accept.status_code == 201 and accept.json()["target_text"] == GOOD


# ------------------------------------------------------------------ re-assembly
async def test_reassembly_is_keyed_by_the_results_revision(env):
    async with client(env) as http:
        run_id = (await start(env, http))["run_id"]
        not_ready = await http.post(f"/v2/book-translations/{run_id}/assemblies")
        prepare_and_seed(env, run_id)
        first = (await http.post(f"/v2/book-translations/{run_id}/assemblies")).json()
        same = (await http.post(f"/v2/book-translations/{run_id}/assemblies")).json()
        await http.post(f"/v2/book-translations/{run_id}/units/bk:u00002/review",
                        json={"action": "accept", "reviewer_id": "rev-1"})
        changed = (await http.post(f"/v2/book-translations/{run_id}/assemblies")).json()
    assert not_ready.status_code == 409
    assert first["workflow_id"] == same["workflow_id"] and same["existing"] and not first["existing"]
    assert changed["workflow_id"] != first["workflow_id"]
    run, inp, queue, _ = env.temporal.started[first["workflow_id"]]
    assert run == AssembleBookTranslationWorkflow.run and queue == TRANSLATE_BOOK
    assert inp.run_id == run_id and inp.units_ref == env.results.get_run(run_id).units_ref
    assert json.dumps(first)  # serialisable
