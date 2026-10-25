"""Figure-description activities (D-044): the English document as a gated promotion candidate, its
translation through UnitTranslator with the approved termbase, and the speech text reading the accepted
Arabic descriptions in place. moto S3 + fake registry + scripted model + fake MT engine."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

import b2c_contracts as c
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_episodes import ModelRole
from b2c_orchestration.figures import AR_STAGE, EN_STAGE, FigureActivities, FiguresARInput, FiguresENInput
from b2c_orchestration.promotion import PromotionService
from b2c_translation.units import build_units_with_provenance
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.orchestration.test_episode_activities import Engine, arabic_for
from tests.unit.orchestration.test_knowledge_activities import termbase
from tests.unit.orchestration.test_translate_book_activities import FakeRuntime, PassingQE
from tests.unit.research.fakes import ScriptedLLM

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
B = "bk"


def blk(page, seq, type_, text):
    return c.Block(block_id=f"{B}:{page}:{seq}", type=type_, text=text, section_path=["Chapter 2"], ocr=False,
                   refs=[c.PageRef(book_id=B, page_no=page)])


def parsed():
    blocks = [blk(1, 0, "heading", "Chapter 2 Thinking"),
              blk(1, 1, "paragraph", "Analysts think in two ways. Figure 2.1 contrasts System 1 with System 2."),
              blk(1, 2, "figure", "System 1 Intuitive Fast System 2 Deliberate Slow"),
              blk(1, 3, "caption", "Figure 2.1 System 1 and System 2 Thinking"),
              blk(2, 0, "paragraph", "Structured techniques slow the analyst down on purpose."),
              blk(2, 1, "table", "| Technique | Purpose |\n| --- | --- |\n| Brainstorming | Generate ideas |"),
              blk(2, 2, "caption", "Figure 2.2 One Core Technique")]
    return c.ParsedBook(book_id=B, title="t", role="spine", page_count=2, toc_source="headings", blocks=blocks,
                        parser={"name": "x"})


class NumberKeepingEngine(Engine):
    """The fake MT keeps the source's numbers (the QA number check is real)."""

    def translate(self, request):
        import re
        from b2c_translation.engines import EngineResult
        self.calls.append(request)
        nums = " ".join(re.findall(r"\d+(?:\.\d+)?", request.unit.source_text))
        return EngineResult(text=arabic_for(request.unit.source_text).rstrip(".") + (f" {nums}." if nums else "."),
                            identity=self.identity)


def describe_script(pv, msg):
    return {"lines": ["The figure contrasts System 1, intuitive and fast, with System 2, deliberate and slow."]}


@pytest.fixture
def store():
    with mock_aws():
        s = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        s.ensure_bucket()
        yield s


def promotion():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect, clock=lambda: next(clock))
    return PromotionService(registry, code_revision="c" * 40, clock=lambda: NOW)


def put(store, stage, files):
    return store.put("crs", stage, hashlib.sha256(b"".join(files.values())).hexdigest(), files)


def test_describe_translate_and_read_in_place(store):
    book = parsed()
    units = build_units_with_provenance(book, max_unit_chars=400).units
    parsed_ref = put(store, "ingest-parse-pdf", {"parsed-book.json": book.model_dump_json().encode()})
    units_ref = put(store, "translation-units",
                    {"units.json": json.dumps([u.model_dump(mode="json") for u in units]).encode()})
    tb_ref = put(store, "book-termbase-approved", {"termbase.json": termbase().to_json().encode()})
    promo = promotion()
    ident = c.ProducerIdentity(role="figure-describer", provider="openrouter", model_id="deepseek/x")
    acts = FigureActivities(artifact_store=store, promotion=promo, clock=lambda: NOW,
                            generator=ModelRole(ScriptedLLM(describe_script, "deepseek/x"), ident, "deepseek"),
                            runtime=FakeRuntime(NumberKeepingEngine(), PassingQE()))
    en = acts.describe(FiguresENInput(course_id="crs", parsed_ref=parsed_ref.model_dump(mode="json"),
                                      units_ref=units_ref.model_dump(mode="json")))
    assert en["stage_output_status"] == "accepted" and en["gates"] == {"figures.grounding": True}
    en_ref = c.ArtifactRef.model_validate(en["ref"])
    assert en_ref.stage == EN_STAGE
    doc = c.FigureDescriptionsDocument.model_validate_json(store.read(en_ref, "figures.json"))
    assert [d.label for d in doc.descriptions] == ["Figure 2.1", "Figure 2.2"]
    assert [d.mode for d in doc.descriptions] == ["description", "table_rows"]
    first_prose = next(u for u in units if "Figure 2.1" in u.source_text)
    assert doc.descriptions[0].after_unit_id == first_prose.unit_id
    again = acts.describe(FiguresENInput(course_id="crs", parsed_ref=parsed_ref.model_dump(mode="json"),
                                         units_ref=units_ref.model_dump(mode="json")))
    assert again["cache_hit"]

    ar = acts.translate(FiguresARInput(course_id="crs", en_ref=en["ref"], termbase_ref=tb_ref.model_dump(mode="json"),
                                       engine_profile="fake", qe_profile="fake"))
    assert ar["stage_output_status"] == "accepted" and ar["gates"] == {"figures.translation": True}
    ar_ref = c.ArtifactRef.model_validate(ar["ref"])
    assert ar_ref.stage == AR_STAGE
    ar_doc = c.FigureDescriptionsDocument.model_validate_json(store.read(ar_ref, "figures.json"))
    assert ar_doc.language == "ar" and ar_doc.source_document_sha256 == en_ref.sha256
    assert all(x.status == "accepted" and x.unit_ids[0].startswith(f"{B}:fig:u") for d in ar_doc.descriptions
               for x in d.lines)

    # speech: the accepted Arabic descriptions are read after their anchor, in the course voice
    from b2c_speech.text import PronunciationLexicon, build_speech_text
    results = {u.unit_id: c.UnitTranslation(unit_id=u.unit_id, target_text="نص عربي واضح هنا.", origin="rule",
                                            decision="accepted", decided_by="policy", decided_at=NOW) for u in units}
    speech = build_speech_text(units, results, book_id=B, source=parsed_ref, lexicon=PronunciationLexicon(),
                               figures=ar_doc, figures_ref=ar_ref)
    figs = [s for s in speech.segments if s.figure_id]
    assert {s.figure_id for s in figs} == {"fig001", "fig002"}
    i = speech.segments.index(figs[0])
    assert speech.segments[i - 1].unit_ids == [first_prose.unit_id]


def test_translation_requires_the_accepted_english_document(store):
    acts = FigureActivities(artifact_store=store, promotion=promotion(), generator=None,
                            runtime=FakeRuntime(Engine(), PassingQE()), clock=lambda: NOW)
    fake = put(store, EN_STAGE, {"figures.json": b"{}"})
    tb_ref = put(store, "book-termbase-approved", {"termbase.json": termbase().to_json().encode()})
    with pytest.raises(ValueError, match="not accepted"):
        acts.translate(FiguresARInput(course_id="crs", en_ref=fake.model_dump(mode="json"),
                                      termbase_ref=tb_ref.model_dump(mode="json"), engine_profile="fake"))
