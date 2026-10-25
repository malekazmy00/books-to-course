import json
from datetime import UTC, datetime
import pytest
from b2c_contracts import ArtifactRef, ParsedBook
from b2c_core.source_identity import artifact_source_identity, parsed_source_identity

def ref(stage, digest):
    return ArtifactRef(stage=stage, key=digest, uri=f"s3://artifacts/c/{stage}/{digest}",
                       sha256=digest, created_at=datetime.now(UTC))

class Store:
    def __init__(self, files): self.files = files
    def read(self, reference, name): return self.files[(reference.uri,name)]

def book(digest=None):
    return ParsedBook(book_id="book", title="Book", role="spine", page_count=1,
        toc_source="headings", blocks=[], parser={"name":"test", **({"source_file_sha256":digest} if digest else {})})

def test_pdf_fingerprint_remains_stable_when_ocr_artifact_changes():
    parsed = book("a"*64)
    old = parsed_source_identity(parsed,ref("ingest-parse-pdf","b"*64))
    new = parsed_source_identity(parsed,ref("ingest-parse-pdf","c"*64))
    assert old["source_file_sha256"] == new["source_file_sha256"] == "a"*64
    assert old["source_parsed_sha256"] != new["source_parsed_sha256"]
    assert old["source_fingerprint_verified"] is True

def test_translation_resolves_exact_input_chain_not_book_alias():
    parsed_ref = ref("ingest-parse-pdf","b"*64)
    translation_ref = ref("translate-content","c"*64)
    store = Store({(parsed_ref.uri,"parsed-book.json"):book("a"*64).model_dump_json().encode(),
        (translation_ref.uri,"translation-source.json"):json.dumps({"source_artifact_ref":parsed_ref.model_dump(mode="json")}).encode()})
    identity = artifact_source_identity(store,translation_ref)
    assert identity["source_file_sha256"] == "a"*64
    assert identity["source_parsed_sha256"] == parsed_ref.sha256

def test_ingest_v2_parsed_artifacts_resolve_like_v1_ones():
    # R4: an outline approved from an ingest v2 run resolves to the v1 projection stored beside v2
    parsed_ref = ref("ingest-parse-v2", "b" * 64)
    approved_ref = ref("approve-outline", "c" * 64)
    store = Store({(parsed_ref.uri, "parsed-book.json"): book("a" * 64).model_dump_json().encode(),
                   (approved_ref.uri, "approved-outline.json"): json.dumps(
                       {"spine_parsed_ref": parsed_ref.model_dump(mode="json")}).encode()})
    identity = artifact_source_identity(store, approved_ref)
    assert identity["source_file_sha256"] == "a" * 64 and identity["source_parsed_ref"]["stage"] == "ingest-parse-v2"

def test_legacy_source_never_claims_verified_pdf_hash():
    identity = parsed_source_identity(book(),ref("ingest-parse-pdf","b"*64))
    assert identity["source_file_sha256"] is None
    assert identity["source_fingerprint_verified"] is False

def test_corrupt_fingerprint_is_rejected():
    with pytest.raises(ValueError,match="fingerprint"):
        parsed_source_identity(book("wrong"),ref("ingest-parse-pdf","b"*64))
