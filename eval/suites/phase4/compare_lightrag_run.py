"""Build a paired diagnostic comparing LightRAG rankings with B2C lexical RAG."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import pathlib
import statistics

def load_module(path: pathlib.Path, name: str):
    spec=importlib.util.spec_from_file_location(name,path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    vals=sorted(values)
    pos=(len(vals)-1)*p
    lo=int(pos); hi=min(lo+1,len(vals)-1); frac=pos-lo
    return round(vals[lo]*(1-frac)+vals[hi]*frac,3)

def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidate",type=pathlib.Path,required=True)
    ap.add_argument("--chunks",type=pathlib.Path,nargs="+",required=True)
    ap.add_argument("--dataset",type=pathlib.Path,required=True)
    ap.add_argument("--output",type=pathlib.Path,required=True)
    ap.add_argument("--k",type=int,default=10)
    ap.add_argument("--split",choices=("development","test"),default="development")
    args=ap.parse_args()
    repo=pathlib.Path(__file__).resolve().parents[3]
    phase3=load_module(repo/"eval/suites/phase3/retrieval_eval.py","phase3_eval")
    phase4=load_module(pathlib.Path(__file__).with_name("compare_rankings.py"),"phase4_compare")
    raw=[row for path in args.chunks for row in phase3.load_chunks(path)]
    source={r["chunk_id"]:r for r in raw}
    rows=phase3.validate_dataset(args.dataset,raw,require_adjudicated=False,allow_auto_labeled=True)
    rows=[r for r in rows if r["split"]==args.split]
    if not rows:
        raise SystemExit("no query rows selected")
    candidate=json.loads(args.candidate.read_text(encoding="utf-8"))
    if candidate.get("mode") not in {"mix","local","hybrid","naive"}:
        raise SystemExit("candidate result has invalid retrieval mode")
    qids={r["query_id"] for r in rows}
    rankings_raw=candidate.get("queries") or {}
    if set(rankings_raw)!=qids:
        raise SystemExit("candidate query IDs do not match selected dataset")
    rankings={}
    invalid=[]
    for qid,ids in rankings_raw.items():
        if not isinstance(ids,list):
            raise SystemExit(f"{qid}: candidate IDs must be a list")
        accepted=[]
        for cid in ids:
            if cid not in source:
                invalid.append({"query_id":qid,"unknown_source_id":str(cid)[:120]})
                continue
            if cid not in accepted:
                accepted.append(cid)
        rankings[qid]=accepted[:args.k]
    if invalid:
        raise SystemExit(f"candidate has {len(invalid)} source IDs not found in frozen corpus")
    latencies=[float(r["elapsed_seconds"]) for r in candidate.get("records",[]) if r.get("http_status")==200]
    if len(latencies)!=len(rows):
        raise SystemExit(f"only {len(latencies)}/{len(rows)} LightRAG queries completed")
    corpus_hash=phase4.corpus_fingerprint(args.chunks)
    dataset_hash=hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    chunks=[phase3.StructuredChunk(
        chunk_id=r["chunk_id"],book_id=r["book_id"],role=r["role"],
        section_path=r.get("section_path",[]),block_ids=r.get("block_ids") or [r["chunk_id"]],
        pages=r["pages"],text=r["text"],
        token_count=max(1,int(r.get("token_count",r.get("approx_word_count",len(r["text"].split()))))),
        embedding_model=r.get("embedding_model","phase1-source/no-embedding")) for r in raw]
    lexical={}
    for row in rows:
        lexical[row["query_id"]]=[c.chunk_id for c,_ in phase3.lexical_search(row["query"],chunks,top_k=args.k)]
    base_run={"retriever":"project_lexical_bm25_style","corpus_sha256":corpus_hash,
        "dataset_sha256":dataset_hash,"rankings":lexical}
    light_run={"retriever":"lightrag_"+candidate["mode"],"corpus_sha256":corpus_hash,
        "dataset_sha256":dataset_hash,"rankings":rankings}
    baseline_path=args.output.with_name(args.output.stem+"-baseline-run.json")
    candidate_path=args.output.with_name(args.output.stem+"-candidate-run.json")
    baseline_path.write_text(json.dumps(base_run,indent=2)+"\n",encoding="utf-8")
    candidate_path.write_text(json.dumps(light_run,indent=2)+"\n",encoding="utf-8")
    paired=phase4.compare(dataset_path=args.dataset,chunk_paths=args.chunks,
        baseline_path=baseline_path,candidate_path=candidate_path,split=args.split,k=args.k,
        allow_auto_labeled=True)
    by_role={}
    for role in sorted({q["role"] for row in rows for q in row.get("qrels",[])}):
        subset=[row for row in rows if any(q["role"]==role for q in row.get("qrels",[]))]
        by_role[role]={
            "query_count":len(subset),
            "baseline":phase3.score_ranked_ids(subset,lexical,ks=(1,3,5,args.k)),
            "candidate":phase3.score_ranked_ids(subset,rankings,ks=(1,3,5,args.k))
        }
    citation_metadata_by_query={
        qid:[{"chunk_id":cid,"book_id":source[cid]["book_id"],"role":source[cid]["role"],
              "pages":source[cid]["pages"],"section_path":source[cid].get("section_path",[])}
             for cid in ids]
        for qid,ids in rankings.items()
    }
    per_type={}
    for typ in sorted({r["question_type"] for r in rows}):
        subset=[r for r in rows if r["question_type"]==typ]
        per_type[typ]={
            "query_count":len(subset),
            "baseline":phase3.score_ranked_ids(subset,lexical,ks=(1,3,5,args.k)),
            "candidate":phase3.score_ranked_ids(subset,rankings,ks=(1,3,5,args.k))
        }
    result={
        "status":paired["status"],"gold_eligible":paired["gold_eligible"],
        "label_provenance":paired["label_provenance"],"mode":candidate["mode"],
        "query_count":len(rows),"top_k":args.k,"corpus_sha256":corpus_hash,
        "dataset_sha256":dataset_hash,"paired_comparison":paired,
        "by_relevant_source_role":by_role,"by_question_type":per_type,
        "citation_metadata_by_query":citation_metadata_by_query,
        "candidate_query_latency_seconds":{"mean":round(statistics.mean(latencies),3),
            "p50":percentile(latencies,.50),"p95":percentile(latencies,.95),
            "max":round(max(latencies),3)},
        "candidate_failures":[r for r in candidate.get("records",[]) if r.get("http_status")!=200],
        "empty_candidate_results":[qid for qid,ids in rankings.items() if not ids],
        "citation_id_validation":{"unknown_source_ids":0,
            "resolved_metadata_fields":["book_id","role","pages","section_path"]},
        "limits":["Qrels are model-auto-validated and are not gold labels.",
            "Per-role slices may overlap when one question has qrels in both roles.",
            "LightRAG references use file_path=source chunk ID and are resolved against the frozen chunk corpus.",
            "Retrieval-only comparison does not adjudicate generated entities or relations."]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"query_count":len(rows),
        "latency":result["candidate_query_latency_seconds"],
        "baseline":paired["baseline"]["metrics"],
        "candidate":paired["candidate"]["metrics"],
        "candidate_failures":len(result["candidate_failures"]),
        "empty_results":len(result["empty_candidate_results"])},ensure_ascii=False))

if __name__=="__main__":
    main()
