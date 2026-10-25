"""Bounded, resumable speech plan (R7; audit 07 C4, §5.2 "the unit of work is the segment").

The old ``full_book`` layout sent a whole book (639k characters, ~20 h of audio, ~60 h of CPU
rendering at RTF ~3) in one HTTP request under a 24 h activity limit: it could not finish. Here
a ``SpeechTextDocument`` is cut into tracks of at most ``max_track_seconds`` (estimated from the
measured speaking rate) and ``max_segments_per_track``, preferring heading boundaries, and each
track into synthesis batches of at most ``max_batch_segments`` / ``max_batch_chars``. Every loop
step consumes one segment, so planning always terminates; every segment's audio has a
content-addressed key (text + voice + model revision + seed), so a retried batch renders only
what is missing and one segment can be re-rendered alone.
"""
from __future__ import annotations

import hashlib
import json

from b2c_contracts import SpeechSegment, SpeechTextDocument, stage_key
from pydantic import BaseModel, ConfigDict, Field

PLAN_VERSION = "speech-plan-2"  # 2: tracks bounded by duration; the segment-count bound is opt-in (D-042)
SEGMENT_AUDIO_STAGE = "speech-segment-audio"
SEGMENT_AUDIO_VERSION = "1"


class PlanLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    # 30 min (D-042): measured peak RSS of one assembly (trim + pauses + BS.1770 + WAV, segment PCMs
    # kept for ASR) is ~1.16 GiB at 30 min, ~1.62 GiB at 45, ~2.11 GiB at 60 (eval/reports/mp3-delivery.md);
    # the audio worker's mem_limit is 2 GiB. Raise both together, never this alone.
    max_track_seconds: float = Field(default=1800.0, gt=0)
    min_track_seconds: float = Field(default=600.0, ge=0)  # below this a heading does not start a track
    chars_per_second: float = Field(default=8.9, gt=0)  # measured on Fasih output (audit 07 C4)
    # None: no count bound. A fixed count tracked duration only while segments were ~14 s long;
    # with phrase segmentation (median 53 chars, ~6.4 s with its pause) 400 segments cut every
    # track at ~43 min. Duration is what bounds the work and the memory of one assembly.
    max_segments_per_track: int | None = Field(default=None, ge=1)
    max_batch_segments: int = Field(default=8, ge=1)
    max_batch_chars: int = Field(default=2400, ge=1)


class VoiceConfig(BaseModel):
    """Everything that changes the rendered audio of a segment; all of it is in the audio key."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str
    voice_id: str
    model_revision: str  # from the provider's own identity, never from the worker's environment (audit 07 H2)
    seed: int = 1234
    sample_rate: int = 24000
    speed: float = 1.0


class TrackPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    track_id: str
    title: str | None = None
    segment_ids: list[str]
    batches: list[list[str]]
    spoken_chars: int
    estimated_seconds: float


class SpeechPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_version: str = PLAN_VERSION
    book_id: str
    limits: PlanLimits
    tracks: list[TrackPlan]
    total_segments: int
    estimated_seconds: float

    @property
    def digest(self) -> str:
        raw = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seconds(segment: SpeechSegment, limits: PlanLimits) -> float:
    return len(segment.spoken_text) / limits.chars_per_second + segment.pause_after_ms / 1000


def _batches(segments: list[SpeechSegment], limits: PlanLimits) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    chars = 0
    for segment in segments:
        size = len(segment.spoken_text)
        if current and (len(current) >= limits.max_batch_segments or chars + size > limits.max_batch_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(segment.segment_id)
        chars += size
    if current:
        batches.append(current)
    return batches


def plan_tracks(doc: SpeechTextDocument, limits: PlanLimits) -> SpeechPlan:
    groups: list[list[SpeechSegment]] = []
    current: list[SpeechSegment] = []
    seconds = 0.0
    for segment in doc.segments:  # one segment per step: always terminates
        cost = _seconds(segment, limits)
        heading_break = segment.structure == "heading" and seconds >= limits.min_track_seconds
        full = seconds + cost > limits.max_track_seconds or (
            limits.max_segments_per_track is not None and len(current) >= limits.max_segments_per_track)
        if current and (heading_break or full):
            groups.append(current)
            current, seconds = [], 0.0
        current.append(segment)
        seconds += cost
    if current:
        groups.append(current)
    tracks = []
    for i, group in enumerate(groups):
        title = group[0].spoken_text[:120] if group[0].structure == "heading" else None
        tracks.append(TrackPlan(track_id=f"{doc.book_id}:t{i:03d}", title=title,
                                segment_ids=[s.segment_id for s in group], batches=_batches(group, limits),
                                spoken_chars=sum(len(s.spoken_text) for s in group),
                                estimated_seconds=round(sum(_seconds(s, limits) for s in group), 3)))
    return SpeechPlan(book_id=doc.book_id, limits=limits, tracks=tracks, total_segments=len(doc.segments),
                      estimated_seconds=round(sum(t.estimated_seconds for t in tracks), 3))


def segment_audio_key(segment: SpeechSegment, voice: VoiceConfig) -> str:
    """Content address of one segment's audio: same spoken text in the same voice = same key."""
    text_sha = hashlib.sha256(segment.spoken_text.encode("utf-8")).hexdigest()
    return stage_key(SEGMENT_AUDIO_STAGE, SEGMENT_AUDIO_VERSION, [text_sha], voice.model_dump(mode="json"),
                     model_id=f"{voice.provider}:{voice.voice_id}", prompt_version=voice.model_revision)


__all__ = ["PLAN_VERSION", "PlanLimits", "SEGMENT_AUDIO_STAGE", "SEGMENT_AUDIO_VERSION", "SpeechPlan", "TrackPlan",
           "VoiceConfig", "plan_tracks", "segment_audio_key"]
