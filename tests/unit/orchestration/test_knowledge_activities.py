"""Knowledge research + concept layer activities (D-034/D-035): stored artifacts, promotion candidates
with system GateResults, termbase seeds (approved entries, else candidates), the dossier read only
when accepted, and the workflow order. moto S3 + the fake stage-output registry + scripted models."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws
from temporalio.testing import ActivityEnvironment

import b2c_contracts as c
from b2c_concepts import ConceptModel
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_orchestration import knowledge as kn
from b2c_orchestration.knowledge import (BuildConceptLayerInput, KnowledgeActivities, ResearchKnowledgeInput,
                                         termbase_seeds)
from b2c_orchestration.promotion import PromotionService
from b2c_research import ModelRole
from b2c_translation.termbase import BookTermbase, TermCandidate
from tests.unit.concepts.test_concept_layer import B, book, model_script, outline
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.research.fakes import ScriptedLLM
from tests.unit.research.test_dossier import extractor_script, judge_script, sources

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "c" * 40


@pytest.fixture
def store():
    with mock_aws():
        s = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        s.ensure_bucket()
        yield s


def promotion(db):
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=db.connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision=SHA, clock=lambda: NOW)


def termbase(approve=True) -> BookTermbase:
    tb = BookTermbase.new(B, source_language="en", target_language="ar")
    for term, freq in (("hegemony", 5), ("war of position", 3), ("passive revolution", 1)):
        tb.add_candidate(TermCandidate(source=term, kind="term", frequency=freq, score=freq), [])
        if approve:
            tb.approve(term, {"hegemony": "الهيمنة", "war of position": "حرب المواقع",
                              "passive revolution": "الثورة السلبية"}[term], "ai-provisional-test")
    tb.add_candidate(TermCandidate(source="Antonio Gramsci", kind="person", frequency=9), [])
    return tb


class FakeProvider:
    kinds = ("openalex.work", "openalex.reviews", "openalex.citing", "openalex.author", "wikidata.entity",
             "wikipedia.page", "openlibrary.search")

    def __init__(self):
        self.queries = []

    def run(self, q, meta):
        self.queries.append(q)
        return sources()[1]


def put(store, stage, files):
    blob = b"".join(files.values())
    return store.put("crs", stage, hashlib.sha256(blob).hexdigest(), files)


def acts(store, db, provider):
    ident = lambda m: c.ProducerIdentity(role="x", provider="openrouter", model_id=m)
    return KnowledgeActivities(
        artifact_store=store,
        extractor=ModelRole(ScriptedLLM(extractor_script, "deepseek/x"), ident("deepseek/x"), "deepseek"),
        judge=ModelRole(ScriptedLLM(judge_script, "google/y"), ident("google/y"), "google"),
        concept_model=ConceptModel(ScriptedLLM(model_script), ident("deepseek/x")),
        providers=lambda builder, meta: [provider], promotion=promotion(db), clock=lambda: NOW)


def test_seeds_prefer_approved_entries_else_candidates():
    seeds, basis, targets = termbase_seeds(termbase(), units=None, max_candidates=10)
    assert basis == "approved" and {s.term for s in seeds} == {"hegemony", "war of position", "passive revolution"}
    assert all(s.status == "approved" for s in seeds) and len(targets) == 3
    seeds, basis, targets = termbase_seeds(termbase(approve=False), units=None, max_candidates=2)
    assert basis == "candidates" and [s.term for s in seeds] == ["hegemony", "war of position"]
    assert targets == ["hegemony", "war of position"]


def test_research_then_concepts_with_promotion(store):
    db = FakeRegistryDB()
    provider = FakeProvider()
    a = acts(store, db, provider)
    parsed_ref = put(store, "ingest-parse-pdf", {"parsed-book.json": book().model_dump_json().encode()})
    outline_ref = put(store, "approve-outline", {"approved-outline.json": json.dumps(
        {"outline": outline().model_dump(mode="json")}).encode()})
    tb_ref = put(store, "book-termbase-approved", {"termbase.json": termbase().to_json().encode()})
    env = ActivityEnvironment()
    research = env.run(a.research, ResearchKnowledgeInput(
        course_id="crs", parsed_ref=parsed_ref.model_dump(mode="json"), termbase_ref=tb_ref.model_dump(mode="json"),
        meta_override={"title": "From Subordination to Revolution", "authors": ["John Chalcraft"]}))
    ref = c.ArtifactRef.model_validate(research["ref"])
    assert ref.stage == kn.KNOWLEDGE_STAGE
    assert sorted(store.list_files(ref)) == ["dossier.json", "gates.json", "meta.json", "sources.json"]
    dossier = c.KnowledgeDossier.model_validate_json(store.read(ref, "dossier.json"))
    assert dossier.metrics["raw_claims"] == 8 and research["published_claims"] == len(dossier.published())
    stored_sources = json.loads(store.read(ref, "sources.json"))
    assert all("Nobel" not in s["text"] for s in stored_sources)
    assert any(q.section == "concept_in_field" and q.concept == "hegemony" for q in provider.queries)
    record = a.promotion.registry.get("crs", ref.stage, ref.key)
    assert set(record.required_gates) == {"knowledge.citations", "knowledge.tier-coverage"}
    assert all(g.decider == "system" for g in record.gate_results)
    assert research["stage_output_status"] == "rejected"  # no tier-1 book-card claim in these fixtures
    again = env.run(a.research, ResearchKnowledgeInput(
        course_id="crs", parsed_ref=parsed_ref.model_dump(mode="json"), termbase_ref=tb_ref.model_dump(mode="json"),
        meta_override={"title": "From Subordination to Revolution", "authors": ["John Chalcraft"]}))
    assert again["cache_hit"] is True and again["ref"] == research["ref"]

    concepts = env.run(a.concepts, BuildConceptLayerInput(
        course_id="crs", parsed_ref=parsed_ref.model_dump(mode="json"), outline_ref=outline_ref.model_dump(mode="json"),
        termbase_ref=tb_ref.model_dump(mode="json"), dossier_ref=research["ref"]))
    cref = c.ArtifactRef.model_validate(concepts["ref"])
    layer = c.ConceptLayer.model_validate_json(store.read(cref, "concept-layer.json"))
    assert layer.termbase_basis == "approved" and len(layer.concepts) == 3
    assert layer.dossier_sha256 is None and concepts["dossier_used"] is False  # the dossier was rejected
    record = a.promotion.registry.get("crs", cref.stage, cref.key)
    assert set(record.required_gates) == {"concepts.grounding", "concepts.coverage"}
    assert concepts["stage_output_status"] == "accepted"
    assert concepts["gates"]["concepts.coverage"]["metrics"]["coverage"] == 1.0


@pytest.fixture
async def wf_env():
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment
    try:
        environment = await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter)
    except Exception as exc:  # pragma: no cover - depends on the test-server download
        pytest.skip(f"Temporal test server unavailable: {exc}")
    try:
        yield environment
    finally:
        await environment.shutdown()


async def test_workflow_runs_research_then_concepts(wf_env):
    from concurrent.futures import ThreadPoolExecutor

    from temporalio import activity
    from temporalio.worker import Worker

    calls = []

    @activity.defn(name=kn.RESEARCH_ACTIVITY)
    def research(inp: ResearchKnowledgeInput) -> dict:
        calls.append(("research", inp.course_id))
        return {"ref": {"stage": kn.KNOWLEDGE_STAGE}, "stage_output_status": "accepted"}

    @activity.defn(name=kn.CONCEPT_ACTIVITY)
    def concepts(inp: BuildConceptLayerInput) -> dict:
        calls.append(("concepts", inp.dossier_ref))
        return {"ref": {"stage": kn.CONCEPT_STAGE}, "stage_output_status": "accepted"}

    async with Worker(wf_env.client, task_queue="k", workflows=[kn.BookKnowledgeWorkflow],
                      activities=[research, concepts], activity_executor=ThreadPoolExecutor(2)):
        out = await wf_env.client.execute_workflow(
            kn.BookKnowledgeWorkflow.run, kn.BookKnowledgeInput(course_id="crs", parsed_ref={"x": 1},
                                                                outline_ref={"y": 2}),
            id="wf-k", task_queue="k")
    assert [x[0] for x in calls] == ["research", "concepts"]
    assert calls[1][1] == {"stage": kn.KNOWLEDGE_STAGE}
    assert out["research"]["stage_output_status"] == "accepted"


def test_knowledge_worker_roles_from_env(tmp_path, store):
    from b2c_orchestration import knowledge_worker as kw
    from b2c_orchestration.queues import KNOWLEDGE, max_concurrent_activities

    key = tmp_path / "key"
    key.write_text("sk-test")
    env = {"B2C_KNOWLEDGE_EXTRACTOR_BASE_URL": "https://openrouter.ai/api/v1",
           "B2C_KNOWLEDGE_EXTRACTOR_MODEL_ID": "deepseek/deepseek-v4-flash-0731",
           "B2C_KNOWLEDGE_EXTRACTOR_ALLOW_REMOTE": "1", "B2C_KNOWLEDGE_EXTRACTOR_API_KEY_FILE": str(key),
           "B2C_KNOWLEDGE_JUDGE_BASE_URL": "https://openrouter.ai/api/v1",
           "B2C_KNOWLEDGE_JUDGE_MODEL_ID": "google/gemini-3.1-flash-lite",
           "B2C_KNOWLEDGE_JUDGE_ALLOW_REMOTE": "1", "B2C_KNOWLEDGE_JUDGE_API_KEY_FILE": str(key),
           "B2C_KNOWLEDGE_FETCH_CACHE": str(tmp_path / "fetch")}
    a = kw.build_activities(store=store, cache=None, promotion=None, env=env)
    assert (a.extractor.family, a.judge.family) == ("deepseek", "google") and a.web_enabled is False
    assert a.concept_model.identity.role == "concepts" and max_concurrent_activities(KNOWLEDGE) == 1
    same = dict(env, B2C_KNOWLEDGE_JUDGE_MODEL_ID="deepseek/deepseek-v4-pro")
    with pytest.raises(ValueError):
        kw.build_activities(store=store, cache=None, promotion=None, env=same)
    no_opt_in = dict(env, B2C_KNOWLEDGE_JUDGE_ALLOW_REMOTE="0")
    with pytest.raises(Exception, match="local"):
        kw.build_activities(store=store, cache=None, promotion=None, env=no_opt_in)
