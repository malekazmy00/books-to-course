"""R1: operator routes expose stage-output status; serving uses an accepted-only guard (deps)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from b2c_contracts import ArtifactRef, GateResult, StageOutputRecord
from b2c_core import Settings
from b2c_core.stage_registry import StageOutputRegistry
from fastapi import HTTPException

from b2c_api.app import create_app
from b2c_api.deps import require_accepted_artifact
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40


def ref(key: str, stage: str = "translate-book") -> ArtifactRef:
    return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/c1/{stage}/{key}/", sha256="c" * 64,
                       created_at=NOW)


ACCEPTED, CANDIDATE, LEGACY = ref("1" * 64), ref("2" * 64), ref("3" * 64, "translate-content")


@pytest.fixture
def registry():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    reg = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect, clock=lambda: next(clock))
    gate = GateResult(stage="translate-book", artifact=ACCEPTED, gate_id="g.one", gate_version="v1",
                      kind="deterministic", passed=True, decider="system", decided_by="policy", decided_at=NOW)
    for artifact, gates in ((ACCEPTED, [gate]), (CANDIDATE, [])):
        reg.register(StageOutputRecord(course_id="c1", artifact=artifact, code_revision=SHA, required_gates=["g.one"],
                                       gate_results=gates, status="candidate", status_changed_at=NOW,
                                       status_changed_by="worker"))
    reg.transition("c1", "translate-book", ACCEPTED.key, "accepted", actor="worker", code_revision=SHA)
    reg.register(StageOutputRecord(course_id="c1", artifact=LEGACY, code_revision="unknown", status="quarantined",
                                   status_reason="legacy pre-gate", status_changed_at=NOW,
                                   status_changed_by="script"))
    return reg


def client(registry):
    app = create_app(settings=Settings(), artifact_store=object(), stage_registry=registry)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def run(coro):
    return asyncio.run(coro)


def test_operator_lists_outputs_with_status(registry):
    async def go():
        async with client(registry) as c:
            all_ = (await c.get("/v1/operator/stage-outputs", params={"course_id": "c1"})).json()
            quarantined = (await c.get("/v1/operator/stage-outputs",
                                       params={"course_id": "c1", "status": "quarantined"})).json()
            bad = await c.get("/v1/operator/stage-outputs", params={"status": "approved"})
            counts = (await c.get("/v1/operator/stage-outputs/counts", params={"course_id": "c1"})).json()
            return all_, quarantined, bad, counts
    all_, quarantined, bad, counts = run(go())
    assert {(o["stage"], o["status"]) for o in all_["outputs"]} == {
        ("translate-book", "accepted"), ("translate-book", "candidate"), ("translate-content", "quarantined")}
    assert [o["status_reason"] for o in quarantined["outputs"]] == ["legacy pre-gate"]
    assert bad.status_code == 422
    assert counts["counts"] == {"translate-book": {"accepted": 1, "candidate": 1},
                                "translate-content": {"quarantined": 1}}


def test_operator_reads_one_output_with_gates_and_history(registry):
    async def go():
        async with client(registry) as c:
            one = await c.get(f"/v1/operator/stage-outputs/c1/translate-book/{ACCEPTED.key}")
            missing = await c.get(f"/v1/operator/stage-outputs/c1/translate-book/{'9' * 64}")
            invalid = await c.get("/v1/operator/stage-outputs/c1/translate-book/not-a-key")
            return one, missing, invalid
    one, missing, invalid = run(go())
    body = one.json()
    assert one.status_code == 200 and body["record"]["status"] == "accepted"
    assert body["record"]["gate_results"][0]["gate_id"] == "g.one"
    assert [e["to_status"] for e in body["history"]] == ["candidate", "accepted"]
    assert missing.status_code == 404 and invalid.status_code == 404


def test_accepted_only_guard_serves_accepted_and_hides_everything_else(registry):
    assert run(require_accepted_artifact(registry, "c1", ACCEPTED)).status == "accepted"
    for artifact in (CANDIDATE, LEGACY, ref("9" * 64)):
        with pytest.raises(HTTPException) as err:
            run(require_accepted_artifact(registry, "c1", artifact))
        assert err.value.status_code == 404
    forged = ACCEPTED.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(HTTPException):
        run(require_accepted_artifact(registry, "c1", forged))


def test_accepted_only_guard_is_unavailable_not_open_when_the_registry_fails():
    class Broken:
        def get(self, *a):
            raise OSError("db down")
    with pytest.raises(HTTPException) as err:
        run(require_accepted_artifact(Broken(), "c1", ACCEPTED))
    assert err.value.status_code == 503
