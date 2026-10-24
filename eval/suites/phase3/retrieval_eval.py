"""Validate source-grounded retrieval qrels and score a deterministic lexical baseline.

This evaluator intentionally does not create labels. A qrel is eligible for a
gold run only after human adjudication and exact source evidence validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import unicodedata
from pathlib import Path
from typing import Any

from b2c_contracts.models import StructuredChunk
from b2c_indexing import lexical_search


class DatasetError(ValueError):
    """Raised when an evaluation dataset is malformed or unsupported."""


def _quote_matches_source(quote: str, source: str) -> bool:
    """Exact contiguous source match after Unicode NFKC and whitespace normalization."""
    normalized_quote = " ".join(unicodedata.normalize("NFKC", quote).split())
    normalized_source = " ".join(unicodedata.normalize("NFKC", source).split())
    return bool(normalized_quote) and normalized_quote in normalized_source


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise DatasetError(f"{path}:{line_no}: each row must be a JSON object")
        rows.append(value)
    return rows


def course_qa_to_pseudo_qrels(result_path: Path, chunks: list[dict[str, Any]],
                              source_manifest_sha256: str) -> list[dict[str, Any]]:
    """Adapt only final auto-validated Phase 5 citations into non-gold Phase 3 pseudo-qrels."""
    result = json.loads(result_path.read_text(encoding="utf-8"))
    course_status = result.get("status")
    if course_status not in {"final", "failed"}:
        raise DatasetError("course Q/A result has an invalid status")
    by_id = {row["chunk_id"]: row for row in chunks}
    rows = []
    seen_queries: dict[str, dict[str, Any]] = {}
    for scope in result.get("scopes", []):
        if scope.get("status") != "final":
            continue
        for qa in scope.get("items", []):
            if qa.get("status") != "final" or len(qa.get("evidence_chunk_ids", [])) != 1:
                raise DatasetError("each auto-labeled Q/A item needs one final exact citation")
            cid = qa["evidence_chunk_ids"][0]
            source = by_id.get(cid)
            if source is None:
                raise DatasetError(f"generated citation {cid} is outside selected corpus")
            if (qa.get("book_id"), qa.get("role"), qa.get("pages")) != (
                source["book_id"], source["role"], source["pages"]
            ):
                raise DatasetError(f"generated source/page attribution disagrees for {qa.get('qa_id')}")
            quote = qa.get("evidence_quote", "")
            if not quote or not _quote_matches_source(quote, source["text"]):
                raise DatasetError(f"generated quote is not exact source evidence for {qa.get('qa_id')}")
            if qa["qa_id"] in seen_queries:
                if seen_queries[qa["qa_id"]]["qrels"][0]["chunk_id"] != cid:
                    raise DatasetError(f"reused Q/A ID {qa['qa_id']} has conflicting citations")
                continue
            row = {
                "schema_version": "b2c-retrieval-gold/1.0", "query_id": qa["qa_id"],
                "query": qa["question"], "split": "development", "question_type": "direct",
                "query_adjudication": "model_auto_validated", "label_origin": "phase5_course_qa_auto_validated",
                "generation_scope_id": scope["scope_id"], "generation_model_id": qa["model_id"],
                "generation_prompt_version": qa["prompt_version"],
                "generation_course_status": course_status,
                "source_manifest_sha256": source_manifest_sha256,
                "qrels": [{"chunk_id": cid, "relevance": 3, "book_id": source["book_id"],
                    "role": source["role"], "pages": source["pages"], "evidence_quote": quote,
                    "adjudication": "model_auto_validated"}],
            }
            rows.append(row)
            seen_queries[qa["qa_id"]] = row
    if not rows:
        raise DatasetError("course Q/A result contains no final items")
    return rows


def load_chunks(path: Path) -> list[dict[str, Any]]:
    rows = _jsonl(path)
    seen: set[str] = set()
    for row in rows:
        required = {"chunk_id", "book_id", "role", "pages", "text"}
        missing = required - row.keys()
        if missing:
            raise DatasetError(f"chunk is missing fields: {sorted(missing)}")
        if row["chunk_id"] in seen:
            raise DatasetError(f"duplicate chunk_id: {row['chunk_id']}")
        seen.add(row["chunk_id"])
        if row["role"] not in {"spine", "supplementary"}:
            raise DatasetError(f"invalid role for {row['chunk_id']}")
        if not isinstance(row["pages"], list) or not row["pages"] or any(
            not isinstance(page, int) or page < 1 for page in row["pages"]
        ):
            raise DatasetError(f"invalid physical page list for {row['chunk_id']}")
    if not rows:
        raise DatasetError("chunk corpus is empty")
    return rows


def validate_dataset(path: Path, chunks: list[dict[str, Any]], *, require_adjudicated: bool = True,
                     allow_auto_labeled: bool = False,
                     allow_system_evaluated: bool = False) -> list[dict[str, Any]]:
    rows = _jsonl(path)
    if not rows:
        raise DatasetError("evaluation dataset is empty; no labels were invented")
    by_id = {row["chunk_id"]: row for row in chunks}
    ids: set[str] = set()
    for row in rows:
        required = {"schema_version", "query_id", "query", "split", "question_type", "qrels", "query_adjudication"}
        missing = required - row.keys()
        if missing:
            raise DatasetError(f"query row is missing fields: {sorted(missing)}")
        if row["schema_version"] != "b2c-retrieval-gold/1.0":
            raise DatasetError(f"unsupported schema_version for {row.get('query_id')}")
        qid = row["query_id"]
        if not isinstance(qid, str) or not qid.strip() or qid in ids:
            raise DatasetError(f"query_id must be nonempty and unique: {qid!r}")
        ids.add(qid)
        if not isinstance(row["query"], str) or len(row["query"].strip()) < 8:
            raise DatasetError(f"query {qid} must contain at least 8 non-space characters")
        if row["split"] not in {"development", "test"}:
            raise DatasetError(f"query {qid} split must be development or test")
        if row["question_type"] not in {
            "direct", "paraphrase", "definition", "formula", "example",
            "cross_book", "unanswerable",
        }:
            raise DatasetError(f"query {qid} has unsupported question_type")
        allowed_labels = {"human_adjudicated", "pending"}
        if allow_auto_labeled:
            allowed_labels.add("model_auto_validated")
        if allow_system_evaluated:
            allowed_labels.add("system_machine_evaluated")
        if row["query_adjudication"] not in allowed_labels:
            raise DatasetError(f"query {qid} has unsupported query_adjudication")
        if row["query_adjudication"] == "system_machine_evaluated":
            review = row.get("system_review") or {}
            if (not allow_system_evaluated or row.get("gold_eligible") is not False
                    or review.get("model_id") is None or review.get("prompt_sha256") is None
                    or review.get("scope") != "machine semantic answerability/completeness and exact source citation; not human gold"):
                raise DatasetError(f"query {qid} has invalid/missing machine review provenance")
        if require_adjudicated and row["query_adjudication"] != "human_adjudicated":
            raise DatasetError(f"query {qid} is not human-adjudicated")
        if not isinstance(row["qrels"], list):
            raise DatasetError(f"query {qid} qrels must be a list")
        if row["question_type"] == "unanswerable":
            if row["qrels"]:
                raise DatasetError(f"unanswerable query {qid} must have no relevant qrels")
            if not row.get("unanswerable_reason"):
                raise DatasetError(f"unanswerable query {qid} needs unanswerable_reason")
            continue
        if not row["qrels"]:
            raise DatasetError(f"answerable query {qid} needs at least one qrel")
        qrel_ids: set[str] = set()
        relevant_spans = []
        for qrel in row["qrels"]:
            fields = {"chunk_id", "relevance", "book_id", "role", "pages", "evidence_quote", "adjudication"}
            absent = fields - qrel.keys()
            if absent:
                raise DatasetError(f"qrel in {qid} is missing fields: {sorted(absent)}")
            cid = qrel["chunk_id"]
            if cid in qrel_ids:
                raise DatasetError(f"duplicate qrel {cid} in {qid}")
            qrel_ids.add(cid)
            source = by_id.get(cid)
            if source is None:
                raise DatasetError(f"qrel {cid} in {qid} is not present in the corpus")
            if (qrel["book_id"], qrel["role"], qrel["pages"]) != (
                source["book_id"], source["role"], source["pages"]
            ):
                raise DatasetError(f"qrel source/page attribution disagrees with corpus for {qid}/{cid}")
            quote = qrel["evidence_quote"]
            if (not isinstance(quote, str) or len(quote.strip()) < 12
                    or not _quote_matches_source(quote, source["text"])):
                raise DatasetError(f"qrel {qid}/{cid} needs an exact source-grounded evidence_quote")
            if not isinstance(qrel["relevance"], int) or qrel["relevance"] not in {1, 2, 3}:
                raise DatasetError(f"qrel {qid}/{cid} relevance must be an integer from 1 to 3")
            if allow_system_evaluated:
                allowed_labels.add("system_machine_evaluated")
            if qrel["adjudication"] not in allowed_labels:
                raise DatasetError(f"qrel {qid}/{cid} has unsupported adjudication")
            if qrel["adjudication"] == "system_machine_evaluated" and row["query_adjudication"] != "system_machine_evaluated":
                raise DatasetError(f"qrel {qid}/{cid} system label lacks system-reviewed query provenance")
            if require_adjudicated and qrel["adjudication"] != "human_adjudicated":
                raise DatasetError(f"qrel {qid}/{cid} is not human-adjudicated")
            if qrel["relevance"] > 0:
                relevant_spans.append((qrel["book_id"], qrel["pages"]))
        if row["question_type"] == "cross_book" and len({book for book, _ in relevant_spans}) < 2:
            raise DatasetError(f"cross_book query {qid} must have relevant evidence in two books")
    if len(ids) != len(rows):
        raise DatasetError("query IDs must be unique")
    return rows


def _dcg(gains: list[int]) -> float:
    return sum((2**gain - 1) / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))


def score_ranked_ids(query_rows: list[dict[str, Any]], ranked_ids: dict[str, list[str]], *, ks=(1, 3, 5, 10)) -> dict[str, Any]:
    """Calculate retrieval and provenance scores from precomputed rankings."""
    results: dict[str, Any] = {"queries": len(query_rows), "metrics": {}}
    for k in ks:
        recalls, reciprocal_ranks, ndcgs = [], [], []
        source_correct = source_total = page_correct = page_total = 0
        unanswerable = unanswerable_false_positive = 0
        for row in query_rows:
            qrels = {q["chunk_id"]: q for q in row["qrels"] if q["relevance"] > 0}
            ranked = ranked_ids.get(row["query_id"], [])[:k]
            if row["question_type"] == "unanswerable":
                unanswerable += 1
                unanswerable_false_positive += bool(ranked)
                continue
            if not qrels:
                continue
            found = [cid for cid in ranked if cid in qrels]
            recalls.append(len(set(found)) / len(qrels))
            rr = next((1 / rank for rank, cid in enumerate(ranked, 1) if cid in qrels), 0.0)
            reciprocal_ranks.append(rr)
            gains = [qrels[cid]["relevance"] if cid in qrels else 0 for cid in ranked]
            ideal = sorted((q["relevance"] for q in qrels.values()), reverse=True)[:k]
            ndcgs.append(_dcg(gains) / _dcg(ideal) if _dcg(ideal) else 0.0)
            for cid in ranked:
                if cid not in qrels:
                    continue
                qrel = qrels[cid]
                source_total += 1
                if qrel["book_id"] and qrel["role"]:
                    source_correct += 1
                page_total += 1
                if qrel["pages"]:
                    page_correct += 1
        results["metrics"][f"recall@{k}"] = sum(recalls) / len(recalls) if recalls else None
        results["metrics"][f"mrr@{k}"] = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else None
        results["metrics"][f"ndcg@{k}"] = sum(ndcgs) / len(ndcgs) if ndcgs else None
        results["metrics"][f"source_attribution_integrity@{k}"] = source_correct / source_total if source_total else None
        results["metrics"][f"page_attribution_integrity@{k}"] = page_correct / page_total if page_total else None
        results["metrics"][f"attributed_relevant_hits@{k}"] = source_total
        results["metrics"][f"unanswerable_false_positive_rate@{k}"] = (
            unanswerable_false_positive / unanswerable if unanswerable else None
        )
    return results


def evaluate(query_rows: list[dict[str, Any]], raw_chunks: list[dict[str, Any]], *, split: str, k: int) -> dict[str, Any]:
    chunks = [StructuredChunk(
        chunk_id=row["chunk_id"], book_id=row["book_id"], role=row["role"],
        section_path=row.get("section_path", []), block_ids=row.get("block_ids") or [row["chunk_id"]],
        pages=row["pages"], text=row["text"],
        token_count=max(1, int(row.get("token_count", row.get("approx_word_count", len(row["text"].split()))))),
        embedding_model=row.get("embedding_model", "phase1-source-chunk/no-embedding"),
    ) for row in raw_chunks]
    selected = [row for row in query_rows if row["split"] == split]
    ranked = {
        row["query_id"]: [chunk.chunk_id for chunk, _ in lexical_search(row["query"], chunks, top_k=k)]
        for row in selected
    }
    return {"split": split, "retriever": "lexical_bm25_style", **score_ranked_ids(selected, ranked, ks=(1, 3, 5, k))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, nargs="+", required=True, help="one or more JSONL files of source-linked chunks")
    parser.add_argument("--source-manifest", type=Path, help="optional frozen source manifest; checks listed chunk hashes and rights status")
    parser.add_argument("--dataset", type=Path, help="Human-adjudicated retrieval qrels JSONL")
    parser.add_argument("--course-qa-result", type=Path,
                        help="Phase 5 final Q/A JSON; derives explicitly non-gold pseudo-qrels")
    parser.add_argument("--split", choices=("development", "test"), default="test")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--allow-pending", action="store_true", help="diagnostic only; never a gold result")
    parser.add_argument("--allow-auto-labeled", action="store_true",
                        help="allow machine-validated course-Q/A citations as pseudo-qrels; diagnostic only")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if bool(args.dataset) == bool(args.course_qa_result):
        parser.error("provide exactly one of --dataset or --course-qa-result")
    if args.course_qa_result and not args.source_manifest:
        parser.error("--course-qa-result requires --source-manifest")
    if args.k < 1:
        parser.error("--k must be positive")
    raw_chunks: list[dict[str, Any]] = []
    chunk_paths: list[Path] = []
    for chunk_path in args.chunks:
        chunk_paths.append(chunk_path)
        raw_chunks.extend(load_chunks(chunk_path))
    if len({row["chunk_id"] for row in raw_chunks}) != len(raw_chunks):
        raise DatasetError("chunk_id collides across corpus files")
    manifest_hash = None
    if args.source_manifest:
        manifest_hash = hashlib.sha256(args.source_manifest.read_bytes()).hexdigest()
        source_manifest = json.loads(args.source_manifest.read_text(encoding="utf-8"))
        declared = {Path(source["chunk_file"]).name: source for source in source_manifest.get("sources", [])}
        selected_names = {path.name for path in chunk_paths}
        if not selected_names <= declared.keys():
            raise DatasetError("one or more chunk files are not listed in the source manifest")
        for path in chunk_paths:
            source = declared[path.name]
            if source.get("rights_status") not in {"cleared", "internal_test_only"}:
                raise DatasetError(f"source rights status is not eligible: {path.name}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != source.get("chunk_sha256"):
                raise DatasetError(f"chunk hash does not match source manifest: {path.name}")
        if args.dataset:
            for row in validate_dataset(args.dataset, raw_chunks,
                    require_adjudicated=not (args.allow_pending or args.allow_auto_labeled),
                    allow_auto_labeled=args.allow_auto_labeled):
                if row.get("source_manifest_sha256") != manifest_hash:
                    raise DatasetError(f"query {row['query_id']} does not pin the selected source manifest hash")
        elif not manifest_hash:
            raise DatasetError("source manifest is required for generated Q/A pseudo-qrels")
    if args.course_qa_result:
        qas = course_qa_to_pseudo_qrels(args.course_qa_result, raw_chunks, manifest_hash)
        allow_auto_labeled = True
        dataset_hash = hashlib.sha256(args.course_qa_result.read_bytes()).hexdigest()
    else:
        qas = validate_dataset(args.dataset, raw_chunks,
            require_adjudicated=not (args.allow_pending or args.allow_auto_labeled),
            allow_auto_labeled=args.allow_auto_labeled)
        allow_auto_labeled = args.allow_auto_labeled
        dataset_hash = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    report = {
        "dataset": str(args.dataset) if args.dataset else str(args.course_qa_result),
        "dataset_sha256": dataset_hash,
        "corpus": [str(path) for path in chunk_paths],
        "corpus_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in chunk_paths},
        "source_manifest": str(args.source_manifest) if args.source_manifest else None,
        "source_manifest_sha256": manifest_hash,
        "gold_eligible": not (args.allow_pending or allow_auto_labeled),
        "label_provenance": ("model_auto_validated_pseudo_qrels" if allow_auto_labeled else
                             ("pending_diagnostic" if args.allow_pending else "human_adjudicated")),
        "result": evaluate(qas, raw_chunks, split=args.split, k=args.k),
        "limits": (["generated citation is one known relevant chunk, not an exhaustive relevance set",
                    "metrics are self-consistency diagnostics, not gold retrieval quality"] if allow_auto_labeled else []) +
                  ["lexical baseline only", "not a Phase 3 gate by itself"],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
