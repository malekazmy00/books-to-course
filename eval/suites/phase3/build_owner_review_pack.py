#!/usr/bin/env python3
"""Build a human-review pack without promoting model labels to gold.

The output stays on the B2C data volume because it contains derived book text.
It is deliberately not a valid retrieval-gold JSONL file until reviewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def exact_match(quote: str, source: str) -> bool:
    normalize = lambda value: " ".join(unicodedata.normalize("NFKC", value).split())
    return normalize(quote) in normalize(source)


def build_pack(source_manifest: Path, query_path: Path, chunk_paths: list[Path]) -> dict[str, Any]:
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    manifest_hash = sha256(source_manifest)
    chunks = [item for path in chunk_paths for item in jsonl(path)]
    by_id = {item["chunk_id"]: item for item in chunks}
    if len(chunks) != 37 or len(by_id) != len(chunks):
        raise ValueError("Expected the frozen, unique 37-chunk Phase 1 corpus")
    declarations = {item["chunk_file"]: item for item in manifest["sources"]}
    for path in chunk_paths:
        entry = declarations.get(path.name)
        if not entry or entry.get("rights_status") not in {"cleared", "internal_test_only"}:
            raise ValueError(f"Chunk file is not eligible in source manifest: {path.name}")
        if sha256(path) != entry.get("chunk_sha256"):
            raise ValueError(f"Frozen chunk hash mismatch: {path.name}")

    queries = jsonl(query_path)
    if len(queries) != 50 or len({row["query_id"] for row in queries}) != 50:
        raise ValueError("Expected exactly 50 unique query candidates")
    if any(row.get("source_manifest_sha256") != manifest_hash for row in queries):
        raise ValueError("Query candidate does not pin the frozen source manifest")

    rows = []
    for row in queries:
        candidates = []
        for qrel in row.get("qrels", []):
            source = by_id.get(qrel.get("chunk_id"))
            if source is None:
                raise ValueError(f"Unknown suggested chunk in {row['query_id']}")
            if (qrel.get("book_id"), qrel.get("role"), qrel.get("pages")) != (
                source["book_id"], source["role"], source["pages"]
            ):
                raise ValueError(f"Suggested source attribution mismatch in {row['query_id']}")
            quote = qrel.get("evidence_quote", "")
            if not quote or not exact_match(quote, source["text"]):
                raise ValueError(f"Suggested quote is not exact source text in {row['query_id']}")
            candidates.append({
                "chunk_id": source["chunk_id"], "book_id": source["book_id"],
                "role": source["role"], "pages": source["pages"],
                "suggested_evidence_quote": quote,
                "candidate_chunk_text": source["text"],
                "candidate_relevance": qrel.get("relevance"),
                "candidate_origin": qrel.get("adjudication"),
                "human_relevance": None, "human_evidence_quote": None,
                "human_review_note": None,
            })
        if not candidates:
            raise ValueError(f"No reviewable source suggestion for {row['query_id']}")
        rows.append({
            "query_id": row["query_id"], "query": row["query"],
            "question_type": row["question_type"], "split": row["split"],
            "candidate_origin": row.get("label_origin", "unknown"),
            "candidate_adjudication": row.get("query_adjudication", "unknown"),
            "human_query_valid": None, "human_answerability": None,
            "human_relevant_chunk_ids": None, "human_reviewer_id": None,
            "human_review_note": None, "suggested_qrels_for_review_only": candidates,
        })
    return {
        "schema": "b2c-phase3-human-review-pack/1.0",
        "status": "pending_human_review", "gold_eligible": False,
        "source_manifest_sha256": manifest_hash,
        "query_candidate_sha256": sha256(query_path),
        "chunk_files_sha256": {path.name: sha256(path) for path in chunk_paths},
        "review_instructions": [
            "Review every query for clarity and answerability from the frozen corpus.",
            "Set human_answerability to supported, partial, or unanswerable; reject unclear queries.",
            "Assign each suggested passage relevance 0..3; add/remove relevant chunk IDs as needed.",
            "For every positive judgment, record a contiguous exact source quote.",
            "Enter the real human reviewer identity. Do not mark this pack gold until review is complete.",
        ],
        "queries": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--chunks", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pack = build_pack(args.source_manifest, args.queries, args.chunks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": pack["status"], "gold_eligible": pack["gold_eligible"],
        "queries": len(pack["queries"]), "output": str(args.output),
        "source_manifest_sha256": pack["source_manifest_sha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
