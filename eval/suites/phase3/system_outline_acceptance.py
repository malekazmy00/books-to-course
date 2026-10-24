#!/usr/bin/env python3
"""Run deterministic system outline review and T-IDX-01 corpus checks."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from b2c_contracts import CourseOutline, ParsedBook, SCHEMA_VERSION
from b2c_outline.review import POLICY_ID, POLICY_VERSION, review_outline
from b2c_indexing.chunking import chunk_book

DATA = Path("/mnt/wsl/b2cdata/phase1-sources")
SAMPLE = DATA / "phase3-tidx01-two-book"
LANE_PATH = DATA / "phase3-tidx01-lane-parsed.json"
THINK_PATH = DATA / "phase3-tidx01-thinkstats-parsed.json"
CANDIDATE_PATH = SAMPLE / "candidate-outline-review-required.json"
OUT = SAMPLE / "system-approved-outline.json"
REPORT = SAMPLE / "system-review-and-audit.json"
def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def main() -> None:
    lane = ParsedBook.model_validate_json(LANE_PATH.read_bytes())
    think = ParsedBook.model_validate_json(THINK_PATH.read_bytes())
    candidate = CourseOutline.model_validate_json(CANDIDATE_PATH.read_bytes())
    result = review_outline(lane, candidate)
    accepted = CourseOutline.model_validate({**candidate.model_dump(mode="python"), "requires_review": False, "schema_version": SCHEMA_VERSION})
    approval = {"review_type":"system", "reviewer_id":None, "system_id":POLICY_ID,
        "policy_version":POLICY_VERSION, "evidence_sha256":result.evidence_sha256,
        "checks":list(result.checks), "scope":"structural_integrity_only"}
    accepted_payload = {"outline":accepted.model_dump(mode="json"), "approval_provenance":approval,
        "draft_source_sha256":sha(CANDIDATE_PATH), "parsed_source_sha256":sha(LANE_PATH)}
    OUT.write_text(json.dumps(accepted_payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    positions={b.block_id:i for i,b in enumerate(lane.blocks)}
    lessons=[l for m in accepted.modules for l in m.lessons]
    assignments={}
    for lesson in lessons:
        for block in lane.blocks[positions[lesson.spine_span.start_block]:positions[lesson.spine_span.end_block]+1]:
            assignments[block.block_id]=lesson.lesson_id
    lane_chunks=chunk_book(lane,target_tokens=500,overlap_ratio=.12,embedding_model="phase3-audit",outline=accepted)
    think_chunks=chunk_book(think,target_tokens=500,overlap_ratio=.12,embedding_model="phase3-audit")
    crossings=[]
    for chunk in lane_chunks:
        if len({assignments[b] for b in chunk.block_ids})!=1: crossings.append(chunk.chunk_id)
    tables=[b for book in (lane,think) for b in book.blocks if b.type=="table"]
    by_book={lane.book_id:lane_chunks,think.book_id:think_chunks}
    table_ok={b.block_id: bool([c for c in by_book[b.refs[0].book_id] if b.block_id in c.block_ids]) and
        all(b.text in c.text for c in by_book[b.refs[0].book_id] if b.block_id in c.block_ids) for b in tables}
    if crossings or len(lane_chunks)+len(think_chunks)!=58 or len(tables)!=11 or not all(table_ok.values()):
        raise RuntimeError("T-IDX-01 structural gate failed")
    report={"status":"PASS", "gate":"T-IDX-01", "policy":approval,
        "accepted_outline_sha256":sha(OUT), "candidate_outline_sha256":sha(CANDIDATE_PATH),
        "lane_parsed_sha256":sha(LANE_PATH), "thinkstats_parsed_sha256":sha(THINK_PATH),
        "lessons":len(lessons), "chunks":len(lane_chunks)+len(think_chunks),
        "boundary_crossings":len(crossings), "table_blocks":len(tables),
        "whole_tables":sum(table_ok.values()), "diagnostic_limitations":[
            "System approval checks structure and source provenance only; no pedagogy or curriculum judgment.",
            "Two-book corpus is a fixed internal-test research sample, not general production evidence."]}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__": main()