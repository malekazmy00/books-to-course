from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[3] / "eval/suites/phase3/retrieval_eval.py"
SPEC = importlib.util.spec_from_file_location("b2c_phase3_retrieval_eval", MODULE)
assert SPEC and SPEC.loader
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)


def chunks():
    return [
        {"chunk_id": "c1", "book_id": "main", "role": "spine", "pages": [2],
         "text": "An event has a probability between zero and one."},
        {"chunk_id": "c2", "book_id": "supp", "role": "supplementary", "pages": [7],
         "text": "Probability mass functions assign probability to outcomes."},
    ]


def query(qid="q1"):
    return {"schema_version": "b2c-retrieval-gold/1.0", "query_id": qid,
        "query": "How is an event probability bounded?", "split": "test", "question_type": "direct",
        "query_adjudication": "human_adjudicated",
        "qrels": [{"chunk_id": "c1", "relevance": 3, "book_id": "main", "role": "spine",
            "pages": [2], "evidence_quote": "An event has a probability between zero and one.",
            "adjudication": "human_adjudicated"}]}


def test_dataset_validation_requires_exact_source_and_human_adjudication(tmp_path):
    path = tmp_path / "qrels.jsonl"
    row = query()
    path.write_text(__import__("json").dumps(row) + "\n", encoding="utf-8")
    assert ev.validate_dataset(path, chunks())[0]["query_id"] == "q1"
    row["qrels"][0]["evidence_quote"] = "probability sometimes relates to outcomes"
    path.write_text(__import__("json").dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ev.DatasetError, match="exact source-grounded"):
        ev.validate_dataset(path, chunks())


def test_source_page_mismatch_rejected(tmp_path):
    row = query()
    row["qrels"][0]["pages"] = [99]
    path = tmp_path / "qrels.jsonl"
    path.write_text(__import__("json").dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ev.DatasetError, match="source/page attribution disagrees"):
        ev.validate_dataset(path, chunks())


def test_retrieval_metrics_and_unanswerable_false_positive(tmp_path):
    answerable = query()
    unanswerable = {"schema_version": "b2c-retrieval-gold/1.0", "query_id": "q2",
        "query": "What is the author's favorite color?", "split": "test", "question_type": "unanswerable",
        "query_adjudication": "human_adjudicated", "qrels": [],
        "unanswerable_reason": "No chunk in either source addresses this fact."}
    path = tmp_path / "qrels.jsonl"
    import json
    path.write_text("\n".join(json.dumps(row) for row in [answerable, unanswerable]) + "\n", encoding="utf-8")
    rows = ev.validate_dataset(path, chunks())
    result = ev.score_ranked_ids(rows, {"q1": ["c1", "c2"], "q2": ["c1"]}, ks=(1, 2))
    assert result["metrics"]["recall@1"] == 1.0
    assert result["metrics"]["mrr@1"] == 1.0
    assert result["metrics"]["source_attribution_integrity@1"] == 1.0
    assert result["metrics"]["page_attribution_integrity@1"] == 1.0
    assert result["metrics"]["unanswerable_false_positive_rate@1"] == 1.0


def test_model_auto_validated_qrels_are_opt_in_diagnostics_not_gold(tmp_path):
    import json
    row = query()
    row["query_adjudication"] = "model_auto_validated"
    row["qrels"][0]["adjudication"] = "model_auto_validated"
    path = tmp_path / "auto-qrels.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ev.DatasetError, match="unsupported query_adjudication"):
        ev.validate_dataset(path, chunks())
    loaded = ev.validate_dataset(path, chunks(), require_adjudicated=False, allow_auto_labeled=True)
    assert loaded[0]["query_adjudication"] == "model_auto_validated"
    scores = ev.score_ranked_ids(loaded, {"q1": ["c1"]}, ks=(1,))
    assert scores["metrics"]["recall@1"] == 1.0


def test_course_qa_result_adapts_to_pseudo_qrels_with_exact_attribution(tmp_path):
    import json
    result = {"status": "final", "scopes": [{"scope_id": "lesson-a", "status": "final", "items": [{
        "qa_id": "qa-1", "status": "final", "question": "How is event probability bounded?",
        "answer": "An event has a probability between zero and one.", "evidence_chunk_ids": ["c1"],
        "book_id": "main", "role": "spine", "pages": [2],
        "evidence_quote": "An event has a probability between zero and one.",
        "model_id": "local-qwen", "prompt_version": "p5-v1"}]}]}
    path = tmp_path / "course-qa.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    rows = ev.course_qa_to_pseudo_qrels(path, chunks(), "a" * 64)
    assert len(rows) == 1
    assert rows[0]["query_adjudication"] == "model_auto_validated"
    assert rows[0]["source_manifest_sha256"] == "a" * 64
    assert rows[0]["qrels"][0]["pages"] == [2]
    result["status"] = "failed"
    result["scopes"].append({"scope_id": "module-a", "status": "final", "items": result["scopes"][0]["items"]})
    path.write_text(json.dumps(result), encoding="utf-8")
    partial_rows = ev.course_qa_to_pseudo_qrels(path, chunks(), "a" * 64)
    assert len(partial_rows) == 1  # re-used child-bank item is scored once
    assert partial_rows[0]["generation_course_status"] == "failed"
    result["scopes"][0]["items"][0]["evidence_quote"] = "not from the cited source"
    path.write_text(json.dumps(result), encoding="utf-8")
    with pytest.raises(ev.DatasetError, match="not exact source evidence"):
        ev.course_qa_to_pseudo_qrels(path, chunks(), "a" * 64)


def test_quote_validation_normalizes_ligatures_without_fuzzy_matching(tmp_path):
    source = [{"chunk_id": "c1", "book_id": "main", "role": "spine", "pages": [2],
               "text": "The coeﬃcient is 0.5 and the estimate is stable."}]
    row = query()
    row["qrels"][0]["evidence_quote"] = "The coefficient is 0.5 and the estimate is stable."
    path = tmp_path / "ligature-qrels.jsonl"
    import json
    path.write_text(json.dumps(row), encoding="utf-8")
    assert ev.validate_dataset(path, source)[0]["query_id"] == "q1"
    row["qrels"][0]["evidence_quote"] = "The coefficient is 0.6 and the estimate is stable."
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ev.DatasetError, match="exact source-grounded"):
        ev.validate_dataset(path, source)

def test_system_machine_labels_require_provenance_and_remain_non_gold(tmp_path):
    import json
    row=query()
    row["query_adjudication"]="system_machine_evaluated"
    row["gold_eligible"]=False
    row["system_review"]={"system_id":"independent-judge","model_id":"judge-v1",
        "prompt_sha256":"a"*64,
        "scope":"machine semantic answerability/completeness and exact source citation; not human gold"}
    row["qrels"][0]["adjudication"]="system_machine_evaluated"
    path=tmp_path/"system-labels.jsonl"
    path.write_text(json.dumps(row)+"\n",encoding="utf-8")
    with pytest.raises(ev.DatasetError,match="unsupported query_adjudication"):
        ev.validate_dataset(path,chunks(),require_adjudicated=False)
    loaded=ev.validate_dataset(path,chunks(),require_adjudicated=False,allow_system_evaluated=True)
    assert loaded[0]["gold_eligible"] is False
    row["system_review"].pop("prompt_sha256")
    path.write_text(json.dumps(row)+"\n",encoding="utf-8")
    with pytest.raises(ev.DatasetError,match="machine review provenance"):
        ev.validate_dataset(path,chunks(),require_adjudicated=False,allow_system_evaluated=True)