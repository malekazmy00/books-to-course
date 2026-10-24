import importlib.util
from pathlib import Path
import pytest
SCRIPT=Path(__file__).parents[3]/"eval/suites/phase3/system_review_query_pack.py"
SPEC=importlib.util.spec_from_file_location("phase3_system_review",SCRIPT)
MODULE=importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
def pack_and_chunks():
    chunks=[{"chunk_id":f"c{i}","book_id":"b","role":"spine","pages":[1],"text":f"A verified source sentence number {i}."} for i in range(50)]
    rows=[]
    for i,c in enumerate(chunks):
        rows.append({"query_id":f"q{i}","query":f"What does source sentence number {i} say?",
            "human_query_valid":None,"human_answerability":None,"human_relevant_chunk_ids":None,
            "human_reviewer_id":None,"human_review_note":None,
            "suggested_qrels_for_review_only":[{"chunk_id":c["chunk_id"],"book_id":"b","role":"spine","pages":[1],
                "suggested_evidence_quote":c["text"],"candidate_chunk_text":c["text"]}]})
    return {"gold_eligible":False,"source_manifest_sha256":"m","query_candidate_sha256":"q",
            "chunk_files_sha256":{"c.jsonl":"h"},"queries":rows},chunks

def test_system_pack_audit_is_explicitly_source_only_and_non_gold():
    pack,chunks=pack_and_chunks()
    result=MODULE.review_pack(pack,chunks,manifest_sha256="m",query_sha256="q")
    assert result["query_count"]==50 and result["validated_citations"]==50
    assert result["gold_eligible"] is False
    assert result["label_status"]=="source_citation_validated_semantic_relevance_unadjudicated"
    assert all(row["semantic_query_validity"]=="not_adjudicated" for row in result["queries"])
    assert "reviewer_id" not in result

def test_system_pack_audit_rejects_quote_mutation_and_human_identity():
    pack,chunks=pack_and_chunks(); pack["queries"][0]["suggested_qrels_for_review_only"][0]["suggested_evidence_quote"]="unsupported quote"*3
    with pytest.raises(ValueError,match="quote"):
        MODULE.review_pack(pack,chunks,manifest_sha256="m",query_sha256="q")
    pack,chunks=pack_and_chunks(); pack["queries"][0]["human_reviewer_id"]="pretend-human"
    with pytest.raises(ValueError,match="human fields"):
        MODULE.review_pack(pack,chunks,manifest_sha256="m",query_sha256="q")