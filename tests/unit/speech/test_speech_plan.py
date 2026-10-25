"""Bounded, resumable track/segment planning (R7; audit 07 C4: full_book could never finish).

A whole book is planned as many bounded tracks, each made of bounded synthesis batches of
segments; planning always terminates, preserves every segment exactly once and in order, and
gives every segment a content-addressed audio key so a retry only renders what is missing.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from b2c_contracts import ArtifactRef, SpeechSegment, SpeechTextDocument
from b2c_speech.plan import PlanLimits, VoiceConfig, plan_tracks, segment_audio_key

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/", sha256="s" * 64,
                     created_at=NOW)
VOICE = VoiceConfig(provider="fasih_v1", voice_id="default", model_revision="rev1", seed=1234, sample_rate=24000)


def document(n, *, chars=300, heading_every=0):
    segments = []
    for i in range(n):
        heading = heading_every and i % heading_every == 0
        text = ("عنوان " if heading else "") + "ب" * (20 if heading else chars - 1) + "."
        segments.append(SpeechSegment(segment_id=f"bk:s{i:05d}", seq=i, unit_ids=[f"bk:u{i:05d}"],
                                      structure="heading" if heading else "paragraph", source_text=text,
                                      spoken_text=text, pause_after_ms=700 if heading else 250))
    return SpeechTextDocument(book_id="bk", language="ar", frontend_version="v", source=SOURCE, segments=segments)


def test_full_book_is_planned_into_bounded_tracks_and_batches():
    doc = document(2200)  # ~660k characters, the size of the book that never finished (audit 07 C4)
    limits = PlanLimits(max_track_seconds=3600, chars_per_second=8.9, max_segments_per_track=400,
                        max_batch_segments=8, max_batch_chars=2400)
    started = time.monotonic()
    plan = plan_tracks(doc, limits)
    assert time.monotonic() - started < 2.0
    ids = [sid for track in plan.tracks for sid in track.segment_ids]
    assert ids == [s.segment_id for s in doc.segments]
    for track in plan.tracks:
        assert track.estimated_seconds <= 3600 and len(track.segment_ids) <= 400
        assert [sid for batch in track.batches for sid in batch] == track.segment_ids
        assert all(len(batch) <= 8 for batch in track.batches)
    assert len(plan.tracks) >= 20 and plan.total_segments == 2200


def test_small_document_is_one_track():
    plan = plan_tracks(document(10), PlanLimits())
    assert len(plan.tracks) == 1 and plan.tracks[0].track_id == "bk:t000"


def test_tracks_prefer_heading_boundaries():
    doc = document(60, heading_every=20)
    plan = plan_tracks(doc, PlanLimits(max_track_seconds=600, min_track_seconds=60, chars_per_second=10))
    starts = [t.segment_ids[0] for t in plan.tracks]
    assert starts == ["bk:s00000", "bk:s00020", "bk:s00040"]


def test_one_oversized_segment_still_terminates():
    doc = document(3, chars=300)
    plan = plan_tracks(doc, PlanLimits(max_track_seconds=10, chars_per_second=8.9, max_batch_chars=100))
    assert [len(t.segment_ids) for t in plan.tracks] == [1, 1, 1]
    assert all(len(b) == 1 for t in plan.tracks for b in t.batches)


def test_plan_is_deterministic_and_versioned():
    a, b = plan_tracks(document(50), PlanLimits()), plan_tracks(document(50), PlanLimits())
    assert a == b and a.digest == b.digest and a.plan_version
    assert plan_tracks(document(50), PlanLimits(max_batch_segments=2)).digest != a.digest


def phrase_document(n, *, chars=53):
    """The new phrase segmentation (speech-readability 2026-10-25): median 53 characters per
    segment, mostly phrase/sentence pauses."""
    pauses = [250, 250, 450, 700]  # phrase, phrase, sentence, paragraph
    segments = [SpeechSegment(segment_id=f"bk:s{i:05d}", seq=i, unit_ids=[f"bk:u{i:05d}"], structure="paragraph",
                              source_text="ب" * (chars - 1) + ".", spoken_text="ب" * (chars - 1) + ".",
                              pause_after_ms=pauses[i % 4]) for i in range(n)]
    return SpeechTextDocument(book_id="bk", language="ar", frontend_version="v", source=SOURCE, segments=segments)


def test_track_length_is_bounded_by_duration_not_by_segment_count():  # T-AUD-10
    plan = plan_tracks(phrase_document(3708), PlanLimits())
    assert PlanLimits().max_segments_per_track is None
    full = plan.tracks[:-1]
    assert len(full) >= 2
    # every full track ends because the next segment would pass the duration bound, not at 400 segments
    assert all(PlanLimits().max_track_seconds - 15 < t.estimated_seconds <= PlanLimits().max_track_seconds
               for t in full)
    assert max(len(t.segment_ids) for t in plan.tracks) > 400


def test_an_explicit_segment_count_bound_is_still_honoured():
    plan = plan_tracks(phrase_document(1000), PlanLimits(max_segments_per_track=400))
    assert [len(t.segment_ids) for t in plan.tracks] == [400, 400, 200]


def test_plan_version_records_the_duration_bound():
    assert plan_tracks(document(5), PlanLimits()).plan_version == "speech-plan-2"


def test_invalid_limits_refused():
    with pytest.raises(ValueError):
        PlanLimits(max_batch_segments=0)
    with pytest.raises(ValueError):
        PlanLimits(chars_per_second=0)


def test_segment_audio_key_is_content_addressed():
    doc = document(2)
    first, second = doc.segments
    assert segment_audio_key(first, VOICE) == segment_audio_key(first, VOICE)
    assert segment_audio_key(first, VOICE) != segment_audio_key(second.model_copy(update={"spoken_text": "نص آخر."}),
                                                                 VOICE)
    assert segment_audio_key(first, VOICE) != segment_audio_key(first, VOICE.model_copy(update={"seed": 7}))
    # the id/position is not part of the key: the same text in the same voice is rendered once
    moved = first.model_copy(update={"segment_id": "bk:s09999", "seq": 9999})
    assert segment_audio_key(moved, VOICE) == segment_audio_key(first, VOICE)
