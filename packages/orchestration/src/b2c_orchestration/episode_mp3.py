"""Episode MP3 after the audio (D-042 open item, D-044 §2). New names; nothing starts it automatically.

    speech_v2_encode_episode   the episode's ACCEPTED ``speech-track`` artifacts, in order -> each
                               track.wav streamed from the store to a temporary file -> streamed
                               normalization + MP3 encoding (``b2c_speech.audio.stream``) -> stage
                               ``episode-mp3``: episode.mp3 (ID3v2.3 tagged, 00/01/02 naming in
                               episode.json) + episode.json (report) -> candidate with gates
                               ``speech.episode-loudness`` (the owner's current default
                               ``app-speech-16``: -16 LUFS +-1, true peak <= -1 dBTP) and
                               ``speech.episode-format`` (MPEG-1 Layer III, mono, CBR, ID3v2.3)
    EncodeEpisodeMp3Workflow   one activity per requested episode, in order; started only on request
                               (``GenerateSpeechV2Workflow`` does not chain into it).

Memory: one block of audio (``block_s``) and the resampler/meters state, not the episode (a one-hour
episode measured 2.75 GiB through ``delivery.encode_episode``; see eval/reports/figure-descriptions.md).
The MP3 is uploaded with ``put_stream`` from disk, hashed while it is written.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from b2c_contracts import ArtifactRef, GateResult, ProducerIdentity, stage_key

    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from .promotion import GatePolicy, NotAccepted, PromotionService

EPISODE_MP3_STAGE = "episode-mp3"
EPISODE_MP3_VERSION = "1"
TRACK_STAGE = "speech-track"
ACTIVITY = "speech_v2_encode_episode"
WORKFLOW = "EncodeEpisodeMp3Workflow"
DEFAULT_LOUDNESS_PROFILE = "app-speech-16"  # the owner's current default (Q2 still open: -16 vs -19)
NON_RETRYABLE = ["InvalidSpeechInput", "NotAcceptedTrack", "UnknownLoudnessProfile", "EpisodeLoudnessError"]
FORMAT_POLICY = GatePolicy("speech.episode-format", "deterministic",
                           {"mpeg": "MPEG-1 Layer III", "channels": "mono", "bitrate_mode": "CBR",
                            "allowed_kbps": [64, 96], "id3": "2.3.0", "text_encoding": "UTF-16 (BOM)",
                            "trailing_bytes": 0})


def loudness_policy(profile_name: str) -> GatePolicy:
    from b2c_speech.audio import profile_by_name
    return GatePolicy("speech.episode-loudness", "deterministic",
                      {**profile_by_name(profile_name).definition(), "measured": "streamed, pre-encode, after gain"})


def _fail(message: str, error_type: str) -> ApplicationError:
    return ApplicationError(message[:400], type=error_type, non_retryable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EncodeEpisodeInput(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    track_refs: list[dict[str, Any]] = Field(min_length=1)  # accepted speech-track artifacts, in order
    episode: int = Field(ge=0)            # 0 = course introduction (D-037/D-038)
    last_episode: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=300)
    book_title: str = Field(min_length=1, max_length=300)
    artist: str | None = None
    loudness_profile: str = DEFAULT_LOUDNESS_PROFILE
    bitrate_kbps: int = 96
    ascii_only: bool = False
    gap_ms: int = Field(default=1200, ge=0, le=1500)  # silence between two tracks (section pause)
    require_accepted_tracks: bool = True


class EpisodeMp3Result(BaseModel):
    episode: int
    artifact: dict[str, Any]
    filename: str
    status: str | None
    gates: dict[str, bool]
    metrics: dict[str, Any]
    cache_hit: bool


class EncodeEpisodesInput(BaseModel):
    episodes: list[EncodeEpisodeInput] = Field(min_length=1)
    activity_task_queue: str = "audio"


class EpisodeMp3Activities:
    def __init__(self, *, artifact_store: Any, promotion: PromotionService | None, tmp_dir: str | None = None,
                 block_s: float = 10.0, clock: Any = _now) -> None:
        self.store = artifact_store
        self.promotion = promotion
        self.tmp_dir = tmp_dir or os.getenv("B2C_AUDIO_TMPDIR") or None
        self.block_s = block_s
        self.clock = clock

    def _track(self, inp: EncodeEpisodeInput, raw: dict[str, Any]) -> ArtifactRef:
        try:
            ref = ArtifactRef.model_validate(raw)
        except Exception as exc:
            raise _fail("a track reference is not an artifact reference", "InvalidSpeechInput") from exc
        if ref.stage != TRACK_STAGE or not ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
            raise _fail("tracks must be speech-track artifacts of this course", "InvalidSpeechInput")
        if inp.require_accepted_tracks:
            if self.promotion is None:
                raise _fail("no stage-output registry: track acceptance cannot be checked", "NotAcceptedTrack")
            try:
                self.promotion.require_accepted(inp.course_id, ref)
            except NotAccepted as exc:
                raise _fail(f"track is not accepted: {exc}", "NotAcceptedTrack") from exc
        return ref

    @activity.defn(name=ACTIVITY)
    def encode(self, inp: EncodeEpisodeInput) -> EpisodeMp3Result:
        from b2c_speech.audio import profile_by_name
        from b2c_speech.audio.mp3 import Mp3Settings
        from b2c_speech.audio.stream import encode_episode_stream
        from b2c_speech.delivery import EpisodeLoudnessError
        try:
            profile = profile_by_name(inp.loudness_profile)
        except ValueError as exc:
            raise _fail(str(exc), "UnknownLoudnessProfile") from exc
        try:
            settings = Mp3Settings(bitrate_kbps=inp.bitrate_kbps)
        except ValueError as exc:
            raise _fail(str(exc), "InvalidSpeechInput") from exc
        if inp.episode > inp.last_episode:
            raise _fail("episode is after the last episode", "InvalidSpeechInput")
        tracks = [self._track(inp, raw) for raw in inp.track_refs]
        config = {"episode": inp.episode, "last_episode": inp.last_episode, "title": inp.title,
                  "book_title": inp.book_title, "artist": inp.artist, "profile": profile.name,
                  "bitrate_kbps": settings.bitrate_kbps, "ascii_only": inp.ascii_only, "gap_ms": inp.gap_ms}
        key = stage_key(EPISODE_MP3_STAGE, EPISODE_MP3_VERSION, [t.sha256 for t in tracks], config)
        existing = self.store.exists(inp.course_id, EPISODE_MP3_STAGE, key)
        hit = existing is not None
        if existing is not None:
            ref = existing
            report = json.loads(self.store.read(ref, "episode.json"))
        else:
            work = tempfile.mkdtemp(prefix="b2c-episode-", dir=self.tmp_dir)
            try:
                with activity_heartbeats(ACTIVITY):
                    paths = []
                    for i, t in enumerate(tracks):
                        path = os.path.join(work, f"track-{i:03d}.wav")
                        self.store.download_to_path(t, "track.wav", path)
                        paths.append(path)
                    out = os.path.join(work, "episode.mp3")
                    try:
                        result = encode_episode_stream(
                            paths, out, number=inp.episode, title=inp.title, book_title=inp.book_title,
                            last_episode=inp.last_episode, artist=inp.artist, ascii_only=inp.ascii_only,
                            profile=profile, settings=settings, strict=False, gap_ms=inp.gap_ms,
                            block_s=self.block_s)
                    except EpisodeLoudnessError as exc:  # strict=False: only a malformed input gets here
                        raise _fail(str(exc), "EpisodeLoudnessError") from exc
                    for p in paths:
                        os.remove(p)
                    report = {**result.report, "filename": result.filename,
                              "tracks_sha256": [t.sha256 for t in tracks]}
                    meta = json.dumps(report, ensure_ascii=False, indent=1, default=str).encode("utf-8")
                    meta_path = os.path.join(work, "episode.json")
                    with open(meta_path, "wb") as f:
                        f.write(meta)
                    with open(out, "rb") as mp3, open(meta_path, "rb") as js:
                        ref = self.store.put_stream(inp.course_id, EPISODE_MP3_STAGE, key,
                                                    {"episode.mp3": mp3, "episode.json": js},
                                                    {"episode.mp3": result.sha256,
                                                     "episode.json": hashlib.sha256(meta).hexdigest()})
            finally:
                shutil.rmtree(work, ignore_errors=True)
        gates = {"speech.episode-loudness": bool(report["passed"]),
                 "speech.episode-format": bool(report["bitrate_mode"] == "CBR" and report["channel_modes"] == ["mono"]
                                               and report.get("mpeg_versions") == ["MPEG-1"]
                                               and report["id3"] == "2.3.0" and report.get("trailing_bytes") == 0)}
        status = None
        if self.promotion is not None:
            policies = [loudness_policy(profile.name), FORMAT_POLICY]
            record = self.promotion.record_candidate(
                inp.course_id, ref, required_gates=[p.gate_id for p in policies], inputs=tracks,
                identities=[ProducerIdentity(role="encoder", model_id=str(report["encoder"]))], config=config)
            if record.status == "candidate":
                evidence = {"speech.episode-loudness": {k: report.get(k) for k in (
                    "integrated_lufs", "true_peak_dbtp", "gain_db", "violations", "duration_s")},
                    "speech.episode-format": {k: report.get(k) for k in (
                        "bitrate_kbps", "bitrate_mode", "channel_modes", "mpeg_versions", "frames", "id3",
                        "trailing_bytes")}}
                for policy in policies:
                    self.promotion.attach_gate(inp.course_id, GateResult(
                        stage=ref.stage, artifact=ref, gate_id=policy.gate_id, gate_version=policy.version,
                        kind=policy.kind, passed=gates[policy.gate_id], metrics=evidence[policy.gate_id],
                        evidence_ref=ref.uri + "episode.json", decider="system",
                        decided_by=f"policy:{policy.gate_id}", decided_at=self.clock()))
                record = self.promotion.settle(inp.course_id, ref)
            status = record.status
        metrics = {k: report.get(k) for k in ("integrated_lufs", "true_peak_dbtp", "gain_db", "duration_s", "bytes",
                                              "frames", "tracks", "streamed", "violations")}
        return EpisodeMp3Result(episode=inp.episode, artifact=ref.model_dump(mode="json"),
                                filename=str(report["filename"]), status=status, gates=gates, metrics=metrics,
                                cache_hit=hit)


def episode_mp3_activities(acts: EpisodeMp3Activities) -> list[Any]:
    return [acts.encode]


ENCODE_OPTIONS: dict[str, Any] = dict(
    start_to_close_timeout=timedelta(hours=1), heartbeat_timeout=LONG_ACTIVITY_HEARTBEAT_TIMEOUT,
    retry_policy=RetryPolicy(initial_interval=timedelta(seconds=30), backoff_coefficient=2.0,
                             maximum_interval=timedelta(minutes=10), maximum_attempts=3,
                             non_retryable_error_types=NON_RETRYABLE))


@workflow.defn(name=WORKFLOW)
class EncodeEpisodeMp3Workflow:
    """Episodes -> tagged MP3 artifacts, in order. Started only on request, after the tracks exist."""

    @workflow.run
    async def run(self, inp: EncodeEpisodesInput) -> dict[str, Any]:
        out = []
        for episode in inp.episodes:
            result: EpisodeMp3Result = await workflow.execute_activity(
                ACTIVITY, episode, result_type=EpisodeMp3Result, task_queue=inp.activity_task_queue, **ENCODE_OPTIONS)
            out.append(result.model_dump(mode="json"))
        return {"episodes": out, "accepted": sum(1 for r in out if r["status"] == "accepted")}


__all__ = ["ACTIVITY", "EPISODE_MP3_STAGE", "EncodeEpisodeInput", "EncodeEpisodeMp3Workflow", "EncodeEpisodesInput",
           "EpisodeMp3Activities", "EpisodeMp3Result", "FORMAT_POLICY", "WORKFLOW", "episode_mp3_activities",
           "loudness_policy"]
