from b2c_orchestration.translation import merge_translation_checkpoint
from b2c_translation import SourceSegment


IDENTITY = {"source_sha256": "source", "model_id": "model", "source_kind": "book",
            "source_language": "en", "target_language": "ar"}
SOURCES = {"page:1": SourceSegment(segment_id="page:1", text="source text " * 60)}


def checkpoint(at, text, **changes):
    return {**IDENTITY, "batch_index": 1, "completed_at": at,
            "segments": [{"segment_id": "page:1", "text": text}], **changes}


def test_newest_complete_unit_wins_independent_of_listing_order():
    old = checkpoint("2026-10-08T01:00:00Z", "ترجمة مكتملة سابقة " * 60)
    new = checkpoint("2026-10-08T02:00:00+00:00", "ترجمة مكتملة أحدث " * 60)
    for order in ([old, new], [new, old]):
        selected = {}
        for record in order:
            merge_translation_checkpoint(record, IDENTITY, SOURCES, selected)
        assert selected["page:1"][1].text == ("ترجمة مكتملة أحدث " * 60).strip()


def test_new_truncated_unit_does_not_displace_complete_checkpoint():
    selected = {}
    merge_translation_checkpoint(checkpoint("2026-10-08T01:00:00Z", "ترجمة مكتملة " * 80), IDENTITY, SOURCES, selected)
    merge_translation_checkpoint(checkpoint("2026-10-08T02:00:00Z", "عنوان"), IDENTITY, SOURCES, selected)
    assert selected["page:1"][1].text == ("ترجمة مكتملة " * 80).strip()


def test_only_truncated_checkpoint_is_not_reused():
    selected = {}
    merge_translation_checkpoint(checkpoint("2026-10-08T02:00:00Z", "عنوان"), IDENTITY, SOURCES, selected)
    assert selected == {}


def test_incompatible_or_invalid_checkpoint_cannot_displace_valid_result():
    selected = {}
    merge_translation_checkpoint(checkpoint("2026-10-08T01:00:00Z", "ترجمة مكتملة " * 80), IDENTITY, SOURCES, selected)
    for record in (
        checkpoint("2026-10-08T02:00:00Z", "ترجمة خاطئة " * 100, model_id="other"),
        checkpoint("not-a-date", "ترجمة خاطئة " * 100),
        checkpoint("2026-10-08T02:00:00", "ترجمة خاطئة " * 100),
        checkpoint("2026-10-08T02:00:00Z", "ترجمة خاطئة " * 100, batch_index=0),
        checkpoint("2026-10-08T02:00:00Z", "ترجمة خاطئة " * 100, segments=[{"segment_id": "unknown", "text": "wrong"}]),
    ):
        merge_translation_checkpoint(record, IDENTITY, SOURCES, selected)
    assert selected["page:1"][1].text == ("ترجمة مكتملة " * 80).strip()


def test_numeric_corruption_cannot_displace_valid_result():
    sources = {"page:1": SourceSegment(segment_id="page:1", text="2026 " + "source text " * 60)}
    selected = {}
    merge_translation_checkpoint(checkpoint("2026-10-08T01:00:00Z", "2026 " + "ترجمة مكتملة " * 80), IDENTITY, sources, selected)
    merge_translation_checkpoint(checkpoint("2026-10-08T02:00:00Z", "2025 " + "ترجمة خاطئة " * 100), IDENTITY, sources, selected)
    assert selected["page:1"][1].text.startswith("2026 ")


def test_checkpoint_listing_includes_tail_batches_and_all_s3_pages():
    import io
    import json
    from types import SimpleNamespace
    from b2c_orchestration.translation import translation_checkpoint_records
    prefix = "checkpoints/workflow/fingerprint/"
    records = {
        prefix + "batch-000001.json": checkpoint("2026-10-08T01:00:00Z", "ترجمة سابقة " * 100),
        prefix + "batch-001001.json": checkpoint("2026-10-08T02:00:00Z", "ترجمة مكتملة " * 100, batch_index=1001),
    }
    class S3:
        def list_objects_v2(self, **args):
            assert args["Prefix"] == prefix
            if "ContinuationToken" not in args:
                return {"Contents": [{"Key": list(records)[0]}], "IsTruncated": True, "NextContinuationToken": "tail"}
            assert args["ContinuationToken"] == "tail"
            return {"Contents": [{"Key": list(records)[1]}], "IsTruncated": False}
        def get_object(self, **args):
            return {"Body": io.BytesIO(json.dumps(records[args["Key"]]).encode())}
    loaded=list(translation_checkpoint_records(SimpleNamespace(s3=S3(),bucket="test"),prefix))
    assert len(loaded) == 2
    selected={}
    for _, record in loaded:
        merge_translation_checkpoint(record,IDENTITY,SOURCES,selected)
    assert selected["page:1"][1].text == ("ترجمة مكتملة " * 100).strip()


def test_checkpoint_listing_does_not_silently_accept_broken_pagination():
    import pytest
    from types import SimpleNamespace
    from b2c_orchestration.translation import translation_checkpoint_records
    class S3:
        def list_objects_v2(self, **args):
            return {"Contents": [], "IsTruncated": True}
    with pytest.raises(ValueError, match="invalid pagination"):
        list(translation_checkpoint_records(SimpleNamespace(s3=S3(),bucket="test"),"checkpoints/"))


def test_untranslated_english_checkpoint_cannot_displace_valid_arabic():
    selected = {}
    accepted = "ترجمة مكتملة " * 80
    merge_translation_checkpoint(checkpoint("2026-10-08T01:00:00Z", accepted),
                                 IDENTITY, SOURCES, selected)
    merge_translation_checkpoint(checkpoint("2026-10-08T02:00:00Z", "untranslated English prose " * 60),
                                 IDENTITY, SOURCES, selected)
    assert selected["page:1"][1].text == accepted.strip()
