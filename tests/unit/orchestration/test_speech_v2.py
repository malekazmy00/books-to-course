"""Speech v2 activities (R7): accepted text only -> segment audio -> gated tracks.

Real ArtifactStore over moto S3, fake Postgres (translation results + stage registry), a fake
TTS synthesizer that returns speech-like WAVs, and the real speech front end and audio QA.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import boto3
import numpy as np
import pytest
from b2c_contracts import ArtifactRef, EngineIdentity, QEResult, TranslationUnit, UnitTranslation
from b2c_core import ArtifactStore
from b2c_core.stage_registry import StageOutputRegistry
from b2c_speech.audio import PCM, write_wav
from b2c_translation.termbase import BookTermbase, Proposal, TermCandidate
from moto import mock_aws
from temporalio.exceptions import ApplicationError

from b2c_orchestration.promotion import PromotionService
from b2c_orchestration.speech_v2 import (AssembleSpeechTrackInput, GenerateSpeechV2Input, SpeechV2Activities,
                                         SynthesizeSegmentsInput)
from b2c_orchestration.translate_book import AssembleTranslationInput, TranslateBookActivities
from b2c_orchestration.translation_store import TranslationResultStore
from tests.unit.core.fake_stage_registry_db import FakeRegistryDB
from tests.unit.orchestration.fake_translation_db import FakeTranslationDB

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
SHA = "a" * 40
RUN = "e" * 64
ENGINE = EngineIdentity(provider="fake", model_id="fake-mt", revision="r1", prompt_version="fake@1")
UNITS = [("heading", "Chapter 1: Hegemony", [], "الفصل 1: الهيمنة"),
         ("prose", "Gramsci wrote in 1929.", ["1"], "كتب Gramsci في عام 1929 عن الهيمنة. 1"),
         ("prose", "It rose by 3.5 percent.", [], "ارتفعت النسبة 3.5% في العقد الأول من القرن 20."),
         ("footnote", "1 See p. 3.", [], "انظر ص 3."),
         ("prose", "Hegemony is consent.", [], "الهيمنة هي الرضا المنظم. وهي ليست القوة وحدها.")]


def speechlike(seconds, sr=24000, amp=0.05, seed=1):
    rng = np.random.default_rng(seed)
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 660 * t) + 0.1 * rng.standard_normal(n))
    x *= 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    return amp * x / np.max(np.abs(x))


class FakeSynth:
    """0.09 s per non-space character (~11 chars/s); ``bad`` texts come back silent once/always."""

    def __init__(self, silent_first=(), always_silent=()):
        self.calls = []
        self.silent_first, self.always_silent = set(silent_first), set(always_silent)

    def identity(self):
        return {"provider": "fake_tts", "model_id": "fake-voice", "revision": "v1", "sample_rate": 24000,
                "seed_honored": True}

    def synthesize(self, text, *, seed):
        self.calls.append((text, seed))
        seconds = 0.09 * len(text.replace(" ", ""))
        silent = any(m in text for m in self.always_silent) or (
            any(m in text for m in self.silent_first) and seed == 1234)
        x = np.zeros(int(seconds * 24000)) if silent else speechlike(seconds, seed=len(text))
        return write_wav(PCM(samples=x, sample_rate=24000))


class FakeASR:
    identity = {"role": "asr", "model_id": "fake-asr", "revision": "1"}

    def __init__(self, garble=False):
        self.garble = garble

    def transcribe(self, audio, language):
        return "كلام غير مفهوم" if self.garble else self.expected

    expected = ""


@pytest.fixture
def store():
    with mock_aws():
        s = ArtifactStore(boto3.client("s3", region_name="us-east-1"), bucket="artifacts")
        s.ensure_bucket()
        yield s


@pytest.fixture
def results():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return TranslationResultStore("postgresql://unused", connect=FakeTranslationDB().connect, clock=lambda: next(clock))


@pytest.fixture
def promo():
    clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    registry = StageOutputRegistry("postgresql://unused", connect=FakeRegistryDB().connect, clock=lambda: next(clock))
    gate_clock = iter(NOW + timedelta(seconds=i) for i in range(100_000))
    return PromotionService(registry, code_revision=SHA, clock=lambda: next(gate_clock))


def units():
    return [TranslationUnit(unit_id=f"bk:u{i:05d}", book_id="bk", block_ids=[f"bk:b{i:05d}"], kind=kind,
                            source_text=src, footnote_markers=markers, pages=[1], section_path=["Ch"])
            for i, (kind, src, markers, _) in enumerate(UNITS)]


def translation(i, decision="accepted"):
    return UnitTranslation(unit_id=f"bk:u{i:05d}", target_text=UNITS[i][3], origin="engine", engine=ENGINE,
                           qe=QEResult(deterministic_pass=True, route="auto_accept"), decision=decision,
                           decided_by="policy", decided_at=NOW)


def assembly(store, results, promo, decisions=None):
    units_ref = store.put("c1", "translation-units", "c" * 64,
                          {"units.json": json.dumps([u.model_dump(mode="json") for u in units()]).encode()})
    for i in range(len(UNITS)):
        results.upsert(RUN, "bk", translation(i, (decisions or {}).get(i, "accepted")))
    acts = TranslateBookActivities(artifact_store=store, result_store=results, runtime=None, promotion=promo)
    out = acts.assemble(AssembleTranslationInput(run_id=RUN, course_id="c1", units_ref=units_ref.model_dump(mode="json")))
    return out


def termbase_ref(store):
    tb = BookTermbase.new("bk", source_language="en", target_language="ar")
    tb.add_candidate(TermCandidate(source="Gramsci", kind="person", frequency=3),
                     [Proposal(target="غرامشي", provenance="transliteration")])
    tb.approve("Gramsci", "غرامشي", "reviewer-1")
    return store.put("c1", "book-termbase-approved", "f" * 64, {"termbase.json": tb.to_json().encode()})


def speech_input(out, **kw):
    data = dict(course_id="c1", assembly_ref=out.artifact, require_intelligibility=False)
    data.update(kw)
    return GenerateSpeechV2Input(**data)


def acts(store, results, promo, synth=None, asr=None):
    return SpeechV2Activities(artifact_store=store, result_store=results, promotion=promo,
                              synthesizer=synth or FakeSynth(), intelligibility=asr)


# ------------------------------------------------------------------ prepare
def test_prepare_builds_traced_speech_text_from_the_accepted_assembly(store, results, promo):
    out = assembly(store, results, promo)
    assert out.status == "accepted"
    a = acts(store, results, promo)
    prepared = a.prepare(speech_input(out, termbase_ref=termbase_ref(store).model_dump(mode="json")))
    assert prepared.status == "accepted" and prepared.gate_passed
    doc = a._doc(ArtifactRef.model_validate(prepared.speech_text_ref))
    spoken = {s.unit_ids[0]: s.spoken_text for s in doc.segments}
    assert spoken["bk:u00000"] == "الفصل الأول: الهيمنة."  # a heading closes with a stop (final fall)
    assert spoken["bk:u00001"] == "كتب غرامشي في عام ألف وتسعمئة وتسعة وعشرين عن الهيمنة."
    assert "ثلاثة فاصلة خمسة بالمئة" in spoken["bk:u00002"] and "القرن العشرين" in spoken["bk:u00002"]
    assert {e.unit_id: e.reason for e in doc.excluded} == {"bk:u00003": "not_spoken_kind:footnote"}
    record = promo.require_accepted("c1", ArtifactRef.model_validate(prepared.speech_text_ref))
    assert [r.stage for r in record.inputs] == ["translate-book", "book-termbase-approved"]
    assert record.latest_gate_results()["speech.text-frontend"].metrics["lexicon_hits"] == 1
    reading = record.latest_gate_results()["speech.reading-quality"]
    assert reading.passed and reading.metrics["pause_plan_errors"] == [] and reading.metrics["phrases_over_limit"] == 0
    assert doc.segments[0].boundary_after == "heading" and doc.segments[-1].boundary_after == "paragraph"
    assert prepared.plan.total_segments == len(doc.segments) and len(prepared.plan.tracks) == 1
    again = a.prepare(speech_input(out, termbase_ref=termbase_ref(store).model_dump(mode="json")))
    assert again.cache_hit and again.speech_text_ref == prepared.speech_text_ref


def test_latin_name_without_lexicon_rejects_the_speech_text(store, results, promo):
    out = assembly(store, results, promo)
    prepared = acts(store, results, promo).prepare(speech_input(out))
    assert prepared.status == "rejected" and not prepared.gate_passed
    assert prepared.gate_metrics["missed_words"] == ["Gramsci"]
    with_override = acts(store, results, promo).prepare(speech_input(out, lexicon_overrides={"Gramsci": "غرامشي"}))
    assert with_override.status == "accepted"


def test_prepare_refuses_an_assembly_that_is_not_accepted(store, results, promo):  # T-TN-02 (neg)
    out = assembly(store, results, promo, decisions={2: "needs_review"})
    assert out.status == "rejected"
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, lexicon_overrides={"Gramsci": "غرامشي"}))
    assert err.value.type == "NotAcceptedTranslation" and err.value.non_retryable
    assert store.s3.list_objects_v2(Bucket="artifacts", Prefix="c1/speech-text/").get("KeyCount", 0) == 0


def test_prepare_refuses_results_changed_after_acceptance(store, results, promo):
    out = assembly(store, results, promo)
    results.upsert(RUN, "bk", translation(4, "needs_review"))  # a reviewer reopened a unit afterwards
    with pytest.raises(ApplicationError) as err:
        acts(store, results, promo).prepare(speech_input(out, lexicon_overrides={"Gramsci": "غرامشي"}))
    assert err.value.type == "TranslationChanged"


def test_prepare_refuses_legacy_and_foreign_refs(store, results, promo):
    out = assembly(store, results, promo)
    legacy = dict(out.artifact, stage="translate-content")
    foreign = dict(out.artifact, uri=out.artifact["uri"].replace("/c1/", "/c2/"))
    for ref in (legacy, foreign):
        with pytest.raises(ApplicationError) as err:
            acts(store, results, promo).prepare(speech_input(out, assembly_ref=ref))
        assert err.value.type == "InvalidSpeechInput"


def test_prepare_without_registry_refuses(store, results, promo):
    out = assembly(store, results, promo)
    with pytest.raises(ApplicationError) as err:
        acts(store, results, None).prepare(speech_input(out))
    assert err.value.type == "NotAcceptedTranslation"


# ------------------------------------------------------------------ synthesize + assemble
def prepared_text(store, results, promo, synth=None, asr=None):
    out = assembly(store, results, promo)
    a = acts(store, results, promo, synth, asr)
    prepared = a.prepare(speech_input(out, lexicon_overrides={"Gramsci": "غرامشي"}))
    return a, prepared


def synth_all(a, prepared, **kw):
    track = prepared.plan.tracks[0]
    statuses = []
    for batch in track.batches:
        statuses += a.synthesize(SynthesizeSegmentsInput(course_id="c1", speech_text_ref=prepared.speech_text_ref,
                                                         track_id=track.track_id, segment_ids=batch, **kw)).segments
    return track, statuses


def test_synthesis_is_resumable_per_segment(store, results, promo):
    synth = FakeSynth()
    a, prepared = prepared_text(store, results, promo, synth)
    track, first = synth_all(a, prepared)
    calls = len(synth.calls)
    assert calls == len(track.segment_ids) and all(s.status == "ok" for s in first)
    _, again = synth_all(a, prepared)
    assert len(synth.calls) == calls and all(s.cache_hit for s in again)


def test_failed_segment_is_rerendered_with_next_seed_then_kept_as_failed(store, results, promo):
    synth = FakeSynth(silent_first=["الرضا"], always_silent=["الأول:"])
    a, prepared = prepared_text(store, results, promo, synth)
    _, statuses = synth_all(a, prepared, max_attempts=2)
    by_text = {s.segment_id: s for s in statuses}
    retried = [s for s in statuses if s.attempts == 2 and s.status == "ok"]
    failed = [s for s in statuses if s.status == "failed_qa"]
    assert len(retried) == 1 and retried[0].seed == 1235
    assert len(failed) == 1 and "silent" in failed[0].violations and failed[0].attempts == 2
    assert len(by_text) == len(statuses)


def test_track_is_assembled_normalized_timed_and_accepted(store, results, promo):
    a, prepared = prepared_text(store, results, promo)
    track, statuses = synth_all(a, prepared)
    result = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref,
                                                 track=track, segments=statuses, require_intelligibility=False))
    assert result.status == "accepted", result
    assert result.gates == {"speech.segment-qa": True, "speech.track-loudness": True,
                            "speech.duration-outliers": True, "speech.asr-intelligibility": None}
    assert result.metrics["integrated_lufs"] == pytest.approx(-16.0, abs=1.0) and result.metrics["true_peak_dbtp"] <= -1
    ref = ArtifactRef.model_validate(result.artifact)
    timings = json.loads(store.read(ref, "segments.json"))
    assert [t["segment_id"] for t in timings] == track.segment_ids
    assert all(t["end_s"] > t["start_s"] for t in timings)
    assert all(b["start_s"] >= a_["end_s"] for a_, b in zip(timings, timings[1:]))
    assert timings[0]["unit_ids"] == ["bk:u00000"]
    record = promo.require_accepted("c1", ref)
    assert record.inputs == [ArtifactRef.model_validate(prepared.speech_text_ref)]
    assert record.identities[0].model_id == "fake-voice"


def test_track_waits_for_intelligibility_when_required_and_no_asr(store, results, promo):
    a, prepared = prepared_text(store, results, promo)
    track, statuses = synth_all(a, prepared)
    result = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref,
                                                 track=track, segments=statuses, require_intelligibility=True))
    assert result.status == "candidate"  # deterministic gates passed; the semantic gate has no verdict


def test_asr_gate_accepts_or_rejects(store, results, promo):
    class EchoASR(FakeASR):
        def transcribe(self, audio, language):
            return self.texts.pop(0)

    a, prepared = prepared_text(store, results, promo)
    track, statuses = synth_all(a, prepared)
    doc = a._doc(ArtifactRef.model_validate(prepared.speech_text_ref))
    texts = {s.segment_id: s.spoken_text for s in doc.segments}
    echo = EchoASR()
    echo.texts = [texts[sid] for sid in track.segment_ids]
    a.intelligibility = echo
    ok = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref, track=track,
                                             segments=statuses, require_intelligibility=True))
    assert ok.status == "accepted" and ok.metrics["asr"]["median_cer"] == 0.0
    a.intelligibility = FakeASR(garble=True)
    bad = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref, track=track,
                                              segments=statuses, require_intelligibility=True, loudness_profile="acx"))
    assert bad.status == "rejected" and bad.gates["speech.asr-intelligibility"] is False


def test_track_with_a_failed_segment_is_rejected(store, results, promo):
    a, prepared = prepared_text(store, results, promo, FakeSynth(always_silent=["الرضا"]))
    track, statuses = synth_all(a, prepared)
    result = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref,
                                                 track=track, segments=statuses, require_intelligibility=False))
    assert result.status == "rejected" and result.gates["speech.segment-qa"] is False
    assert len(result.failed_segments) == 1
    timings = json.loads(store.read(ArtifactRef.model_validate(result.artifact), "segments.json"))
    assert [t["status"] for t in timings].count("failed_qa") == 1  # per-segment status is kept


def test_incomplete_track_is_not_assembled(store, results, promo):
    a, prepared = prepared_text(store, results, promo)
    track, statuses = synth_all(a, prepared)
    result = a.assemble(AssembleSpeechTrackInput(course_id="c1", speech_text_ref=prepared.speech_text_ref,
                                                 track=track, segments=statuses[:-1]))
    assert result.status == "incomplete" and result.artifact is None
