"""Bounded 50-query local retrieval comparison using auto-labeled pseudo-qrels."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any
import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoModelForSequenceClassification, AutoTokenizer

def load_eval(path: Path):
    spec = importlib.util.spec_from_file_location("phase3_eval", path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()

def sha256_tree(path: Path) -> dict[str, str]:
    return {str(file.relative_to(path)): sha256_file(file)
            for file in sorted(path.rglob("*")) if file.is_file()}

def encode_one(text: str, tokenizer, model, *, max_length: int):
    full = tokenizer(text, truncation=False)
    token_count = len(full["input_ids"])
    inputs = tokenizer(text, truncation=True, max_length=max_length, return_tensors="pt")
    with torch.inference_mode():
        output = model(**inputs, return_dict=True)
        vector = F.normalize(output.last_hidden_state[:, 0], p=2, dim=1).cpu()[0]
    return vector, token_count > max_length, token_count

def rank_reranker(query: str, candidates: list[dict[str, Any]], tokenizer, model, *, max_length: int):
    started = time.perf_counter()
    scores = {}
    truncated = 0
    for row in candidates:
        pair = tokenizer(query, row["text"], truncation=False)
        truncated += len(pair["input_ids"]) > max_length
        inputs = tokenizer(query, row["text"], truncation="only_second", max_length=max_length,
                           return_tensors="pt")
        with torch.inference_mode():
            value = float(model(**inputs, return_dict=True).logits.reshape(-1)[0].cpu())
        scores[row["chunk_id"]] = value
    ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
    return ordered, {"seconds": time.perf_counter() - started, "pairs": len(candidates),
                     "truncated_count": truncated}

def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    scores = {}
    for ranking in rankings:
        for pos, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1/(k+pos)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))

def metric_rows(rows, rankings, *, role=None):
    recalls, mrrs, ndcgs = [], [], []
    for row in rows:
        qrels = {q["chunk_id"]: q["relevance"] for q in row["qrels"]
                 if q["relevance"] > 0 and (role is None or q["role"] == role)}
        if not qrels: continue
        ranked = rankings[row["query_id"]][:10]
        hits = [cid for cid in ranked if cid in qrels]
        recalls.append(len(set(hits))/len(qrels))
        mrrs.append(next((1.0/pos for pos,cid in enumerate(ranked,1) if cid in qrels),0.0))
        def dcg(gains): return sum((2**gain-1)/math.log2(pos+1) for pos,gain in enumerate(gains,1))
        actual = dcg([qrels.get(cid,0) for cid in ranked])
        ideal = dcg(sorted(qrels.values(), reverse=True)[:10])
        ndcgs.append(actual/ideal if ideal else 0.0)
    def mean(values): return sum(values)/len(values) if values else None
    return {"queries":len(recalls), "recall@10":mean(recalls), "mrr@10":mean(mrrs),
            "ndcg@10":mean(ndcgs)}

def complete_evidence_rate(rows, rankings):
    evaluated=[]
    for row in rows:
        relevant={q["chunk_id"] for q in row["qrels"] if q["relevance"]>0}
        if relevant:
            evaluated.append(relevant <= set(rankings[row["query_id"]][:10]))
    return {"queries":len(evaluated),"rate":sum(evaluated)/len(evaluated) if evaluated else None}

def summarize_latency(values):
    ordered=sorted(values)
    if not ordered: return {"queries":0,"mean_seconds":None,"p50_seconds":None,"p95_seconds":None}
    return {"queries":len(ordered),"mean_seconds":sum(ordered)/len(ordered),
            "p50_seconds":statistics.median(ordered),
            "p95_seconds":ordered[max(0,math.ceil(.95*len(ordered))-1)]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,required=True)
    parser.add_argument("--chunks",type=Path,nargs="+",required=True)
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--dataset",type=Path,required=True)
    parser.add_argument("--models-root",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--max-length",type=int,default=2048)
    parser.add_argument("--allow-system-evaluated",action="store_true",
        help="accept independently machine-evaluated, explicitly non-gold labels")
    args=parser.parse_args()
    torch.set_num_threads(min(20,torch.get_num_threads()))
    evaluation=load_eval(args.repo/"eval/suites/phase3/retrieval_eval.py")
    manifest_hash=sha256_file(args.manifest)
    raw_chunks=[]
    for file in args.chunks: raw_chunks.extend(evaluation.load_chunks(file))
    if len(raw_chunks)!=37: raise ValueError(f"expected exact 37-chunk frozen corpus, found {len(raw_chunks)}")
    manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
    expected={item["chunk_file"]:item["chunk_sha256"] for item in manifest["sources"]}
    for file in args.chunks:
        if expected.get(file.name)!=sha256_file(file): raise ValueError(f"frozen chunk hash mismatch: {file.name}")
    rows=evaluation.validate_dataset(args.dataset,raw_chunks,require_adjudicated=False,
        allow_auto_labeled=True,allow_system_evaluated=args.allow_system_evaluated)
    expected_query_count=50
    if len(rows)!=expected_query_count and not (args.allow_system_evaluated and 1<=len(rows)<expected_query_count):
        raise ValueError(f"expected {expected_query_count} labeled queries (or a non-empty system-evaluated diagnostic subset), found {len(rows)}")
    label_types={row.get("query_adjudication") for row in rows}
    if args.allow_system_evaluated:
        if label_types!={"system_machine_evaluated"}:
            raise ValueError("system evaluation must contain only fully machine-adjudicated labels")
        if any(q.get("adjudication")!="system_machine_evaluated" for row in rows for q in row["qrels"]):
            raise ValueError("system qrels lack machine adjudication")
    else:
        if label_types!={"model_auto_validated"} or any(q.get("adjudication")!="model_auto_validated" for row in rows for q in row["qrels"]):
            raise ValueError("dataset labels are not the expected model_auto_validated diagnostics")
    if any(row.get("source_manifest_sha256")!=manifest_hash for row in rows):
        raise ValueError("one or more rows refer to a different source manifest")
    if any(row.get("question_type")=="unanswerable" for row in rows) and not args.allow_system_evaluated:
        raise ValueError("unanswerable queries require explicitly machine-reviewed labels")
    corpus_hashes={str(file):sha256_file(file) for file in args.chunks}
    dataset_hash=sha256_file(args.dataset)
    from b2c_contracts import StructuredChunk
    from b2c_indexing import lexical_search
    chunks=[StructuredChunk(chunk_id=r["chunk_id"],book_id=r["book_id"],role=r["role"],
        section_path=r.get("section_path",[]),block_ids=r.get("block_ids") or [r["chunk_id"]],
        pages=r["pages"],text=r["text"],token_count=max(1,int(r.get("token_count",len(r["text"].split())))),
        embedding_model="phase1-source/no-vector") for r in raw_chunks]
    dense_dir=args.models_root/"bge-m3"
    reranker_dir=args.models_root/"bge-reranker-v2-m3"
    dense_tokenizer=AutoTokenizer.from_pretrained(dense_dir,local_files_only=True)
    dense_config=AutoConfig.from_pretrained(dense_dir,local_files_only=True)
    dense_tokenizer_max=dense_tokenizer.model_max_length
    dense_position_max=dense_config.max_position_embeddings
    dense_model=AutoModel.from_pretrained(dense_dir,local_files_only=True).eval()
    max_dense=min(args.max_length,dense_tokenizer.model_max_length)
    corpus_started=time.perf_counter()
    corpus_vectors=[]; corpus_counts=[]; corpus_truncated=0
    for item in raw_chunks:
        vec,truncated,count=encode_one(item["text"],dense_tokenizer,dense_model,max_length=max_dense)
        corpus_vectors.append(vec); corpus_counts.append(count); corpus_truncated+=truncated
    corpus_vectors=torch.stack(corpus_vectors)
    corpus_encode_seconds=time.perf_counter()-corpus_started
    lexical_rankings={}; dense_rankings={}; hybrid_rankings={}
    method_seconds={"lexical":[],"dense":[],"hybrid_rrf":[]}
    hybrid_seconds_by_query={}
    query_truncated=0; query_counts=[]; dense_query_encoding_seconds=0.0
    by_id={item["chunk_id"]:item for item in raw_chunks}
    for row in rows:
        qid=row["query_id"]
        lex_started=time.perf_counter()
        lexical=lexical_search(row["query"],chunks,top_k=len(chunks))
        lex_ids=[chunk.chunk_id for chunk,_ in lexical]
        lexical_seconds=time.perf_counter()-lex_started
        dense_started=time.perf_counter()
        qvec,truncated,count=encode_one(row["query"],dense_tokenizer,dense_model,max_length=max_dense)
        qenc_seconds=time.perf_counter()-dense_started
        query_truncated+=truncated; query_counts.append(count); dense_query_encoding_seconds+=qenc_seconds
        sims=torch.mv(corpus_vectors,qvec)
        dense_ids=[raw_chunks[i]["chunk_id"] for i in sorted(range(len(raw_chunks)),
            key=lambda i:(-float(sims[i]),raw_chunks[i]["chunk_id"]))]
        dense_seconds=time.perf_counter()-dense_started
        fuse_started=time.perf_counter()
        hybrid_ids=rrf(lex_ids,dense_ids)
        fuse_seconds=time.perf_counter()-fuse_started
        lexical_rankings[qid]=lex_ids; dense_rankings[qid]=dense_ids; hybrid_rankings[qid]=hybrid_ids
        method_seconds["lexical"].append(lexical_seconds)
        method_seconds["dense"].append(dense_seconds)
        hybrid_seconds=lexical_seconds+dense_seconds+fuse_seconds
        method_seconds["hybrid_rrf"].append(hybrid_seconds)
        hybrid_seconds_by_query[qid]=hybrid_seconds
    dense_model.cpu(); del dense_model; del dense_tokenizer
    reranker_tokenizer=AutoTokenizer.from_pretrained(reranker_dir,local_files_only=True)
    reranker_config=AutoConfig.from_pretrained(reranker_dir,local_files_only=True)
    reranker_tokenizer_max=reranker_tokenizer.model_max_length
    reranker_position_max=reranker_config.max_position_embeddings
    reranker=AutoModelForSequenceClassification.from_pretrained(reranker_dir,local_files_only=True).eval()
    reranked={}; rerank_seconds=[]; rerank_pairs=0; rerank_truncated=0
    for row in rows:
        qid=row["query_id"]
        candidate_ids=hybrid_rankings[qid][:10]
        candidates=[by_id[cid] for cid in candidate_ids]
        ranked,timing=rank_reranker(row["query"],candidates,reranker_tokenizer,reranker,
                                    max_length=min(args.max_length,reranker_tokenizer.model_max_length))
        reranked[qid]=ranked; rerank_seconds.append(timing["seconds"])
        rerank_pairs+=timing["pairs"]; rerank_truncated+=timing["truncated_count"]
        method_seconds.setdefault("hybrid_rrf_bge_reranker_top10",[]).append(
            hybrid_seconds_by_query[qid]+timing["seconds"])
    reranker.cpu(); del reranker; del reranker_tokenizer
    if corpus_truncated or query_truncated or rerank_truncated:
        raise ValueError(f"truncation detected: corpus={corpus_truncated}, queries={query_truncated}, reranker_pairs={rerank_truncated}")
    rankings={"lexical":lexical_rankings,"dense_bge_m3":dense_rankings,
        "hybrid_rrf_lexical_dense":hybrid_rankings,
        "hybrid_rrf_top10_bge_reranker_v2_m3":reranked}
    roles=sorted({q["role"] for row in rows for q in row["qrels"]})
    metrics={method:{"overall":metric_rows(rows,ranking),
        "complete_evidence@10":complete_evidence_rate(rows,ranking),
        "by_source_role":{role:metric_rows(rows,ranking,role=role) for role in roles}}
        for method,ranking in rankings.items()}
    report={
        "evaluation_kind":("50-query independent system-evaluated semantic qrels; not human gold" if args.allow_system_evaluated else "50-query model_auto_validated pseudo-qrels self-consistency diagnostic; not human gold"),
        "query_count":len(rows),"answerable_query_count":sum(bool(row["qrels"]) for row in rows),
        "dataset_sha256":dataset_hash,"gold_eligible":False,
        "label_provenance":("system_machine_evaluated" if args.allow_system_evaluated else "model_auto_validated_pseudo_qrels"),
        "system_judge_models":sorted({row["system_review"]["model_id"] for row in rows if row.get("system_review")}),
        "system_judge_prompt_sha256":sorted({row["system_review"]["prompt_sha256"] for row in rows if row.get("system_review")}),
        "label_validation":"all queries and qrels validated against exact source spans and attribution",
        "source_manifest_sha256":manifest_hash,"corpus_chunk_count":len(raw_chunks),
        "corpus_sha256":corpus_hashes,
        "source_role_query_counts":{role:sum(any(q["role"]==role for q in row["qrels"]) for row in rows) for role in roles},
        "models":{
            "dense":{"id":"BAAI/bge-m3","local_dir":str(dense_dir),"max_input_tokens":max_dense,
                "tokenizer_max_length":dense_tokenizer_max,
                "max_position_embeddings":dense_position_max,
                "pooling":"CLS + L2 normalization","sha256_by_file":sha256_tree(dense_dir)},
            "reranker":{"id":"BAAI/bge-reranker-v2-m3","local_dir":str(reranker_dir),
                "max_input_tokens":min(args.max_length,reranker_tokenizer_max),
                "tokenizer_max_length":reranker_tokenizer_max,
                "max_position_embeddings":reranker_position_max,
                "sha256_by_file":sha256_tree(reranker_dir)},
            "transformers":__import__("transformers").__version__,"torch":torch.__version__,
            "device":"cpu","threads":torch.get_num_threads()},
        "reranker_per_query_seconds":rerank_seconds,
        "encoding":{"corpus":{"chunks":len(raw_chunks),"seconds":corpus_encode_seconds,
            "max_length":max_dense,"truncated_count":corpus_truncated,"token_count_max":max(corpus_counts)},
            "queries":{"count":len(rows),"seconds":dense_query_encoding_seconds,
            "max_length":max_dense,"truncated_count":query_truncated,"token_count_max":max(query_counts)},
            "reranker":{"pairs":rerank_pairs,"candidates_per_query":10,"seconds":sum(rerank_seconds),
            "max_length":min(args.max_length,reranker_tokenizer_max),
            "truncated_count":rerank_truncated}},
        "metrics_at_10":metrics,
        "latency_per_query_seconds":{name:summarize_latency(values) for name,values in method_seconds.items()},
        "limits":["machine-evaluated labels are not human gold; independent model judgments can still be wrong",
            "reference qrels may not enumerate every relevant chunk; report source coverage and exact evidence validation",
            "no human-adjudicated query set; results are a research benchmark, not commercial certification",
            "corpus encoding/index-build latency is reported separately from query latency",
            "network disabled; no providers called"],
    }
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
