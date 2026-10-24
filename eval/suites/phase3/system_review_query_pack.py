"""System audit of the frozen 50-query pack; validates source citations, not semantics."""
from __future__ import annotations
import hashlib, json, unicodedata
from pathlib import Path
from typing import Any

POLICY_ID = "b2c-retrieval-source-citation-review"
POLICY_VERSION = "1.0.0"
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def norm(text: str) -> str: return " ".join(unicodedata.normalize("NFKC", text).split())
def review_pack(pack: dict[str, Any], chunks: list[dict[str, Any]], *,
                manifest_sha256: str, query_sha256: str) -> dict[str, Any]:
    """Verify all evidence links and freeze a system review attestation.

    The routine never judges whether a question is clear/answerable or whether its
    qrel is semantically relevant. Those fields remain pending; output is non-gold.
    """
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    if len(by_id) != len(chunks): raise ValueError("chunk IDs must be unique")
    if pack.get("gold_eligible") is not False: raise ValueError("input pack must remain non-gold")
    if pack.get("source_manifest_sha256") != manifest_sha256: raise ValueError("source manifest hash mismatch")
    if pack.get("query_candidate_sha256") != query_sha256: raise ValueError("query candidate hash mismatch")
    expected_chunks = pack.get("chunk_files_sha256", {})
    if not expected_chunks: raise ValueError("frozen chunk hashes are required")
    checked = []
    query_ids = set()
    for row in pack.get("queries", []):
        qid = row.get("query_id")
        if not qid or qid in query_ids: raise ValueError("query IDs must be nonempty and unique")
        query_ids.add(qid)
        if any(row.get(k) is not None for k in ("human_query_valid", "human_answerability", "human_relevant_chunk_ids", "human_reviewer_id", "human_review_note")):
            raise ValueError(f"human fields must remain blank: {qid}")
        candidates = row.get("suggested_qrels_for_review_only", [])
        if not candidates: raise ValueError(f"no source-citation candidate in {qid}")
        qrel_evidence = []
        for candidate in candidates:
            cid = candidate.get("chunk_id")
            chunk = by_id.get(cid)
            if chunk is None: raise ValueError(f"candidate chunk outside frozen corpus: {qid}/{cid}")
            if (candidate.get("book_id"), candidate.get("role"), candidate.get("pages")) != (chunk["book_id"], chunk["role"], chunk["pages"]):
                raise ValueError(f"book/role/page mismatch: {qid}/{cid}")
            quote = candidate.get("suggested_evidence_quote", "")
            if len(norm(quote)) < 12 or norm(quote) not in norm(chunk["text"]):
                raise ValueError(f"evidence quote is not an exact normalized source substring: {qid}/{cid}")
            if candidate.get("candidate_chunk_text") != chunk["text"]:
                raise ValueError(f"candidate chunk text differs from frozen source: {qid}/{cid}")
            qrel_evidence.append({"chunk_id":cid,"evidence_quote_sha256":hashlib.sha256(quote.encode()).hexdigest(),
                                  "source_chunk_sha256":hashlib.sha256(chunk["text"].encode()).hexdigest()})
        evidence={"query_id":qid,"query_sha256":hashlib.sha256(row["query"].encode()).hexdigest(),
                  "qrels":qrel_evidence,"checks":["query_id_unique","human_review_fields_blank",
                  "frozen_chunk_identity_matches","exact_source_quote_matches","chunk_text_matches_frozen_source"]}
        evidence_raw=json.dumps(evidence,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
        checked.append({**evidence,"evidence_sha256":hashlib.sha256(evidence_raw).hexdigest(),
                        "semantic_query_validity":"not_adjudicated","semantic_qrel_relevance":"not_adjudicated"})
    if len(checked) != 50: raise ValueError(f"expected exactly 50 queries; found {len(checked)}")
    attestation={"schema":"b2c-system-reviewed-retrieval-pack/1.0","review_type":"system",
        "system_id":POLICY_ID,"policy_version":POLICY_VERSION,"gold_eligible":False,
        "label_status":"source_citation_validated_semantic_relevance_unadjudicated",
        "scope":"checks frozen artifact identity and exact source/page citation; does not validate query clarity, answerability, or semantic relevance",
        "source_manifest_sha256":manifest_sha256,"query_candidate_sha256":query_sha256,
        "query_count":len(checked),"validated_citations":sum(len(x["qrels"]) for x in checked),
        "queries":checked}
    raw=json.dumps(attestation,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
    attestation["attestation_sha256"]=hashlib.sha256(raw).hexdigest()
    return attestation

def main() -> None:
    root=Path("/mnt/wsl/b2cdata/phase1-sources")
    from retrieval_eval import load_chunks
    manifest=Path("/root/projects/book2course/eval/golden/phase1/source-manifest.json"); query=root/"phase3-qa-50.jsonl"
    pack_path=root/"phase3-owner-review-pack.json"
    pack=json.loads(pack_path.read_text(encoding="utf-8"))
    chunks=[]
    for name in ("lane-primary-30-chunks.jsonl","thinkstats-supplement-7-chunks.jsonl"):
        chunk_path=root/name
        if pack["chunk_files_sha256"].get(name) != sha(chunk_path): raise ValueError(f"frozen chunk file changed: {name}")
        chunks.extend(load_chunks(chunk_path))
    result=review_pack(pack,chunks,manifest_sha256=sha(manifest),query_sha256=sha(query))
    out=root/"phase3-owner-review-system-audit.json"
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="queries"},ensure_ascii=False,indent=2))
if __name__=="__main__": main()