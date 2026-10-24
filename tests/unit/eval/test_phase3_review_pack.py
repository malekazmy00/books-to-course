import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[3] / "eval/suites/phase3/build_owner_review_pack.py"
SPEC = importlib.util.spec_from_file_location("phase3_owner_review_pack", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_review_pack_keeps_model_labels_pending_and_pins_sources(tmp_path):
    chunk_path = tmp_path / "chunk.jsonl"
    source = {"book_id": "b1", "role": "spine", "pages": [1],
              "text": "A fair coin has two equally likely outcomes."}
    chunk_path.write_text("\n".join(json.dumps({**source, "chunk_id": f"c{i}"}) for i in range(37)),
                          encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = {"sources": [{"chunk_file": chunk_path.name, "rights_status": "internal_test_only",
                             "chunk_sha256": MODULE.sha256(chunk_path)}]}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    query = {"query": "What are the coin outcomes?", "split": "development",
             "question_type": "direct", "query_adjudication": "model_auto_validated",
             "label_origin": "fixture", "qrels": [{"chunk_id": "c0", "book_id": "b1",
             "role": "spine", "pages": [1], "evidence_quote": source["text"],
             "relevance": 3, "adjudication": "model_auto_validated"}]}
    query["source_manifest_sha256"] = MODULE.sha256(manifest_path)
    query_path = tmp_path / "queries.jsonl"
    query_path.write_text("\n".join(json.dumps({**query, "query_id": f"q{i}"}) for i in range(50)),
                          encoding="utf-8")
    pack = MODULE.build_pack(manifest_path, query_path, [chunk_path])
    assert pack["status"] == "pending_human_review"
    assert pack["gold_eligible"] is False
    assert len(pack["queries"]) == 50
    assert pack["queries"][0]["human_query_valid"] is None
    assert pack["queries"][0]["suggested_qrels_for_review_only"][0]["candidate_chunk_text"] == source["text"]
    assert pack["queries"][0]["suggested_qrels_for_review_only"][0]["human_relevance"] is None


def test_review_pack_rejects_frozen_corpus_hash_mismatch(tmp_path):
    chunk_path = tmp_path / "chunk.jsonl"
    chunk_path.write_text("\n".join(json.dumps({"chunk_id": f"c{i}", "book_id": "b1",
        "role": "spine", "pages": [1], "text": "A source passage."}) for i in range(37)),
        encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"sources": [{"chunk_file": chunk_path.name,
        "rights_status": "internal_test_only", "chunk_sha256": "0" * 64}]}), encoding="utf-8")
    query_path = tmp_path / "queries.jsonl"
    query_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        MODULE.build_pack(manifest_path, query_path, [chunk_path])
