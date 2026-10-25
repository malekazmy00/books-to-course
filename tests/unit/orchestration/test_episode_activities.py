"""Episode-script activities and workflow (D-035..D-038): EN script per episode as a promotion
candidate with system GateResults, its translation through UnitTranslator with the approved
termbase, the book text taken from the book run's accepted units, the original term at its first
mention (book text and script lines), and the workflow order. moto S3 + fake registry + fakes."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws
from temporalio.testing import ActivityEnvironment

import b2c_contracts as c
from b2c_concepts import BookIndex, ConceptModel, build_concept_layer
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_episodes import ModelRole
from b2c_orchestration import episodes as ep
from b2c_orchestration.episodes import EpisodeActivities, EpisodeScriptARInput, EpisodeScriptENInput
from b2c_orchestration.promotion import PromotionService
from b2c_translation.engines import EngineCapabilities, EngineResult
from b2c_translation.units import build_units_with_provenance
from tests.unit.concepts.test_concept_layer import SEEDS, book, model_script, outline
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.orchestration.test_knowledge_activities import termbase
from tests.unit.orchestration.test_translate_book_activities import FakeRuntime, PassingQE
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "c" * 40
RUN = "e" * 64
TERMS_AR = {"hegemony": "الهيمنة", "war of position": "حرب المواقع", "passive revolution": "الثورة السلبية"}
FILLER = ("نص عربي واضح يشرح الفكرة كما وردت عند الكاتب في هذا الموضع من الكتاب مع بيان المعنى العام "
          "للقارئ والسامع دون زيادة أو نقصان في المضمون الأصلي للجملة المترجمة هنا").split()


def arabic_for(source: str) -> str:
    out = []
    for sentence in [s for s in re.split(r"(?<=[.?!])\s+", source.strip()) if s]:
        words = [FILLER[i % len(FILLER)] for i in range(max(3, len(sentence.split())))]
        for en, ar in TERMS_AR.items():
            if re.search(rf"\b{en}\b", sentence, re.I):
                words.insert(1, ar)
        if re.search(r"\b(not|without|no)\b", sentence, re.I):
            words.insert(0, "لا")
        out.append(" ".join(words) + ("؟" if sentence.endswith("?") else "."))
    return " ".join(out)


class Engine:
    identity = property(lambda self: c.EngineIdentity(provider="fake", model_id="fake-mt", prompt_version="p@1"))
    capabilities = property(lambda self: EngineCapabilities(follows_instructions=True, supports_terms=True,
                                                            supports_context=True, supports_repair=False,
                                                            max_source_chars=2000))

    def __init__(self):
        self.calls = []

    def translate(self, request):
        self.calls.append(request)
        return EngineResult(text=arabic_for(request.unit.source_text), identity=self.identity)


def gen_script(pv, msg):
    data = json.loads(msg[msg.index("{"):])
    if pv == "ep-recap-v1":
        blocks = [b for e in data["previous_episodes"] for b in e["blocks"] if b["type"] != "heading"]
        return {"recap": [{"text": "Earlier: " + blocks[0]["text"][:60], "evidence": [
            {"block": blocks[0]["id"], "quote": blocks[0]["text"][:40]}]}]}
    blocks = [b for b in data["lesson"] if b["type"] != "heading"]
    ev = lambda b: [{"block": b["id"], "quote": b["text"][:40]}]
    item = lambda b: {"text": b["text"][:120], "evidence": ev(b)}
    return {"covers": [item(blocks[0])], "summary": [item(b) for b in blocks],
            "definitions": [{"term": t["term"], "text": f"The {t['term']} as the lesson uses it.", "evidence": ev(blocks[-1])}
                            for t in data["new_terms"] if t["needs_definition"]],
            "questions": [{"question": f"What does the lesson say in part {i}?", "answer": blocks[i % len(blocks)]["text"][:80],
                           "evidence": ev(blocks[i % len(blocks)])} for i in range(6)]}


def judge_script(pv, msg):
    data = json.loads(msg[msg.index("{"):])
    return {"verdicts": [{"id": i["id"], "supported": True} for i in data["items"]]}


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


def put(store, stage, files):
    return store.put("crs", stage, hashlib.sha256(b"".join(files.values())).hexdigest(), files)


def setup(store):
    parsed = book()
    units = build_units_with_provenance(parsed, max_unit_chars=400).units
    index = BookIndex.from_parsed_v1(parsed, outline())
    ident = c.ProducerIdentity(role="x", provider="openrouter", model_id="deepseek/x")
    layer = build_concept_layer(index, SEEDS, model=ConceptModel(ScriptedLLM(model_script), ident),
                                termbase_basis="approved", clock=lambda: NOW)
    refs = dict(
        parsed=put(store, "ingest-parse-pdf", {"parsed-book.json": parsed.model_dump_json().encode()}),
        outline=put(store, "approve-outline", {"approved-outline.json": json.dumps(
            {"outline": outline().model_dump(mode="json")}).encode()}),
        units=put(store, "translation-units", {"units.json": json.dumps([u.model_dump(mode="json") for u in units]).encode()}),
        concepts=put(store, "concept-layer", {"concept-layer.json": layer.model_dump_json().encode()}),
        termbase=put(store, "book-termbase-approved", {"termbase.json": termbase().to_json().encode()}))
    results = {u.unit_id: c.UnitTranslation(unit_id=u.unit_id, target_text=arabic_for(u.source_text), origin="rule",
                                            decision="accepted", decided_by="policy", decided_at=NOW) for u in units}
    return refs, units, results


def acts(store, db, results, engine=None):
    ident = lambda m: c.ProducerIdentity(role="x", provider="openrouter", model_id=m)
    return EpisodeActivities(
        artifact_store=store, promotion=promotion(db), clock=lambda: NOW,
        generator=ModelRole(ScriptedLLM(gen_script, "deepseek/x"), ident("deepseek/x"), "deepseek"),
        judge=ModelRole(ScriptedLLM(judge_script, "google/y"), ident("google/y"), "google"),
        runtime=FakeRuntime(engine or Engine(), PassingQE()), book_results=lambda run_id: results)


def common(refs):
    d = lambda r: r.model_dump(mode="json")
    return dict(course_id="crs", parsed_ref=d(refs["parsed"]), outline_ref=d(refs["outline"]),
                units_ref=d(refs["units"]), concept_ref=d(refs["concepts"]), translation_run_id=RUN)


def test_en_then_ar_episode_with_promotion_and_first_mentions(store):
    db = FakeRegistryDB()
    refs, units, results = setup(store)
    a = acts(store, db, results)
    env = ActivityEnvironment()
    en = env.run(a.build_en, EpisodeScriptENInput(**common(refs), episode=2, require_accepted_inputs=False))
    ref = c.ArtifactRef.model_validate(en["ref"])
    assert ref.stage == ep.EN_STAGE and en["has_script"] and en["stage_output_status"] == "accepted", en
    script = c.EpisodeScript.model_validate_json(store.read(ref, "script.json"))
    assert script.episode == 2 and script.lesson_id == "m02.l01" and script.language == "en"
    recap = [x for x in script.sections[0].lines if x.line_id.split(".")[-1].startswith("r")]
    assert recap and all(s.lesson_id == "m01.l01" for x in recap for s in x.sources)
    record = a.promotion.registry.get("crs", ref.stage, ref.key)
    assert set(record.required_gates) == {"episodes.grounding", "episodes.validation", "episodes.length",
                                          "episodes.coverage"}
    again = env.run(a.build_en, EpisodeScriptENInput(**common(refs), episode=2, require_accepted_inputs=False))
    assert again["cache_hit"] is True and again["ref"] == en["ref"]

    ar = env.run(a.translate, EpisodeScriptARInput(**common(refs), episode=2, en_ref=en["ref"],
                                                   termbase_ref=refs["termbase"].model_dump(mode="json"),
                                                   engine_profile="fake", qe_profile="qe"))
    aref = c.ArtifactRef.model_validate(ar["ref"])
    assert aref.stage == ep.AR_STAGE
    script_ar = c.EpisodeScript.model_validate_json(store.read(aref, "script.json"))
    assert script_ar.language == "ar" and script_ar.source_script_sha256 == ref.sha256
    gates = json.loads(store.read(aref, "gates.json"))
    assert gates["episodes.first-mention"]["passed"], gates["episodes.first-mention"]["metrics"]
    texts = " ".join(x.text for x in script_ar.all_lines())
    assert texts.count("(war of position)") >= 1  # inserted at the first mention (key terms add their own)
    first = next(x for x in script_ar.all_lines() if "حرب المواقع" in x.text)
    assert "حرب المواقع (war of position)" in first.text and "war of position" in first.inserted_originals
    book_section = next(s for s in script_ar.sections if s.kind == "book_text")
    assert book_section.lines and all(x.unit_ids[0] in results for x in book_section.lines)
    assert ar["gates"]["episodes.translation"] is True, gates["episodes.translation"]["metrics"]
    assert ar["stage_output_status"] == "accepted"
    translations = json.loads(store.read(aref, "translations.json"))
    assert all(t["unit"]["section_path"][0] == "episode-script" for t in translations)


def test_book_text_not_accepted_keeps_ar_out(store):
    db = FakeRegistryDB()
    refs, units, results = setup(store)
    victim = next(u for u in units if "Hegemony again" in u.source_text)
    results[victim.unit_id] = c.UnitTranslation(unit_id=victim.unit_id, target_text="نص.", origin="rule",
                                                decision="needs_review", decided_by="policy", decided_at=NOW)
    a = acts(store, db, results)
    env = ActivityEnvironment()
    en = env.run(a.build_en, EpisodeScriptENInput(**common(refs), episode=2, require_accepted_inputs=False))
    ar = env.run(a.translate, EpisodeScriptARInput(**common(refs), episode=2, en_ref=en["ref"],
                                                   termbase_ref=refs["termbase"].model_dump(mode="json"),
                                                   engine_profile="fake", qe_profile="qe"))
    assert ar["gates"]["episodes.translation"] is False and ar["stage_output_status"] == "rejected"


def test_inputs_must_be_accepted(store):
    db = FakeRegistryDB()
    refs, _, results = setup(store)
    a = acts(store, db, results)
    with pytest.raises(ValueError, match="concept layer"):
        ActivityEnvironment().run(a.build_en, EpisodeScriptENInput(**common(refs), episode=1))


def test_course_intro_without_material_is_rejected_with_a_report(store):
    db = FakeRegistryDB()
    refs, _, results = setup(store)
    a = acts(store, db, results)
    out = ActivityEnvironment().run(a.build_en, EpisodeScriptENInput(**common(refs), episode=0,
                                                                     require_accepted_inputs=False))
    assert out["has_script"] is False and out["stage_output_status"] == "rejected"
    report = json.loads(store.read(c.ArtifactRef.model_validate(out["ref"]), "report.json"))
    assert "failed" in report


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


async def test_workflow_per_episode_en_then_ar(wf_env):
    from concurrent.futures import ThreadPoolExecutor

    from temporalio import activity
    from temporalio.worker import Worker

    calls = []

    @activity.defn(name=ep.EN_ACTIVITY)
    def en(inp: EpisodeScriptENInput) -> dict:
        calls.append(("en", inp.episode))
        accepted = inp.episode != 5
        return {"ref": {"stage": ep.EN_STAGE, "episode": inp.episode}, "has_script": True,
                "stage_output_status": "accepted" if accepted else "rejected"}

    @activity.defn(name=ep.AR_ACTIVITY)
    def ar(inp: EpisodeScriptARInput) -> dict:
        calls.append(("ar", inp.episode, inp.en_ref["episode"]))
        return {"stage_output_status": "accepted"}

    refs = dict(course_id="crs", parsed_ref={}, outline_ref={}, units_ref={}, concept_ref={}, translation_run_id=RUN)
    async with Worker(wf_env.client, task_queue="e", workflows=[ep.BuildEpisodeScriptsWorkflow],
                      activities=[en, ar], activity_executor=ThreadPoolExecutor(2)):
        out = await wf_env.client.execute_workflow(
            ep.BuildEpisodeScriptsWorkflow.run, ep.BuildEpisodeScriptsInput(
                **refs, episodes=[0, 5, 8], termbase_ref={}, engine_profile="fake"), id="wf-e", task_queue="e")
    assert calls == [("en", 0), ("ar", 0, 0), ("en", 5), ("en", 8), ("ar", 8, 8)]
    assert out["episodes"]["05"]["ar"] == {"skipped": "the English script is not accepted"}


def test_audio_title_drops_section_numbers():
    assert ep.audio_title("1.1 Our Vision") == "Our Vision"
    assert ep.audio_title("Chapter 2 Two Types\nof Thinking") == "Two Types of Thinking"
    assert ep.audio_title("Preface") == "Preface"
