import pytest

from b2c_contracts import Block, PageRef, ParsedBook
from b2c_outline import OutlineUnavailable
from b2c_orchestration.ingest import OutlineActivities


def parsed_book():
    return ParsedBook(
        book_id="book-no-toc", title="No TOC", role="spine", page_count=2,
        toc_source="headings", blocks=[
            Block(block_id="book-no-toc:1:1", type="paragraph", text="First concept.",
                  refs=[PageRef(book_id="book-no-toc", page_no=1)], section_path=[], ocr=False),
            Block(block_id="book-no-toc:2:1", type="paragraph", text="Second concept.",
                  refs=[PageRef(book_id="book-no-toc", page_no=2)], section_path=[], ocr=False),
        ], parser={"name": "fixture", "version": "1", "options_hash": "a" * 64},
    )


class FakeGateway:
    provider_name = "llama.cpp"
    model_id = "fixture-local-model"
    page_batch_max_chars = 24000
    page_batch_max_pages = 10

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def structured(self, messages, schema, **kwargs):
        self.calls.append((messages, kwargs))
        return schema.model_validate(self.payload)


def test_no_toc_activity_uses_structured_model_and_preserves_review_provenance():
    gateway = FakeGateway({"sections": [{
        "title": "Core concepts", "start_block": "book-no-toc:1:1", "end_block": "book-no-toc:2:1",
    }]})
    sections = OutlineActivities(store=None, gateway=gateway)._infer_sections(parsed_book())
    assert gateway.calls
    assert sections[0].title == "Core concepts"
    assert sections[0].start_block == "book-no-toc:1:1"
    assert sections[0].end_block == "book-no-toc:2:1"


def test_no_toc_activity_rejects_model_that_omits_source_blocks():
    gateway = FakeGateway({"sections": [{
        "title": "Core concepts", "start_block": "book-no-toc:1:1", "end_block": "book-no-toc:1:1",
    }]})
    with pytest.raises(OutlineUnavailable, match="omitted"):
        OutlineActivities(store=None, gateway=gateway)._infer_sections(parsed_book())


def test_no_toc_remote_route_is_disabled_without_explicit_eval_opt_in(monkeypatch):
    gateway = FakeGateway({"sections": [{
        "title": "Core concepts", "start_block": "book-no-toc:1:1", "end_block": "book-no-toc:2:1",
    }]})
    gateway.provider_name = "openrouter"
    monkeypatch.delenv("B2C_OUTLINE_REMOTE_EVAL_DATA", raising=False)
    with pytest.raises(OutlineUnavailable, match="remote evaluation-data route"):
        OutlineActivities(store=None, gateway=gateway)._infer_sections(parsed_book())
    assert not gateway.calls
