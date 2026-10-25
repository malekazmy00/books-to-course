"""Outline v2 through the API (R4). Owner decision 2026-10-25: there is no human reviewer. The outline
is accepted by the promotion service (structure gate + independent cross-check, system decisions);
the API shows the v2 draft, its gates and status, and the approve routes only hand an *accepted*
outline, exactly as built, to indexing. They never decide, and they never need the v1 opt-in."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import boto3
import httpx
import pytest
from moto import mock_aws
from temporalio.testing import ActivityEnvironment

import b2c_contracts as c
from b2c_api.app import create_app
from b2c_core import ArtifactStore, Settings
from b2c_core.stage_registry import StageOutputRegistry
from b2c_orchestration.outline_v2 import BuildOutlineV2Input, OutlineV2Activities
from b2c_orchestration.promotion import PromotionService
from tests.unit.api.test_book_upload import FakeTemporal, _resolved
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.outline.v2_fixtures import sat_like

WORKFLOW = "b2c-ingest-" + "2" * 64
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def registry(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))


def setup(revision):
    store = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
    store.ensure_bucket()
    parsed = sat_like()
    v2 = parsed.model_dump_json().encode()
    v1 = c.parsed_book_v1_from_v2(parsed)
    v1 = v1.model_copy(update={"parser": {**v1.parser, "source_file_sha256": "e" * 64}})
    parsed_ref = store.put("course-1", "ingest-parse-v2", hashlib.sha256(v2).hexdigest(),
                           {"parsed-book-v2.json": v2, "parsed-book.json": v1.model_dump_json().encode()})
    reg = registry(FakeRegistryDB())
    acts = OutlineV2Activities(store=store, promotion=PromotionService(reg, code_revision=revision))
    outline = ActivityEnvironment().run(acts.build, BuildOutlineV2Input(
        "course-1", parsed_ref.model_dump(mode="json"), []))
    temporal = FakeTemporal()
    temporal.status_name = "COMPLETED"
    temporal.result_value = {"parsed_book": {"ref": parsed_ref.model_dump(mode="json"), "accepted": True},
                             "outline": outline, "status": "accepted"}
    return store, reg, temporal, outline


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    with mock_aws():
        yield setup("a" * 40)


def app_for(store, reg, temporal, settings=None):
    return create_app(settings=settings or Settings(), artifact_store=store,
                      temporal_connect=lambda _: _resolved(temporal), stage_registry=reg)


def draft_v1(store, outline):
    ref = c.ArtifactRef.model_validate(outline["ref"])
    return c.CourseOutline.model_validate_json(store.read(ref, "course-outline.json"))


async def call(app, method, path, **kw):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, **kw)


async def test_the_outline_view_shows_the_v2_draft_its_gates_and_its_status(env):
    store, reg, temporal, outline = env
    response = await call(app_for(store, reg, temporal), "GET", f"/v1/ingestions/{WORKFLOW}/outline")
    assert response.status_code == 200, response.text
    body = response.json()
    # D-036: the preface is the opening module, chapter 1 follows
    assert [m["title"] for m in body["outline"]["modules"]][:2] == ["Introduction to the Book",
                                                                  "Chapter 1 • Getting Organized"]
    assert body["outline_v2"]["format"] == "course-outline/2" and body["outline_v2"]["excluded_ranges"]
    assert body["quality"]["accepted"] is True
    gates = body["stage_output"]["gates"]
    assert body["stage_output"]["status"] == "accepted"
    assert {g["kind"] for g in gates.values()} == {"deterministic"} and all(g["passed"] for g in gates.values())


async def test_approve_system_hands_an_accepted_outline_to_indexing_without_any_opt_in(env):
    store, reg, temporal, outline = env
    app = app_for(store, reg, temporal)  # B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL is off (R0 default kept)
    response = await call(app, "POST", f"/v1/ingestions/{WORKFLOW}/outline/approve-system",
                          json={"index_after_approval": False})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stage_output"]["status"] == "accepted"
    assert body["approval_provenance"]["review_type"] == "system"
    assert body["approval_provenance"]["system_id"] == "b2c-outline-v2-promotion"
    approved = c.ArtifactRef.model_validate(body["approved_outline"])
    payload = store.read_json(approved, "approved-outline.json")
    assert payload["outline_v2_ref"] == outline["ref"] and payload["outline"]["requires_review"] is False
    again = await call(app, "POST", f"/v1/ingestions/{WORKFLOW}/outline/approve-system",
                       json={"index_after_approval": False})
    assert again.status_code == 201 and again.json()["approved_outline"]["key"] == body["approved_outline"]["key"]


async def test_an_edited_outline_is_not_the_accepted_one(env):
    store, reg, temporal, outline = env
    edited = draft_v1(store, outline).model_dump(mode="python")
    edited["modules"][1]["lessons"][1]["title"] = "Something else"
    response = await call(app_for(store, reg, temporal), "POST", f"/v1/ingestions/{WORKFLOW}/outline/approve",
                          json={"review_type": "human", "reviewer_id": "operator", "outline": edited,
                                "index_after_approval": False})
    assert response.status_code == 422, response.text


async def test_an_outline_that_its_gates_did_not_accept_is_not_handed_over(monkeypatch):
    monkeypatch.delenv("B2C_ALLOW_SYSTEM_OUTLINE_APPROVAL", raising=False)
    with mock_aws():
        store, reg, temporal, outline = setup("unknown")  # unstamped build: gates pass, stays a candidate
        response = await call(app_for(store, reg, temporal), "POST",
                              f"/v1/ingestions/{WORKFLOW}/outline/approve-system", json={"index_after_approval": False})
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["status"] == "candidate"
        unregistered = await call(app_for(store, registry(FakeRegistryDB()), temporal), "POST",
                                  f"/v1/ingestions/{WORKFLOW}/outline/approve-system",
                                  json={"index_after_approval": False})
        assert unregistered.status_code == 409, unregistered.text
