"""StructuredChunk v2 provenance, IndexQualityReport and SupplementLinkJudgement (SCHEMA 1.5.0, R5)."""
import hashlib

import pytest
from pydantic import ValidationError

import b2c_contracts as c


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk(**kw):
    text = kw.pop("text", "Alpha beta.\n\nGamma.")
    base = dict(chunk_id="b:chunk:1", book_id="b", role="spine", section_path=["M", "L"],
                block_ids=["b:1", "b:2"], pages=[1], text=text, token_count=4, embedding_model="unassigned",
                evidence_class="body", lesson_id="m1.l1",
                parts=[c.ChunkPart(block_id="b:1", start=0, end=11), c.ChunkPart(block_id="b:2", start=0, end=6)],
                text_sha256=sha(text), chunker_version="b2c-chunker/2")
    return c.StructuredChunk(**(base | kw))


def test_schema_version_bumped_for_index_v2():
    assert tuple(int(x) for x in c.SCHEMA_VERSION.split(".")) >= (1, 5, 0)


def test_v1_chunk_payload_still_validates():
    legacy = {"chunk_id": "b:chunk:x", "book_id": "b", "role": "spine", "section_path": [],
              "block_ids": ["b:1"], "pages": [1], "text": "t", "token_count": 1, "embedding_model": "unassigned",
              "schema_version": "1.1.0"}
    parsed = c.StructuredChunk.model_validate(legacy)
    assert parsed.evidence_class == "body" and parsed.parts == [] and parsed.text_sha256 is None


def test_parts_must_follow_block_ids():
    assert chunk().parts[0].block_id == "b:1"
    with pytest.raises(ValidationError):
        chunk(block_ids=["b:2", "b:1"])
    with pytest.raises(ValidationError):
        chunk(parts=[c.ChunkPart(block_id="b:1", start=0, end=11)])  # b:2 has no part


def test_part_range_is_nonempty():
    with pytest.raises(ValidationError):
        c.ChunkPart(block_id="b:1", start=5, end=5)


def test_text_sha256_must_match_text():
    with pytest.raises(ValidationError):
        chunk(text_sha256="0" * 64)


def test_note_class_is_explicit():
    assert chunk(evidence_class="note").evidence_class == "note"
    with pytest.raises(ValidationError):
        chunk(evidence_class="index")


def check(name, value, op, threshold):
    ok = {"<=": value <= threshold, ">=": value >= threshold, "==": value == threshold}[op]
    return c.QualityCheck(name=name, value=value, op=op, threshold=threshold, passed=ok)


def test_index_report_cannot_be_accepted_with_a_failed_check():
    good = [check("orphan_blocks", 0, "==", 0)]
    bad = [check("orphan_blocks", 3, "==", 0)]
    assert c.IndexQualityReport(book_id="b", role="spine", report_version="1", chunker_version="x",
                                source_format="parsed-book/2", metrics={}, checks=good, accepted=True).accepted
    with pytest.raises(ValidationError):
        c.IndexQualityReport(book_id="b", role="spine", report_version="1", chunker_version="x",
                             source_format="parsed-book/2", metrics={}, checks=bad, accepted=True)


def judgement(**kw):
    base = dict(lesson_id="m1.l1", chunk_id="s:chunk:1", book_id="s", rank=1, method="tfidf-cosine/1",
                score=0.3, threshold=0.2, verdict="linked", calibrated=False, policy_version="sha256:x")
    return c.SupplementLinkJudgement(**(base | kw))


def test_link_verdict_must_follow_threshold():
    assert judgement().verdict == "linked"
    assert judgement(score=0.1, verdict="below_threshold").verdict == "below_threshold"
    with pytest.raises(ValidationError):
        judgement(score=0.1, verdict="linked")
    with pytest.raises(ValidationError):
        judgement(score=0.3, verdict="below_threshold")
