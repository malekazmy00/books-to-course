"""Offline dense/hybrid/reranker retrieval diagnostic using the frozen local BGE weights."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load helper module {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _encode(texts: list[str], tokenizer, model, *, batch_size: int, max_length: int) -> tuple[torch.Tensor, dict[str, Any]]:
    started = time.perf_counter()
    vectors = []
    truncated = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        full = tokenizer(batch, padding=False, truncation=False)
        truncated += sum(len(ids) > max_length for ids in full["input_ids"])
        inputs = tokenizer(batch, padding=True, truncation=True, max_length=max_length, return_tensors="pt")
        with torch.inference_mode():
            output = model(**inputs, return_dict=True)
            cls = output.last_hidden_state[:, 0]
            vectors.append(F.normalize(cls, p=2, dim=1).cpu())
    return torch.cat(vectors, dim=0), {"seconds": round(time.perf_counter() - started, 3),
        "count": len(texts), "max_length": max_length, "truncated_count": truncated}


def _rrf(*rankings: list[str], k0: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (k0 + rank)
    return sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))


def _ranker_scores(query: str, rows: list[dict[str, Any]], tokenizer, model,
                   *, max_length: int, batch_size: int) -> tuple[dict[str, float], dict[str, Any]]:
    started = time.perf_counter()
    scores: dict[str, float] = {}
    truncated = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        full = tokenizer([query] * len(batch), [row["text"] for row in batch],
            padding=False, truncation=False)
        truncated += sum(len(ids) > max_length for ids in full["input_ids"])
        inputs = tokenizer([query] * len(batch), [row["text"] for row in batch], padding=True,
            truncation="only_second", max_length=max_length, return_tensors="pt")
        with torch.inference_mode():
            logits = model(**inputs, return_dict=True).logits.reshape(-1).cpu().tolist()
        scores.update({row["chunk_id"]: float(score) for row, score in zip(batch, logits)})
    return scores, {"seconds": round(time.perf_counter() - started, 3), "pairs": len(rows),
        "max_length": max_length, "truncated_count": truncated}


def main() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, nargs="+", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--qa-result", type=Path, required=True)
    parser.add_argument("--models-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=1)
    args = parser.parse_args()
    torch.set_num_threads(min(20, os.cpu_count() or 1))
    eval_module = _module(args.repo / "eval/suites/phase3/retrieval_eval.py", "phase3_eval")
    manifest_hash = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    raw_chunks = []
    for path in args.chunks:
        raw_chunks.extend(eval_module.load_chunks(path))
    if len({row["chunk_id"] for row in raw_chunks}) != len(raw_chunks):
        raise ValueError("chunk IDs collide across selected corpus files")
    qas = eval_module.course_qa_to_pseudo_qrels(args.qa_result, raw_chunks, manifest_hash)
    if not qas:
        raise ValueError("no automatically validated Q/A citations available for diagnostic evaluation")
    lexical = _module(args.repo / "packages/indexing/src/b2c_indexing/retrieval.py", "b2c_lexical")
    from b2c_contracts import StructuredChunk
    chunks = [StructuredChunk(chunk_id=row["chunk_id"], book_id=row["book_id"], role=row["role"],
        section_path=row.get("section_path", []), block_ids=row.get("block_ids") or [row["chunk_id"]],
        pages=row["pages"], text=row["text"], token_count=max(1, int(row.get("token_count", len(row["text"].split())))),
        embedding_model="phase1-source/no-vector") for row in raw_chunks]
    model_root = args.models_root
    dense_dir = model_root / "bge-m3"
    reranker_dir = model_root / "bge-reranker-v2-m3"
    tokenizer = AutoTokenizer.from_pretrained(dense_dir, local_files_only=True)
    dense_model = AutoModel.from_pretrained(dense_dir, local_files_only=True).eval()
    texts = [row["text"] for row in raw_chunks]
    dense_vectors, chunk_encoding = _encode(texts, tokenizer, dense_model,
        batch_size=args.batch_size, max_length=args.max_length)
    query_vectors, query_encoding = _encode([row["query"] for row in qas], tokenizer, dense_model,
        batch_size=args.batch_size, max_length=args.max_length)
    dense_model.cpu(); del dense_model
    del tokenizer
    dense_rankings: dict[str, list[str]] = {}
    lexical_rankings: dict[str, list[str]] = {}
    hybrid_rankings: dict[str, list[str]] = {}
    ranking_seconds = {"dense_similarity": 0.0, "lexical": 0.0, "rrf": 0.0}
    by_id = {row["chunk_id"]: row for row in raw_chunks}
    for index, query_row in enumerate(qas):
        started = time.perf_counter()
        sims = torch.mv(dense_vectors, query_vectors[index])
        dense_order = sorted(range(len(raw_chunks)), key=lambda i: (-float(sims[i]), raw_chunks[i]["chunk_id"]))
        dense_ids = [raw_chunks[i]["chunk_id"] for i in dense_order]
        ranking_seconds["dense_similarity"] += time.perf_counter() - started
        started = time.perf_counter()
        scored = lexical.lexical_search(query_row["query"], chunks, top_k=len(chunks))
        lexical_ids = [chunk.chunk_id for chunk, _ in scored]
        ranking_seconds["lexical"] += time.perf_counter() - started
        started = time.perf_counter()
        hybrid_ids = _rrf(lexical_ids, dense_ids)
        ranking_seconds["rrf"] += time.perf_counter() - started
        qid = query_row["query_id"]
        dense_rankings[qid], lexical_rankings[qid], hybrid_rankings[qid] = dense_ids, lexical_ids, hybrid_ids
    rerank_tokenizer = AutoTokenizer.from_pretrained(reranker_dir, local_files_only=True)
    reranker = AutoModelForSequenceClassification.from_pretrained(reranker_dir, local_files_only=True).eval()
    reranked: dict[str, list[str]] = {}
    rerank_timings = []
    for query_row in qas:
        hybrid_ids = hybrid_rankings[query_row["query_id"]]
        candidates = [by_id[cid] for cid in hybrid_ids]
        scores, timing = _ranker_scores(query_row["query"], candidates, rerank_tokenizer, reranker,
            max_length=args.max_length, batch_size=args.batch_size)
        reranked[query_row["query_id"]] = sorted(scores, key=lambda cid: (-scores[cid], cid))
        rerank_timings.append(timing)
    reranker.cpu(); del reranker; del rerank_tokenizer
    if chunk_encoding["truncated_count"] or query_encoding["truncated_count"] or any(
            item["truncated_count"] for item in rerank_timings):
        raise ValueError("evaluation inputs were truncated; increase --max-length before comparing rankers")
    rankings = {"lexical": lexical_rankings, "dense_bge_m3": dense_rankings,
        "hybrid_rrf_lexical_dense": hybrid_rankings, "hybrid_rrf_then_bge_reranker_v2_m3": reranked}
    report = {
        "evaluation_kind": "one-query generated-citation self-consistency diagnostic; not human gold",
        "course_qa_result_status": json.loads(args.qa_result.read_text(encoding="utf-8")).get("status"),
        "query_count": len(qas), "query_ids": [row["query_id"] for row in qas],
        "label_provenance": "model_auto_validated_pseudo_qrels", "gold_eligible": False,
        "source_manifest_sha256": manifest_hash,
        "corpus_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in args.chunks},
        "models": {"dense": str(dense_dir), "dense_pooling": "CLS + L2 normalization (from local SentenceTransformers modules.json)",
            "reranker": str(reranker_dir), "transformers": __import__("transformers").__version__,
            "torch": torch.__version__, "device": "cpu", "threads": torch.get_num_threads()},
        "encoding": {"chunks": chunk_encoding, "queries": query_encoding},
        "ranking_seconds": {key: round(value, 4) for key, value in ranking_seconds.items()},
        "reranker_timings": rerank_timings,
        "metrics": {name: eval_module.score_ranked_ids(qas, ranking, ks=(1, 3, 5, 10, len(raw_chunks)))
                    for name, ranking in rankings.items()},
        "citation_ranks": {name: {qid: next((rank for rank, cid in enumerate(order, 1)
            if cid in {qrel["chunk_id"] for q in qas if q["query_id"] == qid for qrel in q["qrels"]}), None)
            for qid, order in ranking.items()} for name, ranking in rankings.items()},
        "limits": ["one generated Q/A citation only; qrels are not exhaustive or independent",
            "Recall/MRR/nDCG here are self-consistency diagnostics, not retrieval-quality estimates",
            "no unanswerable queries, no human-adjudicated relevance labels", "no external model/network calls"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
