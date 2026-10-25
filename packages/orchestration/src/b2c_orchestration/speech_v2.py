"""Speech v2 (S9, roadmap R7; audit 07 C1-C4): accepted text -> segments -> gated tracks.

New names only; ``GenerateMP3Workflow`` and its activity are untouched.

    speech_v2_prepare              accepted translate-book assembly (registry) + its unit results
                                   (digest re-checked) + approved termbase -> ``speech-text``
                                   artifact (SpeechTextDocument + bounded plan) -> gates
                                   ``speech.text-frontend`` and ``speech.reading-quality``
                                   (phrase limits, pause plan, speakable text) -> accepted | rejected
    speech_v2_synthesize_segments  one bounded batch of segments -> content-addressed
                                   ``speech-segment-audio`` artifacts (WAV + per-segment QA);
                                   a retry renders only what is missing; a segment that fails QA
                                   is re-rendered with the next seed up to ``max_attempts``
    speech_v2_assemble_track       segment WAVs + structural pauses -> loudness-normalized track,
                                   ``segments.json`` timings -> candidate -> gates
                                   ``speech.segment-qa``, ``speech.track-loudness``,
                                   ``speech.duration-outliers`` (+ ``speech.asr-intelligibility``)
                                   -> accepted | rejected | candidate (gate still open)

Nothing here reads a translation that is not accepted: the assembly must be ``accepted`` in the
stage-output registry, every unit result must still be the one the assembly was built from,
and ``build_speech_text`` refuses any unit that is not accepted/dropped.

Second, explicit acceptance path (D-041): with ``notes_ref`` the accepted ``translator-notes``
artifact (gate ``translator-notes.coverage``: every unit accepted, dropped or annotated) takes the
place of the strict assembly acceptance. The notes must belong to the assembly's run and units and
be built from the unit results as they are now; annotated units are spoken as course-voice notes.

Figures (D-044): with ``figures_ref`` the accepted ``figure-descriptions-ar`` artifact is read in place,
in the course voice (accepted descriptions only); it is an input of the speech-text artifact.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import (ArtifactRef, FigureDescriptionsDocument, GateResult, ProducerIdentity,
                               SpeechTextDocument, TranslatorNotesDocument, stage_key)
    from b2c_speech.plan import PlanLimits, SpeechPlan, TrackPlan, VoiceConfig, plan_tracks, segment_audio_key
    from b2c_speech.plan import SEGMENT_AUDIO_STAGE

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .promotion import GatePolicy, NotAccepted, PromotionService

SPEECH_TEXT_STAGE = "speech-text"
SPEECH_TRACK_STAGE = "speech-track"
SPEECH_TRACK_VERSION = "2"  # 2: segment edge silence trimmed, pauses exact (D-042)
ASSEMBLY_STAGE = "translate-book"
UNITS_STAGE = "translation-units"
TERMBASE_APPROVED_STAGE = "book-termbase-approved"
NOTES_STAGE = "translator-notes"
FIGURES_STAGE = "figure-descriptions-ar"
DEFAULT_LOUDNESS_PROFILE = "app-speech-16"  # plan recommendation; owner decision Q2 pending
# Provisional ASR thresholds (audit 07 §5.5): median CER <= 5%, p95 <= 12%, to be calibrated
# against a human recording of the same text (T-TTS-02) before the gate is trusted.
ASR_POLICY_DEFINITION = {"max_median_cer": 0.05, "max_p95_cer": 0.12, "provisional": True}
NON_RETRYABLE = ["InvalidSpeechInput", "NotAcceptedTranslation", "NotSpeechReady", "TranslationChanged",
                 "UnknownLoudnessProfile"]


def _fail(message: str, error_type: str) -> ApplicationError:
    return ApplicationError(message[:400], type=error_type, non_retryable=True)


# ------------------------------------------------------------------ messages
class GenerateSpeechV2Input(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    assembly_ref: dict[str, Any]  # accepted translate-book assembly (or the assembly of accepted notes_ref)
    notes_ref: dict[str, Any] | None = None  # D-041: accepted translator-notes artifact (alternative acceptance)
    figures_ref: dict[str, Any] | None = None  # D-044: accepted Arabic figure-descriptions artifact (read in place)
    termbase_ref: dict[str, Any] | None = None  # book-termbase-approved snapshot (pronunciation lexicon)
    lexicon_overrides: dict[str, str] = {}  # reviewed speech lexicon: Latin source form -> Arabic spoken form
    engine_profile: str = "fasih-xtts"  # b2c_speech.text.ENGINE_PROFILES: phrase limits of the TTS engine
    language: Literal["ar"] = "ar"
    loudness_profile: str = DEFAULT_LOUDNESS_PROFILE
    voice_id: str = "default"
    seed: int = 1234
    max_attempts: int = Field(default=2, ge=1, le=5)
    require_intelligibility: bool = True
    limits: PlanLimits = PlanLimits()
    track_ids: list[str] | None = None  # render only these tracks of the plan
    activity_task_queue: str = "audio"


class SpeechPrepared(BaseModel):
    speech_text_ref: dict[str, Any]
    status: str | None  # registry status of the speech-text artifact
    gate_passed: bool
    gate_metrics: dict[str, Any]
    plan: SpeechPlan
    cache_hit: bool


class SynthesizeSegmentsInput(BaseModel):
    course_id: str
    speech_text_ref: dict[str, Any]
    track_id: str
    segment_ids: list[str] = Field(min_length=1)
    voice_id: str = "default"
    seed: int = 1234
    max_attempts: int = Field(default=2, ge=1, le=5)


class SegmentStatus(BaseModel):
    segment_id: str
    status: Literal["ok", "failed_qa"]
    audio_ref: dict[str, Any]
    attempts: int
    seed: int
    violations: list[str] = []
    duration_s: float
    chars: int
    cache_hit: bool


class SegmentBatchResult(BaseModel):
    track_id: str
    segments: list[SegmentStatus]
    provider: dict[str, Any]


class AssembleSpeechTrackInput(BaseModel):
    course_id: str
    speech_text_ref: dict[str, Any]
    track: TrackPlan
    segments: list[SegmentStatus]
    loudness_profile: str = DEFAULT_LOUDNESS_PROFILE
    require_intelligibility: bool = True


class SpeechTrackResult(BaseModel):
    track_id: str
    artifact: dict[str, Any] | None
    status: str | None  # accepted | rejected | candidate | None (no registry)
    gates: dict[str, bool | None]
    failed_segments: list[str] = []
    metrics: dict[str, Any] = {}


# ------------------------------------------------------------------ TTS provider
class SegmentSynthesizer(Protocol):
    def identity(self) -> dict[str, Any]:
        """{"provider", "model_id", "revision", "sample_rate", "seed_honored"} read from the service."""

    def synthesize(self, text: str, *, seed: int) -> bytes:
        """WAV bytes for one segment of speech-ready text."""


class FasihSynthesizer:
    """Fasih TTS (``docker/fasih/server.py``) one segment per request: POST /v1/tts -> WAV.

    The provider identity comes from the service's ``/info`` (model id, sample rate), never from
    the worker's environment (audit 07 H2). Fasih v1 samples without a seed: ``seed_honored`` is
    False and re-rendering a failed segment relies on that sampling (recorded per attempt)."""

    def __init__(self, base_url: str, *, revision: str, timeout_s: float = 1800.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.revision = revision
        self.timeout_s = timeout_s
        self._identity: dict[str, Any] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "FasihSynthesizer":
        return cls(os.getenv("B2C_FASIH_TTS_URL", "http://fasih-tts:3006"),
                   revision=os.getenv("B2C_FASIH_MODEL_REVISION", "unpinned"))

    def identity(self) -> dict[str, Any]:
        with self._lock:
            if self._identity is None:
                with urllib.request.urlopen(self.base_url + "/info", timeout=30) as response:
                    info = json.loads(response.read())
                self._identity = {"provider": "fasih_v1", "model_id": str(info.get("model") or "fasih"),
                                  "revision": self.revision, "sample_rate": int(info.get("sample_rate") or 24000),
                                  "seed_honored": False}
            return dict(self._identity)

    def synthesize(self, text: str, *, seed: int) -> bytes:
        body = json.dumps({"text": text, "auto_diacritize": True}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/v1/tts", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            if response.headers.get_content_type() != "audio/wav":
                raise ValueError("TTS provider did not return WAV")
            return response.read()


# ------------------------------------------------------------------ gate policies
def text_policy() -> GatePolicy:
    from b2c_speech.text import FRONTEND_VERSION, SPEECH_TEXT_GATE
    return GatePolicy("speech.text-frontend", "deterministic", {**SPEECH_TEXT_GATE, "frontend": FRONTEND_VERSION})


def reading_policy(profile: str) -> GatePolicy:
    from b2c_speech.text import READING_QUALITY_GATE, engine_profile
    return GatePolicy("speech.reading-quality", "deterministic",
                      {**READING_QUALITY_GATE, "profile": engine_profile(profile).definition()})


def segment_policy(sample_rate: int) -> GatePolicy:
    from b2c_speech.audio import SegmentLimits
    return GatePolicy("speech.segment-qa", "deterministic",
                      {"limits": SegmentLimits(sample_rate=sample_rate).definition(), "max_failed_segments": 0})


def loudness_policy(profile_name: str) -> GatePolicy:
    from b2c_speech.audio import profile_by_name
    return GatePolicy("speech.track-loudness", "deterministic", profile_by_name(profile_name).definition())


DURATION_POLICY = GatePolicy("speech.duration-outliers", "deterministic",
                             {"tolerance": 0.35, "min_segments": 5, "max_outliers": 0})
ASR_POLICY = GatePolicy("speech.asr-intelligibility", "semantic", ASR_POLICY_DEFINITION)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scoped(raw: dict[str, Any] | None, *, bucket: str, course_id: str, stage: str, label: str) -> ArtifactRef:
    try:
        ref = ArtifactRef.model_validate(raw or {})
    except Exception as exc:
        raise _fail(f"{label} is not an artifact reference", "InvalidSpeechInput") from exc
    if ref.stage != stage or not ref.uri.startswith(f"s3://{bucket}/{course_id}/"):
        raise _fail(f"{label} must be a {stage} artifact of this course", "InvalidSpeechInput")
    return ref


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


# ------------------------------------------------------------------ activities
class SpeechV2Activities:
    def __init__(self, *, artifact_store: Any, result_store: Any, promotion: PromotionService | None,
                 synthesizer: SegmentSynthesizer | None = None, intelligibility: Any = None,
                 clock: Any = _now) -> None:
        from b2c_speech.audio import NoIntelligibilityCheck
        self.store = artifact_store
        self.result_store = result_store
        self.promotion = promotion
        self.synthesizer = synthesizer
        self.intelligibility = intelligibility or NoIntelligibilityCheck()
        self.clock = clock
        self._docs: OrderedDict[str, SpeechTextDocument] = OrderedDict()
        self._lock = threading.Lock()

    # .............................................................. helpers
    def _doc(self, ref: ArtifactRef) -> SpeechTextDocument:
        with self._lock:
            if ref.key in self._docs:
                return self._docs[ref.key]
        doc = SpeechTextDocument.model_validate_json(self.store.read(ref, "speech-text.json"))
        with self._lock:
            self._docs[ref.key] = doc
            while len(self._docs) > 4:
                self._docs.popitem(last=False)
        return doc

    def _gate(self, course_id: str, artifact: ArtifactRef, policy: GatePolicy, passed: bool,
              metrics: dict[str, Any], evidence: str) -> None:
        assert self.promotion is not None
        self.promotion.attach_gate(course_id, GateResult(
            stage=artifact.stage, artifact=artifact, gate_id=policy.gate_id, gate_version=policy.version,
            kind=policy.kind, passed=passed, metrics=metrics, evidence_ref=artifact.uri + evidence,
            decider="system", decided_by=f"policy:{policy.gate_id}", decided_at=self.clock()))

    def _lexicon(self, inp: GenerateSpeechV2Input, book_id: str) -> tuple[Any, ArtifactRef | None]:
        from b2c_speech.text import PronunciationLexicon
        if inp.termbase_ref is None:
            return PronunciationLexicon.from_mappings(overrides=inp.lexicon_overrides), None
        from b2c_translation.termbase import BookTermbase, TermbaseError
        ref = _scoped(inp.termbase_ref, bucket=self.store.bucket, course_id=inp.course_id,
                      stage=TERMBASE_APPROVED_STAGE, label="termbase_ref")
        try:
            termbase = BookTermbase.from_json(self.store.read(ref, "termbase.json"))
        except TermbaseError as exc:
            raise _fail(f"approved termbase artifact is invalid: {exc}", "InvalidSpeechInput") from exc
        if termbase.book_id != book_id:
            raise _fail("the approved termbase belongs to another book", "InvalidSpeechInput")
        return PronunciationLexicon.from_mappings(terms=termbase.locked_terms(), entities=termbase.entity_table(),
                                                  overrides=inp.lexicon_overrides), ref

    # .............................................................. prepare
    @activity.defn(name="speech_v2_prepare")
    def prepare(self, inp: GenerateSpeechV2Input) -> SpeechPrepared:
        from b2c_speech.audio import profile_by_name
        from b2c_speech.text import (FRONTEND_VERSION, NotSpeechReady, build_speech_text, engine_profile,
                                     evaluate_reading_quality, evaluate_speech_text)

        from .translate_book import UnitsCache, translation_results_digest
        try:
            profile_by_name(inp.loudness_profile)
        except ValueError as exc:
            raise _fail(str(exc), "UnknownLoudnessProfile") from exc
        try:
            engine_profile(inp.engine_profile)
        except ValueError as exc:
            raise _fail(str(exc), "UnknownEngineProfile") from exc
        assembly = _scoped(inp.assembly_ref, bucket=self.store.bucket, course_id=inp.course_id,
                           stage=ASSEMBLY_STAGE, label="assembly_ref")
        if self.promotion is None:
            raise _fail("no stage-output registry is configured: acceptance cannot be checked",
                        "NotAcceptedTranslation")
        notes_ref = None
        if inp.notes_ref is not None:
            notes_ref = _scoped(inp.notes_ref, bucket=self.store.bucket, course_id=inp.course_id,
                                stage=NOTES_STAGE, label="notes_ref")
        try:
            # D-041: the accepted notes (coverage gate) replace the strict assembly acceptance
            self.promotion.require_accepted(inp.course_id, notes_ref or assembly)
        except NotAccepted as exc:
            what = "translator notes are" if notes_ref else "translation assembly is"
            raise _fail(f"the {what} not accepted: {exc}", "NotAcceptedTranslation") from exc
        with activity_heartbeats("speech_v2_prepare"):
            document = json.loads(self.store.read(assembly, "translation.json"))
            units_ref = _scoped(document.get("units_ref"), bucket=self.store.bucket, course_id=inp.course_id,
                                stage=UNITS_STAGE, label="assembly units_ref")
            units = UnitsCache(size=1).get(self.store, units_ref)
            known = {u.unit_id for u in units}
            stored = [r for r in self.result_store.fetch_run(document["run_id"]) if r.unit_id in known]
            translations = {r.unit_id: r.translation for r in stored}
            digest = translation_results_digest(translations)
            if digest != document.get("results_digest"):
                raise _fail("unit results changed after this assembly was built; re-assemble and re-accept it",
                            "TranslationChanged")
            notes = None
            if notes_ref is not None:
                notes_doc = TranslatorNotesDocument.model_validate_json(
                    self.store.read(notes_ref, "translator-notes.json"))
                if notes_doc.run_id != document["run_id"] or notes_doc.units_ref is None \
                        or notes_doc.units_ref.sha256 != units_ref.sha256:
                    raise _fail("the translator notes belong to another run or units artifact", "InvalidSpeechInput")
                if notes_doc.results_digest != digest:
                    raise _fail("unit results changed after the translator notes were built; rebuild them",
                                "TranslationChanged")
                notes = {n.unit_id: n for n in notes_doc.notes}
            book_id = units[0].book_id if units else ""
            figures, figures_ref = None, None
            if inp.figures_ref is not None:
                figures_ref = _scoped(inp.figures_ref, bucket=self.store.bucket, course_id=inp.course_id,
                                      stage=FIGURES_STAGE, label="figures_ref")
                try:
                    self.promotion.require_accepted(inp.course_id, figures_ref)
                except NotAccepted as exc:
                    raise _fail(f"the figure descriptions are not accepted: {exc}", "NotAcceptedTranslation") from exc
                figures = FigureDescriptionsDocument.model_validate_json(self.store.read(figures_ref, "figures.json"))
                if figures.book_id != book_id:
                    raise _fail("the figure descriptions belong to another book", "InvalidSpeechInput")
            lexicon, termbase_ref = self._lexicon(inp, book_id)
            config = {"frontend": FRONTEND_VERSION, "lexicon_digest": lexicon.digest,
                      "limits": inp.limits.model_dump(mode="json"), "language": inp.language,
                      "engine_profile": engine_profile(inp.engine_profile).definition()}
            if notes_ref is not None:  # the strict path keeps its artifact keys
                config["acceptance_path"] = "translator-notes"
            if figures_ref is not None:  # without figures the artifact keys are unchanged
                config["figures"] = "read in place (D-044)"
            inputs = ([assembly] + ([notes_ref] if notes_ref else []) + ([termbase_ref] if termbase_ref else [])
                      + ([figures_ref] if figures_ref else []))
            key = stage_key(SPEECH_TEXT_STAGE, FRONTEND_VERSION, [r.sha256 for r in inputs], config)

            def compute() -> dict[str, bytes]:
                try:
                    doc = build_speech_text(units, translations, book_id=book_id, source=assembly, lexicon=lexicon,
                                            language=inp.language, profile=inp.engine_profile, notes=notes,
                                            notes_ref=notes_ref, figures=figures, figures_ref=figures_ref)
                except NotSpeechReady as exc:
                    raise _fail(str(exc), "NotSpeechReady") from exc
                plan = plan_tracks(doc, inp.limits)
                return {"speech-text.json": doc.model_dump_json(indent=1).encode("utf-8"),
                        "speech-plan.json": plan.model_dump_json(indent=1).encode("utf-8")}

            ref, hit = self.store.get_or_compute(inp.course_id, SPEECH_TEXT_STAGE, key, compute)
            doc = self._doc(ref)
            plan = SpeechPlan.model_validate_json(self.store.read(ref, "speech-plan.json"))
            text_passed, metrics = evaluate_speech_text(doc)
            reading_passed, reading = evaluate_reading_quality(doc, inp.engine_profile)
            policy, reading_gate = text_policy(), reading_policy(inp.engine_profile)
            record = self.promotion.record_candidate(inp.course_id, ref,
                                                     required_gates=[policy.gate_id, reading_gate.gate_id],
                                                     inputs=inputs, config=config,
                                                     identities=[ProducerIdentity(role="tool", model_id="b2c-speech-tn",
                                                                                  revision=FRONTEND_VERSION)])
            if record.status == "candidate":
                self._gate(inp.course_id, ref, policy, text_passed, metrics, "speech-text.json")
                self._gate(inp.course_id, ref, reading_gate, reading_passed, reading, "speech-text.json")
                record = self.promotion.settle(inp.course_id, ref)
            passed = text_passed and reading_passed
            metrics = {**metrics, "reading_quality": reading}
        return SpeechPrepared(speech_text_ref=ref.model_dump(mode="json"), status=record.status, gate_passed=passed,
                              gate_metrics=metrics, plan=plan, cache_hit=hit)

    # .............................................................. synthesize
    @activity.defn(name="speech_v2_synthesize_segments")
    def synthesize(self, inp: SynthesizeSegmentsInput) -> SegmentBatchResult:
        from b2c_speech.audio import SegmentLimits, check_segment, read_wav
        if self.synthesizer is None:
            raise ApplicationError("no TTS synthesizer is configured on this worker", type="SynthesizerMissing")
        ref = _scoped(inp.speech_text_ref, bucket=self.store.bucket, course_id=inp.course_id,
                      stage=SPEECH_TEXT_STAGE, label="speech_text_ref")
        if self.promotion is not None:
            try:
                self.promotion.require_accepted(inp.course_id, ref)
            except NotAccepted as exc:
                raise _fail(f"speech text is not accepted: {exc}", "NotAcceptedTranslation") from exc
        doc = self._doc(ref)
        by_id = {s.segment_id: s for s in doc.segments}
        unknown = [sid for sid in inp.segment_ids if sid not in by_id]
        if unknown:
            raise _fail(f"segments not in the speech text: {unknown[:3]}", "InvalidSpeechInput")
        identity = self.synthesizer.identity()
        limits = SegmentLimits(sample_rate=int(identity["sample_rate"]))
        out: list[SegmentStatus] = []
        with activity_heartbeats("speech_v2_synthesize_segments"):
            for sid in inp.segment_ids:
                segment = by_id[sid]
                for attempt in range(inp.max_attempts):
                    voice = VoiceConfig(provider=str(identity["provider"]), voice_id=inp.voice_id,
                                        model_revision=f"{identity['model_id']}@{identity['revision']}",
                                        seed=inp.seed + attempt, sample_rate=limits.sample_rate)

                    def render(segment=segment, voice=voice) -> dict[str, bytes]:
                        wav = self.synthesizer.synthesize(segment.spoken_text, seed=voice.seed)
                        qa = check_segment(read_wav(wav), segment.spoken_text, limits)
                        record = {"passed": qa.passed, "violations": list(qa.violations), "metrics": qa.metrics,
                                  "limits": limits.definition(), "voice": voice.model_dump(mode="json"),
                                  "provider": identity, "spoken_text": segment.spoken_text}
                        return {"segment.wav": wav, "qa.json": json.dumps(record, ensure_ascii=False).encode("utf-8")}

                    audio_ref, hit = self.store.get_or_compute(inp.course_id, SEGMENT_AUDIO_STAGE,
                                                               segment_audio_key(segment, voice), render)
                    qa = json.loads(self.store.read(audio_ref, "qa.json"))
                    if qa["passed"] or attempt == inp.max_attempts - 1:
                        break
                out.append(SegmentStatus(
                    segment_id=sid, status="ok" if qa["passed"] else "failed_qa",
                    audio_ref=audio_ref.model_dump(mode="json"), attempts=attempt + 1, seed=voice.seed,
                    violations=qa["violations"], duration_s=float(qa["metrics"]["duration_s"]),
                    chars=int(qa["metrics"]["chars"]), cache_hit=hit))
        return SegmentBatchResult(track_id=inp.track_id, segments=out, provider=identity)

    # .............................................................. assemble
    @activity.defn(name="speech_v2_assemble_track")
    def assemble(self, inp: AssembleSpeechTrackInput) -> SpeechTrackResult:
        import numpy as np
        from b2c_speech.audio import (PCM, check_track, duration_outliers, evaluate_intelligibility,
                                      normalize_loudness, profile_by_name, read_wav, write_wav)
        from b2c_speech.audio.trim import assemble_with_pauses, trim_settings
        ref = _scoped(inp.speech_text_ref, bucket=self.store.bucket, course_id=inp.course_id,
                      stage=SPEECH_TEXT_STAGE, label="speech_text_ref")
        doc = self._doc(ref)
        by_id = {s.segment_id: s for s in doc.segments}
        statuses = {s.segment_id: s for s in inp.segments}
        missing = [sid for sid in inp.track.segment_ids if sid not in statuses]
        if missing:
            return SpeechTrackResult(track_id=inp.track.track_id, artifact=None, status="incomplete",
                                     gates={}, failed_segments=missing)
        profile = profile_by_name(inp.loudness_profile)
        with activity_heartbeats("speech_v2_assemble_track"):
            pieces, rate = [], None
            segment_audio: dict[str, Any] = {}
            for sid in inp.track.segment_ids:
                audio_ref = ArtifactRef.model_validate(statuses[sid].audio_ref)
                pcm = read_wav(self.store.read(audio_ref, "segment.wav"))
                if rate is None:
                    rate = pcm.sample_rate
                if pcm.sample_rate != rate:
                    raise _fail("segments of one track have different sample rates", "InvalidSpeechInput")
                segment_audio[sid] = pcm
                pieces.append(pcm.mono())
            # Edge silence of every segment is trimmed to a short guard, so the silence between two
            # segments is the planned pause (boundary_after / break_after), not pause + TTS edges.
            pauses = [by_id[sid].pause_after_ms for sid in inp.track.segment_ids]
            samples, cuts = assemble_with_pauses(pieces, pauses, int(rate))
            del pieces
            timings = []
            for sid, cut in zip(inp.track.segment_ids, cuts):
                status = statuses[sid]
                timings.append({"segment_id": sid, "unit_ids": by_id[sid].unit_ids, "start_s": round(cut["start_s"], 3),
                                "end_s": round(cut["end_s"], 3), "status": status.status, "attempts": status.attempts,
                                "audio_sha256": status.audio_ref["sha256"], "pause_after_ms": by_id[sid].pause_after_ms,
                                **{k: round(cut[k], 1) for k in ("pause_inserted_ms", "removed_leading_ms",
                                                                  "removed_trailing_ms", "kept_leading_ms",
                                                                  "kept_trailing_ms")}})
            track = PCM(samples=samples, sample_rate=int(rate))
            clock = track.duration_s
            normalized, gain_db = normalize_loudness(track, profile, in_place=True)  # bounded memory
            loud = check_track(normalized, profile)
            outliers = duration_outliers({s.segment_id: (s.duration_s, s.chars) for s in inp.segments
                                          if s.segment_id in by_id}, tolerance=DURATION_POLICY.definition["tolerance"],
                                         min_segments=DURATION_POLICY.definition["min_segments"])
            failed = sorted(sid for sid in inp.track.segment_ids if statuses[sid].status != "ok")
            asr: dict[str, Any] | None = None
            results = [evaluate_intelligibility(self.intelligibility, segment_audio[sid], by_id[sid].spoken_text,
                                                max_cer=ASR_POLICY_DEFINITION["max_p95_cer"])
                       for sid in inp.track.segment_ids]
            if all(r is not None for r in results) and results:
                cers = [float(r.metrics["cer"]) for r in results]
                asr = {"median_cer": float(np.median(cers)), "p95_cer": _p95(cers), "segments": len(cers),
                       "worst": sorted(zip(cers, inp.track.segment_ids), reverse=True)[:5],
                       "asr_model": results[0].metrics.get("asr_model")}
            files = {
                "track.wav": write_wav(normalized),
                "segments.json": json.dumps(timings, ensure_ascii=False, indent=1).encode("utf-8"),
                "track.json": json.dumps({
                    "track_id": inp.track.track_id, "title": inp.track.title, "language": doc.language,
                    "frontend_version": doc.frontend_version, "lexicon_digest": doc.lexicon_digest,
                    "loudness_profile": profile.name, "gain_db": round(gain_db, 3), "sample_rate": rate,
                    "edge_trim": trim_settings(),
                    "speech_text_sha256": ref.sha256}, ensure_ascii=False).encode("utf-8"),
                "qa.json": json.dumps({"track": loud.metrics, "violations": list(loud.violations),
                                       "duration_outliers": outliers, "failed_segments": failed, "asr": asr},
                                      ensure_ascii=False, default=str).encode("utf-8"),
            }
            key = stage_key(SPEECH_TRACK_STAGE, SPEECH_TRACK_VERSION,
                            [ref.sha256] + [statuses[s].audio_ref["sha256"] for s in inp.track.segment_ids],
                            {"track_id": inp.track.track_id, "profile": profile.name})
            out_ref, _ = self.store.get_or_compute(inp.course_id, SPEECH_TRACK_STAGE, key, lambda: files)
        gates: dict[str, bool | None] = {
            "speech.segment-qa": not failed, "speech.track-loudness": loud.passed,
            "speech.duration-outliers": not outliers,
            ASR_POLICY.gate_id: None if asr is None else (
                asr["median_cer"] <= ASR_POLICY_DEFINITION["max_median_cer"]
                and asr["p95_cer"] <= ASR_POLICY_DEFINITION["max_p95_cer"])}
        metrics = {"duration_s": round(clock, 3), "segments": len(inp.track.segment_ids), "gain_db": round(gain_db, 3),
                   "integrated_lufs": loud.metrics.get("integrated_lufs"),
                   "true_peak_dbtp": loud.metrics.get("true_peak_dbtp"), "loudness_violations": list(loud.violations),
                   "duration_outliers": outliers, "asr": asr}
        status = None
        if self.promotion is not None:
            policies = [segment_policy(int(rate)), loudness_policy(profile.name), DURATION_POLICY]
            required = [p.gate_id for p in policies] + ([ASR_POLICY.gate_id] if inp.require_intelligibility else [])
            provider = json.loads(self.store.read(ArtifactRef.model_validate(
                statuses[inp.track.segment_ids[0]].audio_ref), "qa.json"))["provider"]
            identities = [ProducerIdentity(role="tts", provider=str(provider["provider"]),
                                           model_id=str(provider["model_id"]), revision=str(provider["revision"]))]
            if asr is not None:
                asr_identity = self.intelligibility.identity
                identities.append(ProducerIdentity(role="asr", model_id=str(asr_identity.get("model_id")),
                                                   revision=asr_identity.get("revision")))
            record = self.promotion.record_candidate(
                inp.course_id, out_ref, required_gates=required, inputs=[ref], identities=identities,
                config={"track_id": inp.track.track_id, "profile": profile.name})
            if record.status == "candidate":
                evidence = {"speech.segment-qa": {"failed_segments": failed, "segments": len(inp.track.segment_ids)},
                            "speech.track-loudness": {**loud.metrics, "violations": list(loud.violations)},
                            "speech.duration-outliers": {"outliers": outliers}}
                for policy in policies:
                    self._gate(inp.course_id, out_ref, policy, bool(gates[policy.gate_id]), evidence[policy.gate_id],
                               "qa.json")
                if asr is not None:
                    self._gate(inp.course_id, out_ref, ASR_POLICY, bool(gates[ASR_POLICY.gate_id]), asr, "qa.json")
                record = self.promotion.settle(inp.course_id, out_ref)
            status = record.status
        return SpeechTrackResult(track_id=inp.track.track_id, artifact=out_ref.model_dump(mode="json"), status=status,
                                 gates=gates, failed_segments=failed, metrics=metrics)


def speech_v2_activities(acts: SpeechV2Activities) -> list[Any]:
    return [acts.prepare, acts.synthesize, acts.assemble]


# ------------------------------------------------------------------ workflow
def _retry(maximum_attempts: int) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=30), backoff_coefficient=2.0,
                       maximum_interval=timedelta(minutes=10), maximum_attempts=maximum_attempts,
                       non_retryable_error_types=NON_RETRYABLE)


PREPARE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(minutes=30),
                                       heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))
# A batch is bounded (<= 8 segments, <= 2400 characters: ~5 min of audio, ~15 min at RTF 3).
SYNTHESIZE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(hours=2),
                                          heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(5))
ASSEMBLE_OPTIONS: dict[str, Any] = dict(start_to_close_timeout=timedelta(minutes=30),
                                        heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT, retry_policy=_retry(3))


@workflow.defn(name="GenerateSpeechV2Workflow")
class GenerateSpeechV2Workflow:
    def __init__(self) -> None:
        self._state: dict[str, Any] = {"phase": "starting", "segments": {}, "tracks": {}}

    @workflow.query(name="speech_v2_state")
    def state(self) -> dict[str, Any]:
        return self._state

    @workflow.run
    async def run(self, inp: GenerateSpeechV2Input) -> dict[str, Any]:
        queue = inp.activity_task_queue
        self._state["phase"] = "preparing"
        prepared: SpeechPrepared = await workflow.execute_activity(
            "speech_v2_prepare", inp, result_type=SpeechPrepared, task_queue=queue, **PREPARE_OPTIONS)
        self._state.update(speech_text_ref=prepared.speech_text_ref, speech_text_status=prepared.status,
                           plan_tracks=len(prepared.plan.tracks), plan_segments=prepared.plan.total_segments)
        if prepared.status != "accepted":
            self._state["phase"] = "stopped"
            return {"status": f"speech_text_{prepared.status}", "speech_text_ref": prepared.speech_text_ref,
                    "gate_metrics": prepared.gate_metrics, "tracks": []}
        tracks = [t for t in prepared.plan.tracks if not inp.track_ids or t.track_id in inp.track_ids]
        results: list[dict[str, Any]] = []
        for track in tracks:
            self._state["phase"] = f"synthesizing {track.track_id}"
            statuses: list[SegmentStatus] = []
            for batch in track.batches:
                out: SegmentBatchResult = await workflow.execute_activity(
                    "speech_v2_synthesize_segments",
                    SynthesizeSegmentsInput(course_id=inp.course_id, speech_text_ref=prepared.speech_text_ref,
                                            track_id=track.track_id, segment_ids=batch, voice_id=inp.voice_id,
                                            seed=inp.seed, max_attempts=inp.max_attempts),
                    result_type=SegmentBatchResult, task_queue=queue, **SYNTHESIZE_OPTIONS)
                statuses += out.segments
                self._state["segments"].update({s.segment_id: s.status for s in out.segments})
            self._state["phase"] = f"assembling {track.track_id}"
            result: SpeechTrackResult = await workflow.execute_activity(
                "speech_v2_assemble_track",
                AssembleSpeechTrackInput(course_id=inp.course_id, speech_text_ref=prepared.speech_text_ref,
                                         track=track, segments=statuses, loudness_profile=inp.loudness_profile,
                                         require_intelligibility=inp.require_intelligibility),
                result_type=SpeechTrackResult, task_queue=queue, **ASSEMBLE_OPTIONS)
            self._state["tracks"][track.track_id] = result.status
            results.append(result.model_dump(mode="json"))
        self._state["phase"] = "done"
        accepted = sum(1 for r in results if r["status"] == "accepted")
        return {"status": "completed", "speech_text_ref": prepared.speech_text_ref, "tracks": results,
                "accepted_tracks": accepted, "segment_status": dict(self._state["segments"])}


__all__ = ["ASR_POLICY", "AssembleSpeechTrackInput", "DURATION_POLICY", "FasihSynthesizer", "GenerateSpeechV2Input",
           "GenerateSpeechV2Workflow", "SPEECH_TEXT_STAGE", "SPEECH_TRACK_STAGE", "SegmentBatchResult",
           "SegmentStatus", "SegmentSynthesizer", "SpeechPrepared", "SpeechTrackResult", "SpeechV2Activities",
           "SynthesizeSegmentsInput", "loudness_policy", "segment_policy", "speech_v2_activities", "text_policy"]
