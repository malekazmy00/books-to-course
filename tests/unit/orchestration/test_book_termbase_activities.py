"""Termbase stage of the rebuilt book translation workflow (D-028 principle 5), with fakes.

Artifacts: real ``ArtifactStore`` over moto S3. Postgres: the in-memory fakes of
``translation_store`` and ``termbase_store``. Engine/QE: fakes from the batch tests.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from b2c_contracts import ArtifactRef
from b2c_core import ArtifactStore
from b2c_translation.engines import EngineCapabilities
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate
from moto import mock_aws
from temporalio.exceptions import ApplicationError

import b2c_orchestration.translate_book as tb
from b2c_orchestration.termbase_review import TermSelection, approve_terms, reject_term
from b2c_orchestration.termbase_store import TermbaseScope, TermbaseStore
from b2c_orchestration.translate_book import (BuildTermbaseInput, CheckTermbaseInput, TranslateBookActivities,
                                              translation_run_id)
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.orchestration.fake_termbase_db import FakeTermbaseDB
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB
from tests.unit.orchestration.test_translate_book_activities import (FakeEngine, FakeMemory, FakeRuntime, PassingQE,
                                                                     batch, book_input, parsed_ref, prepare)

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
SCOPE = TermbaseScope("c1", "bk", "en", "ar")


class TermEngine(FakeEngine):
    capabilities = property(lambda self: EngineCapabilities(
        follows_instructions=True, supports_terms=True, supports_context=True, supports_repair=False,
        max_source_chars=2000))


class ProposingRuntime(FakeRuntime):
    def __init__(self, engine, qe=None, proposer=None):
        super().__init__(engine, qe)
        self.proposer = proposer
        self.asked: list[str] = []

    def term_proposer(self, profile_id):
        self.asked.append(profile_id)
        return self.proposer


class FakeBuilder:
    """Stands in for ``b2c_translation.termbase.build_book_termbase`` (its recall is tuned elsewhere)."""

    def __init__(self, candidates):
        self.candidates = candidates
        self.calls: list[dict] = []

    def __call__(self, units, *, sources, source_language, target_language):
        self.calls.append({"units": units, "sources": sources})
        termbase = BookTermbase.new(units[0].book_id, source_language=source_language,
                                    target_language=target_language, extractor_version="fake@1")
        for source, kind, freq in self.candidates:
            candidate = TermCandidate(source=source, kind=kind, frequency=freq, sections=1)
            proposals = [p for s in sources for p in s.propose(candidate)]
            termbase.add_candidate(candidate, proposals)
        return termbase


class FixedSource:
    def __init__(self, mapping):
        self.mapping = mapping

    def propose(self, candidate):
        target = self.mapping.get(candidate.source)
        return [] if target is None else [Proposal(target=target, provenance="gateway_proposer")]


CANDIDATES = [("Rice", "term", 2), ("Wheat", "term", 1), ("Farmers", "term", 1)]


@pytest.fixture
def artifacts():
    with mock_aws():
        store = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        store.ensure_bucket()
        yield store


@pytest.fixture
def results():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect,
                                  clock=lambda: next(clock))


@pytest.fixture
def terms():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TermbaseStore("postgresql://unused", connect=FakeTermbaseDB().connect, clock=lambda: next(clock))


def acts_for(artifacts, results, terms, *, engine=None, proposer=None, builder=None, memory=None):
    runtime = ProposingRuntime(engine or TermEngine(), PassingQE(), proposer)
    return TranslateBookActivities(artifact_store=artifacts, result_store=results, runtime=runtime,
                                   memory=memory, termbase_store=terms,
                                   termbase_builder=builder or FakeBuilder(CANDIDATES))


def build_input(inp, prepared, **kw):
    data = dict(run_id=translation_run_id(inp), course_id="c1", units_ref=prepared.units_ref,
                engine_profile="fake", source_language="en", target_language="ar")
    data.update(kw)
    return BuildTermbaseInput(**data)


def check_input(inp, **kw):
    data = dict(run_id=translation_run_id(inp), course_id="c1", book_id="bk", source_language="en",
                target_language="ar")
    data.update(kw)
    return CheckTermbaseInput(**data)


def setup(artifacts, results, terms, **kw):
    inp = book_input(parsed_ref(artifacts))
    acts = acts_for(artifacts, results, terms, **kw)
    prepared = prepare(acts, inp)
    return inp, acts, prepared


# ------------------------------------------------------------------ build
def test_build_stores_versioned_artifact_and_pending_review_state(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    built = acts.build_termbase(build_input(inp, prepared))
    ref = ArtifactRef.model_validate(built.artifact)
    assert ref.stage == tb.TERMBASE_STAGE and ref.uri.startswith("s3://artifacts/c1/book-termbase/")
    stored = BookTermbase.from_json(artifacts.read(ref, "termbase.json"))  # sha256 verified
    assert stored.sha256() == built.termbase_sha256 and stored.book_id == "bk"
    assert {e.source for e in stored.entries} == {"Rice", "Wheat", "Farmers"}
    assert all(e.status == "pending" and e.approved_by is None for e in stored.entries)
    assert built.entry_count == 3 and built.extracted == 3 and built.absent == 0 and not built.proposer
    assert terms.get_build(SCOPE).artifact_ref == built.artifact
    # the stored units of this run were the extraction input
    assert [u.unit_id for u in acts.termbase_builder.calls[0]["units"]] == prepared.unit_ids


def test_rebuild_keeps_human_decisions_and_versions_the_artifact(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    first = acts.build_termbase(build_input(inp, prepared))
    approve_terms(terms, SCOPE, [TermSelection("Rice", "الأرز")], "aly")
    reject_term(terms, SCOPE, "Farmers", "aly", "common word")
    acts.termbase_builder = FakeBuilder([("Rice", "term", 3), ("Wheat", "term", 1)])
    second = acts.build_termbase(build_input(inp, prepared))
    assert second.artifact != first.artifact and second.absent == 1
    stored = BookTermbase.from_json(artifacts.read(ArtifactRef.model_validate(second.artifact), "termbase.json"))
    assert stored.get("Rice").status == "approved" and stored.get("Rice").target == "الأرز"
    assert stored.get("Rice").candidate.frequency == 3
    assert stored.get("Farmers").status == "rejected"


def test_sources_in_priority_order_and_proposer_only_when_the_profile_enables_it(artifacts, results, terms):
    from b2c_translation.glossary import ContextualGlossary
    from b2c_translation.termbase import (GlossarySource, MemoryTermSource, PriorTermbaseSource,
                                          TransliterationSource)

    class LookupMemory(FakeMemory):
        def lookup_entry(self, unit, source_language, target_language):
            return None

    inp, acts, prepared = setup(artifacts, results, terms)
    acts.build_termbase(build_input(inp, prepared))
    kinds = [type(s) for s in acts.termbase_builder.calls[0]["sources"]]
    assert kinds == [TransliterationSource]  # no glossary, no prior approvals, no TM, no proposer
    assert acts.runtime.asked == ["fake"]

    # an approval in another course of the same book becomes a 'prior_termbase' proposal
    other = TermbaseScope("c0", "bk", "en", "ar")
    terms.merge_extraction(other, FakeBuilder([("Wheat", "term", 1)])(
        acts.units_cache.get(artifacts, ArtifactRef.model_validate(prepared.units_ref)), sources=[],
        source_language="en", target_language="ar"))
    approve_terms(terms, other, [TermSelection("Wheat", "القمح")], "aly")
    proposer = FixedSource({"Farmers": "المزارعون"})
    acts = acts_for(artifacts, results, terms, proposer=proposer, memory=LookupMemory())
    glossary = ContextualGlossary(domain_id="agri", mappings={"Rice": "الأرز"})
    built = acts.build_termbase(build_input(inp, prepared, contextual_glossary=glossary))
    kinds = [type(s) for s in acts.termbase_builder.calls[0]["sources"]]
    assert kinds == [GlossarySource, PriorTermbaseSource, MemoryTermSource, FixedSource, TransliterationSource]
    assert built.proposer
    by_source = {e.source: e for e in terms.entries(SCOPE)}
    assert by_source["Rice"].proposals[0].provenance == "glossary"
    assert by_source["Wheat"].proposals[0].provenance == "prior_termbase"
    assert by_source["Wheat"].status == "pending"  # a prior approval is a proposal, never an approval
    assert by_source["Farmers"].proposals[0].provenance == "gateway_proposer"


def test_real_builder_runs_on_the_stored_units(artifacts, results, terms):
    """Wiring only: the public ``build_book_termbase`` on stored units; recall is tested elsewhere."""
    from tests.unit.translation.termbase.conftest import make_units
    units = make_units(book_id="bk")
    ref = artifacts.put("c1", "translation-units", "e" * 64,
                        {"units.json": json.dumps([u.model_dump(mode="json") for u in units]).encode()})
    acts = TranslateBookActivities(artifact_store=artifacts, result_store=results,
                                   runtime=ProposingRuntime(TermEngine()), termbase_store=terms)
    built = acts.build_termbase(BuildTermbaseInput(run_id="f" * 64, course_id="c1", units_ref=ref.model_dump(
        mode="json"), engine_profile="fake", source_language="en", target_language="ar"))
    entries = terms.entries(SCOPE)
    assert built.entry_count == len(entries) > 0
    assert all(e.status == "pending" for e in entries)


def test_build_refuses_foreign_units_and_needs_a_store(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    with pytest.raises(ApplicationError) as err:
        acts.build_termbase(build_input(inp, prepared, course_id="c2"))
    assert err.value.type == "InvalidTranslationSource" and err.value.non_retryable
    acts.termbase_store = None
    with pytest.raises(ApplicationError) as err:
        acts.build_termbase(build_input(inp, prepared))
    assert err.value.type == "TermbaseNotConfigured" and err.value.non_retryable


# ------------------------------------------------------------------ gate
def test_gate_stays_closed_while_entries_at_or_above_the_threshold_are_pending(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    acts.build_termbase(build_input(inp, prepared))
    gate = acts.check_termbase(check_input(inp))
    assert not gate.open and gate.snapshot is None and gate.counts["pending_blocking"] == 3
    # threshold 2: only "Rice" (frequency 2) blocks
    assert acts.check_termbase(check_input(inp, min_frequency=2)).counts["pending_blocking"] == 1
    approve_terms(terms, SCOPE, [TermSelection("Rice", "الأرز")], "aly")
    gate = acts.check_termbase(check_input(inp, min_frequency=2))
    assert gate.open and gate.locked_terms == 1 and gate.counts["pending_total"] == 2
    snap = BookTermbase.from_json(artifacts.read(ArtifactRef.model_validate(gate.snapshot), "termbase.json"))
    assert [e.source for e in snap.entries] == ["Rice"]  # pending entries are not in the snapshot
    assert ArtifactRef.model_validate(gate.snapshot).stage == tb.TERMBASE_APPROVED_STAGE


def test_without_the_requirement_pending_entries_are_ignored_not_approved(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    acts.build_termbase(build_input(inp, prepared))
    gate = acts.check_termbase(check_input(inp, require_approved=False))
    assert gate.open and gate.locked_terms == 0 and gate.entities == 0
    assert all(e.status == "pending" for e in terms.entries(SCOPE))


# ------------------------------------------------------------------ batches use approved entries only
def gate_for(acts, inp, terms, approvals):
    approve_terms(terms, SCOPE, [TermSelection(s, t) for s, t in approvals], "aly", replace=True)
    return acts.check_termbase(check_input(inp, require_approved=False))


def test_batches_get_locked_terms_and_reuse_results_only_under_the_same_digest(artifacts, results, terms):
    engine = TermEngine()
    inp, acts, prepared = setup(artifacts, results, terms, engine=engine)
    acts.build_termbase(build_input(inp, prepared))
    gate = gate_for(acts, inp, terms, [("Rice", "الأرز")])
    ids = prepared.unit_ids
    first = batch(inp, prepared, ids).model_copy(update={"termbase_ref": gate.snapshot,
                                                         "termbase_digest": gate.digest})
    out = acts.translate_batch(first)
    assert out.translated == 5
    rice = next(c for c in engine.calls if c.unit.source_text.startswith("Rice"))
    assert dict(rice.terms) == {"Rice": "الأرز"}  # only APPROVED, only terms occurring in the unit
    assert all("Wheat" not in c.terms for c in engine.calls)

    # resuming with the same approved termbase keeps every result
    engine.calls.clear()
    assert acts.translate_batch(first).skipped == 5 and engine.calls == []

    # a changed approved termbase gets a new digest: policy results are produced again
    gate2 = gate_for(acts, inp, terms, [("Rice", "الأرز"), ("Wheat", "القمح")])
    assert gate2.digest != gate.digest
    second = first.model_copy(update={"termbase_ref": gate2.snapshot, "termbase_digest": gate2.digest})
    out = acts.translate_batch(second)
    assert out.skipped == 0 and out.translated == 5
    rice = next(c for c in engine.calls if c.unit.source_text.startswith("Rice"))
    assert dict(rice.terms) == {"Rice": "الأرز", "Wheat": "القمح"}


def test_hint_entries_reach_the_engine_as_hints_not_locked_terms(artifacts, results, terms):  # D-039
    engine = TermEngine()
    inp, acts, prepared = setup(artifacts, results, terms, engine=engine)
    acts.build_termbase(build_input(inp, prepared))
    approve_terms(terms, SCOPE, [TermSelection("Rice", "الأرز", tier="hint")], "aly")
    gate = acts.check_termbase(check_input(inp, require_approved=False))
    assert gate.open and gate.locked_terms == 0
    run = batch(inp, prepared, prepared.unit_ids).model_copy(update={"termbase_ref": gate.snapshot,
                                                                     "termbase_digest": gate.digest})
    acts.translate_batch(run)
    rice = next(c for c in engine.calls if c.unit.source_text.startswith("Rice"))
    assert dict(rice.terms) == {} and dict(rice.hints) == {"Rice": "الأرز"}


def test_batch_refuses_a_digest_that_does_not_match_the_snapshot(artifacts, results, terms):
    inp, acts, prepared = setup(artifacts, results, terms)
    acts.build_termbase(build_input(inp, prepared))
    gate = gate_for(acts, inp, terms, [("Rice", "الأرز")])
    bad = batch(inp, prepared, prepared.unit_ids[:1]).model_copy(update={"termbase_ref": gate.snapshot,
                                                                         "termbase_digest": "e" * 64})
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(bad)
    assert err.value.type == "TermbaseChanged" and err.value.non_retryable
    orphan = bad.model_copy(update={"termbase_ref": None})
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(orphan)
    assert err.value.type == "InvalidTermbase"
    wrong_stage = bad.model_copy(update={"termbase_ref": prepared.units_ref, "termbase_digest": gate.digest})
    with pytest.raises(ApplicationError) as err:
        acts.translate_batch(wrong_stage)
    assert err.value.type == "InvalidTranslationSource"


def test_human_results_survive_a_termbase_change(artifacts, results, terms):
    from b2c_contracts import UnitTranslation
    engine = TermEngine()
    inp, acts, prepared = setup(artifacts, results, terms, engine=engine)
    acts.build_termbase(build_input(inp, prepared))
    gate = gate_for(acts, inp, terms, [("Rice", "الأرز")])
    run_id = translation_run_id(inp)
    results.upsert(run_id, "bk", UnitTranslation(unit_id="bk:u00003", target_text="بشري.", origin="human",
                                                 decision="accepted", decided_by="human", decided_at=NOW))
    acts.translate_batch(batch(inp, prepared, ["bk:u00003"]).model_copy(
        update={"termbase_ref": gate.snapshot, "termbase_digest": gate.digest}))
    assert engine.calls == [] and results.fetch(run_id, "bk:u00003").translation.target_text == "بشري."
