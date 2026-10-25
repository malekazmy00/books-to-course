"""Speech-ready text (SCHEMA 1.5.0, R7 / audit 07 §5.2 S1). Negative controls MUST raise.

A ``SpeechTextDocument`` is what the TTS stage reads: deterministic spoken text split into TTS
segments, each traced to the accepted translation units it was made from (C3).
"""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import b2c_contracts as c

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
SOURCE = c.ArtifactRef(stage="translate-book", key="k", uri="s3://artifacts/c1/translate-book/k/",
                       sha256="s" * 64, created_at=NOW)


def seg(seq, **kw):
    base = dict(segment_id=f"bk:s{seq:05d}", seq=seq, unit_ids=["bk:u00001"], block_ids=["bk:b00001"],
                structure="paragraph", source_text="نص.", spoken_text="نص.", pause_after_ms=250)
    return c.SpeechSegment(**(base | kw))


def doc(segments, **kw):
    base = dict(book_id="bk", language="ar", frontend_version="speech-text-ar-1", lexicon_digest=None,
                source=SOURCE, segments=list(segments))
    return c.SpeechTextDocument(**(base | kw))


def test_schema_version_bumped_for_speech_text():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 5, 0)


def test_valid_document_round_trips():
    d = doc([seg(0), seg(1, unit_ids=["bk:u00002"], structure="heading", pause_after_ms=700)],
            excluded=[c.SpeechExclusion(unit_id="bk:u00003", reason="not_spoken_kind:footnote")])
    again = c.SpeechTextDocument.model_validate_json(d.model_dump_json())
    assert again == d and again.segments[1].structure == "heading"


def test_segment_needs_a_unit():
    with pytest.raises(ValidationError):
        seg(0, unit_ids=[])


def test_segment_spoken_text_not_blank():
    with pytest.raises(ValidationError):
        seg(0, spoken_text="   ")


def test_segment_ids_unique():
    with pytest.raises(ValidationError):
        doc([seg(0), seg(1, segment_id="bk:s00000")])


def test_segment_order_is_contiguous():
    with pytest.raises(ValidationError):
        doc([seg(0), seg(2)])


def test_excluded_unit_cannot_also_be_spoken():
    with pytest.raises(ValidationError):
        doc([seg(0)], excluded=[c.SpeechExclusion(unit_id="bk:u00001", reason="dropped")])


def test_document_needs_segments():
    with pytest.raises(ValidationError):
        doc([])


def test_pause_bounded():
    with pytest.raises(ValidationError):
        seg(0, pause_after_ms=60_000)
