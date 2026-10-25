"""Termbase review API of the rebuilt translation path (D-028 principle 5, Gate G3).

Temporal is faked (signals and queries are recorded); Postgres is the in-memory fakes;
artifacts live in moto S3; the termbase is built by the real activity with a fake extractor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import httpx
import pytest
from b2c_core import ArtifactStore, Settings
from moto import mock_aws
from temporalio.exceptions import WorkflowAlreadyStartedError

from b2c_api.app import create_app
from b2c_orchestration.termbase_store import TermbaseStore
from b2c_orchestration.translate_book import (BuildTermbaseInput, PrepareUnitsInput, TermbaseApproval,
                                              TranslateBookActivities, translate_book_workflow_id)
from b2c_orchestration.translation_profiles import load_translation_profiles
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB
from tests.unit.orchestration.test_book_termbase_activities import FakeBuilder, FixedSource, ProposingRuntime
from tests.unit.orchestration.test_translate_book_activities import FakeEngine, parsed_book

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
CANDIDATES = [("Rice", "term", 3), ("Wheat", "term", 2), ("Farmers", "term", 1), ("Lena Vasquez", "person", 2)]
PROPOSALS = {"Rice": "الأرز", "Wheat": "القمح", "Lena Vasquez": "لينا فاسكيز"}


class Handle:
    def __init__(self, temporal, workflow_id):
        self.temporal, self.workflow_id = temporal, workflow_id

    async def describe(self):
        return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))

    async def signal(self, name, arg):
        if self.temporal.signal_error is not None:
            raise self.temporal.signal_error
        self.temporal.signals.append((self.workflow_id, name, arg))

    async def query(self, name):
        return {"phase": "awaiting_approval", "query": name}


class FakeTemporal:
    def __init__(self):
        self.started: dict[str, tuple] = {}
        self.signals: list[tuple] = []
        self.signal_error: Exception | None = None

    async def start_workflow(self, run, inp, *, id, task_queue, **kwargs):
        if id in self.started:
            raise WorkflowAlreadyStartedError(id, "TranslateBookWorkflow")
        self.started[id] = (run, inp, task_queue, kwargs)

    def get_workflow_handle(self, workflow_id):
        return Handle(self, workflow_id)


@pytest.fixture
def env():
    with mock_aws():
        artifacts = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        artifacts.ensure_bucket()
        clock = iter(NOW + timedelta(seconds=i) for i in range(10_000))
        results = TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect,
                                         clock=lambda: next(clock))
        terms = TermbaseStore("postgresql://unused", connect=FakeTermbaseDB().connect, clock=lambda: next(clock))
        temporal = FakeTemporal()

        async def connect(_settings):
            return temporal
        settings = Settings()
        app = create_app(settings=settings, artifact_store=artifacts, temporal_connect=connect)
        app.state.translation_result_store = results
        app.state.termbase_store = terms
        app.state.translation_profiles = load_translation_profiles(settings, env={})
        parsed = artifacts.put("c1", "ingest-parse-pdf", "b" * 64,
                               {"parsed-book.json": parsed_book().model_dump_json().encode()})
        yield SimpleNamespace(app=app, artifacts=artifacts, results=results, terms=terms, temporal=temporal,
                              parsed=parsed)


def client(env):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test")


async def start(env, http, **kw):
    body = {"source_artifact_ref": env.parsed.model_dump(mode="json"), "source_language": "en",
            "target_language": "ar", "engine_profile": "hy-mt2", **kw}
    response = await http.post("/v2/courses/c1/book-translations", json=body)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def build(env, run_id):
    """What the workflow does before the gate: prepare, then build the termbase."""
    _, inp, _, _ = env.temporal.started[translate_book_workflow_id(run_id)]
    acts = TranslateBookActivities(artifact_store=env.artifacts, result_store=env.results,
                                   runtime=ProposingRuntime(FakeEngine(), proposer=FixedSource(PROPOSALS)),
                                   termbase_store=env.terms, termbase_builder=FakeBuilder(CANDIDATES))
    prepared = acts.prepare(PrepareUnitsInput(run_id=run_id, inp=inp))
    acts.build_termbase(BuildTermbaseInput(run_id=run_id, course_id="c1", units_ref=prepared.units_ref,
                                           engine_profile=inp.engine_profile, source_language="en",
                                           target_language="ar"))
    return inp


async def ready(env, http, **kw):
    run_id = await start(env, http, **kw)
    build(env, run_id)
    return run_id


def base(run_id):
    return f"/v2/book-translations/{run_id}/termbase"


# ------------------------------------------------------------------ start options
async def test_start_passes_the_termbase_gate_options(env):
    async with client(env) as http:
        run_id = await start(env, http)
        inp = env.temporal.started[translate_book_workflow_id(run_id)][1]
        assert inp.require_approved_termbase is True and inp.termbase_min_frequency == 1
        env.temporal.started.clear()
        other = await start(env, http, require_approved_termbase=False, termbase_min_frequency=3)
        inp = env.temporal.started[translate_book_workflow_id(other)][1]
        assert other == run_id  # gate options do not change the run identity
        assert inp.require_approved_termbase is False and inp.termbase_min_frequency == 3
        bad = await http.post("/v2/courses/c1/book-translations", json={
            "source_artifact_ref": env.parsed.model_dump(mode="json"), "source_language": "en",
            "target_language": "ar", "engine_profile": "hy-mt2", "termbase_min_frequency": 0})
        assert bad.status_code == 422


# ------------------------------------------------------------------ listing
async def test_list_with_filters_and_gate(env):
    async with client(env) as http:
        run_id = await ready(env, http, termbase_min_frequency=2)
        listing = (await http.get(base(run_id))).json()
        assert listing["total"] == 4 and listing["book_id"] == "bk"
        assert [i["source"] for i in listing["items"]] == ["Rice", "Lena Vasquez", "Wheat", "Farmers"]
        assert listing["gate"] == {"pending_blocking": 3, "pending_total": 4, "approved": 0, "rejected": 0,
                                   "absent": 0, "min_frequency": 2}
        assert listing["items"][0]["proposals"][0] == {"target": "الأرز", "provenance": "gateway_proposer",
                                                       "reference": None, "source_approved_by": None,
                                                       "note": None}
        assert listing["build"]["artifact"]["stage"] == "book-termbase"
        people = (await http.get(base(run_id), params={"kind": "person"})).json()
        assert [i["source"] for i in people["items"]] == ["Lena Vasquez"]
        frequent = (await http.get(base(run_id), params={"min_frequency": 3})).json()
        assert [i["source"] for i in frequent["items"]] == ["Rice"]
        found = (await http.get(base(run_id), params={"q": "wheat"})).json()
        assert [i["source"] for i in found["items"]] == ["Wheat"]
        arabic = (await http.get(base(run_id), params={"q": "الأرز"})).json()
        assert [i["source"] for i in arabic["items"]] == ["Rice"]
        paged = (await http.get(base(run_id), params={"limit": 1, "offset": 1})).json()
        assert paged["total"] == 4 and [i["source"] for i in paged["items"]] == ["Lena Vasquez"]
        assert (await http.get(base(run_id), params={"status": "approved"})).json()["total"] == 0
        assert (await http.get(base(run_id), params={"status": "bogus"})).status_code == 422
        assert (await http.get(base(run_id), params={"kind": "bogus"})).status_code == 422


async def test_ids_and_missing_state(env):
    async with client(env) as http:
        assert (await http.get(base("NOT-A-RUN"))).status_code == 404
        assert (await http.get(base("a" * 64))).status_code == 404
        run_id = await start(env, http)
        assert (await http.get(base(run_id))).status_code == 409  # units not prepared yet
        _, inp, _, _ = env.temporal.started[translate_book_workflow_id(run_id)]
        TranslateBookActivities(artifact_store=env.artifacts, result_store=env.results, runtime=None).prepare(
            PrepareUnitsInput(run_id=run_id, inp=inp))
        assert (await http.get(base(run_id))).status_code == 409  # termbase not built yet


# ------------------------------------------------------------------ decisions
async def test_approve_with_proposal_or_edit_and_reject(env):
    async with client(env) as http:
        run_id = await ready(env, http)
        ok = await http.post(f"{base(run_id)}/approve", json={"source": "rice", "reviewer_id": "aly"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "approved" and ok.json()["target"] == "الأرز"
        assert ok.json()["reviewer"] == "aly" and ok.json()["decided_at"]
        edited = await http.post(f"{base(run_id)}/approve", json={
            "source": "Farmers", "target": "المزارعون", "reviewer_id": "aly"})
        assert edited.json()["target"] == "المزارعون"
        rejected = await http.post(f"{base(run_id)}/reject", json={
            "source": "Wheat", "reviewer_id": "aly", "reason": "too generic"})
        assert rejected.json()["status"] == "rejected" and rejected.json()["rejection_reason"] == "too generic"
        listing = (await http.get(base(run_id), params=[("status", "approved"), ("status", "rejected")])).json()
        assert {i["source"] for i in listing["items"]} == {"Rice", "Farmers", "Wheat"}


@pytest.mark.parametrize("body, status", [
    ({"source": "Rice", "target": "Rice", "reviewer_id": "aly"}, 422),            # not Arabic
    ({"source": "Rice", "target": "الأرز rice", "reviewer_id": "aly"}, 422),      # mixed
    ({"source": "Rice", "target": "   ", "reviewer_id": "aly"}, 422),             # empty
    ({"source": "Rice", "target": "الأرز"}, 422),                                  # no reviewer
    ({"source": "Rice", "target": "الأرز", "reviewer_id": "bad reviewer!"}, 422),
    ({"source": "Rice", "target": "الأرز", "reviewer_id": "g3-policy-auto"}, 422),  # machine policy
    ({"source": "Rice", "targets": ["الأرز", "رز"], "reviewer_id": "aly"}, 422),   # one target per source
    ({"source": "Nope", "target": "لا", "reviewer_id": "aly"}, 404),
])
async def test_approve_validation(env, body, status):
    async with client(env) as http:
        run_id = await ready(env, http)
        response = await http.post(f"{base(run_id)}/approve", json=body)
        assert response.status_code == status, response.text
        assert (await http.get(base(run_id), params={"status": "approved"})).json()["total"] == 0


async def test_one_locked_target_per_source(env):
    async with client(env) as http:
        run_id = await ready(env, http)
        await http.post(f"{base(run_id)}/approve", json={"source": "Rice", "reviewer_id": "aly"})
        changed = await http.post(f"{base(run_id)}/approve", json={
            "source": "Rice", "target": "رز", "reviewer_id": "aly"})
        assert changed.status_code == 422  # needs replace=true
        replaced = await http.post(f"{base(run_id)}/approve", json={
            "source": "Rice", "target": "رز", "reviewer_id": "aly", "replace": True})
        assert replaced.status_code == 200 and replaced.json()["target"] == "رز"
        shared = await http.post(f"{base(run_id)}/approve", json={
            "source": "Wheat", "target": "رز", "reviewer_id": "aly"})
        assert shared.status_code == 422  # already locked for Rice


async def test_bulk_approve_selected_proposals_all_or_nothing(env):
    async with client(env) as http:
        run_id = await ready(env, http)
        bad = await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": [
            {"source": "Rice"}, {"source": "Wheat", "target": "قمح مختلف"}]})  # not a proposal
        assert bad.status_code == 422
        dup = await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": [
            {"source": "Rice"}, {"source": "rice"}]})
        assert dup.status_code == 422
        no_proposal = await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": [
            {"source": "Farmers"}]})
        assert no_proposal.status_code == 422
        assert (await http.get(base(run_id), params={"status": "approved"})).json()["total"] == 0
        ok = await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": [
            {"source": "Rice"}, {"source": "Wheat", "target": "القمح"}, {"source": "Lena Vasquez"}]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["approved"] == 3
        assert {i["target"] for i in ok.json()["items"]} == {"الأرز", "القمح", "لينا فاسكيز"}
        empty = await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": []})
        assert empty.status_code == 422


# ------------------------------------------------------------------ done -> signal
async def test_done_signals_only_when_nothing_blocks(env):
    async with client(env) as http:
        run_id = await ready(env, http, termbase_min_frequency=2)
        early = await http.post(f"{base(run_id)}/done", json={"reviewer_id": "aly"})
        assert early.status_code == 409 and early.json()["detail"]["gate"]["pending_blocking"] == 3
        assert env.temporal.signals == []
        await http.post(f"{base(run_id)}/bulk-approve", json={"reviewer_id": "aly", "selections": [
            {"source": "Rice"}, {"source": "Lena Vasquez"}]})
        await http.post(f"{base(run_id)}/reject", json={"source": "Wheat", "reviewer_id": "aly"})
        # "Farmers" (frequency 1) stays pending: below the threshold, not used, not approved
        done = await http.post(f"{base(run_id)}/done", json={"reviewer_id": "aly"})
        assert done.status_code == 202, done.text
        assert done.json()["gate"]["pending_blocking"] == 0 and done.json()["gate"]["pending_total"] == 1
        [(workflow_id, name, arg)] = env.temporal.signals
        assert workflow_id == translate_book_workflow_id(run_id) and name == "termbase_approved"
        assert arg == TermbaseApproval(reviewer_id="aly")
        assert (await http.post(f"{base(run_id)}/done", json={})).status_code == 422  # reviewer required


async def test_done_when_the_workflow_is_not_waiting(env):
    async with client(env) as http:
        run_id = await ready(env, http, termbase_min_frequency=5)
        env.temporal.signal_error = RuntimeError("workflow execution already completed")
        response = await http.post(f"{base(run_id)}/done", json={"reviewer_id": "aly"})
        assert response.status_code == 409


async def test_bearer_auth_applies(monkeypatch, env):
    monkeypatch.setenv("B2C_API_AUTH_REQUIRED", "true")
    monkeypatch.setenv("B2C_APP_API_TOKEN", "secret-token")
    app = create_app(settings=Settings(), artifact_store=env.artifacts)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.get(base("a" * 64))).status_code == 401
        assert (await http.post(f"{base('a' * 64)}/done", json={"reviewer_id": "aly"})).status_code == 401


async def test_approve_records_forbidden_variants(env):
    async with client(env) as http:
        run_id = await ready(env, http)
        ok = await http.post(f"{base(run_id)}/approve", json={
            "source": "rice", "target": "الأرز", "reviewer_id": "aly", "forbidden_variants": ["الرز", " "]})
        assert ok.status_code == 200, ok.text
        assert ok.json()["forbidden_variants"] == ["الرز"]
        same = await http.post(f"{base(run_id)}/approve", json={
            "source": "Farmers", "target": "المزارعون", "reviewer_id": "aly",
            "forbidden_variants": ["المزارعون"]})
        assert same.status_code == 422  # the approved target cannot be one of its forbidden variants
