"""Paired, same-corpus comparison of Phase 3 and graph-assisted rankings."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any
class ComparisonError(ValueError):
    """A comparison is not fair or reproducible."""
def _phase3():
    path=Path(__file__).resolve().parents[1]/"phase3"/"retrieval_eval.py"
    spec=importlib.util.spec_from_file_location("b2c_phase3_eval_for_phase4",path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {path}")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
def corpus_fingerprint(paths: list[Path])->str:
    h=hashlib.sha256()
    for path in sorted(paths,key=lambda p:p.name):
        h.update(path.name.encode()); h.update(b"\\0"); h.update(path.read_bytes()); h.update(b"\\0")
    return h.hexdigest()
def _json(path:Path)->dict[str,Any]:
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise ComparisonError(f"invalid {path}: {exc}") from exc
    if not isinstance(value,dict): raise ComparisonError(f"{path} must contain a JSON object")
    return value
def compare(*,dataset_path:Path,chunk_paths:list[Path],baseline_path:Path,candidate_path:Path,
            split:str="test",k:int=10,allow_auto_labeled:bool=False)->dict[str,Any]:
    if not chunk_paths or k<1: raise ComparisonError("chunk files are required and k must be positive")
    ev=_phase3(); chunks=[row for path in chunk_paths for row in ev.load_chunks(path)]
    if len({r["chunk_id"] for r in chunks})!=len(chunks): raise ComparisonError("duplicate chunk_id")
    data_hash=hashlib.sha256(dataset_path.read_bytes()).hexdigest(); corpus_hash=corpus_fingerprint(chunk_paths)
    rows=ev.validate_dataset(dataset_path,chunks,require_adjudicated=not allow_auto_labeled,
                             allow_auto_labeled=allow_auto_labeled)
    rows=[r for r in rows if r["split"]==split]
    if not rows: raise ComparisonError(f"no queries in split {split!r}")
    runs={"baseline":_json(baseline_path),"candidate":_json(candidate_path)}
    if runs["baseline"].get("retriever")==runs["candidate"].get("retriever"):
        raise ComparisonError("retrievers must have distinct names")
    expected={r["query_id"] for r in rows}; known={r["chunk_id"] for r in chunks}; ranked={}
    for label,run in runs.items():
        if run.get("corpus_sha256")!=corpus_hash: raise ComparisonError(f"{label} corpus hash mismatch")
        if run.get("dataset_sha256")!=data_hash: raise ComparisonError(f"{label} dataset hash mismatch")
        rankings=run.get("rankings")
        if not isinstance(rankings,dict) or set(rankings)!=expected:
            raise ComparisonError(f"{label} query IDs differ from selected query set")
        ranked[label]={}
        for qid,ids in rankings.items():
            if not isinstance(ids,list) or any(not isinstance(cid,str) for cid in ids):
                raise ComparisonError(f"{label}/{qid}: ranking must be chunk IDs")
            if len(ids)!=len(set(ids)): raise ComparisonError(f"{label}/{qid}: duplicate chunk IDs")
            if set(ids)-known: raise ComparisonError(f"{label}/{qid}: unknown chunk IDs")
            ranked[label][qid]=ids[:k]
    scores={name:ev.score_ranked_ids(rows,ranked[name],ks=(1,3,5,k)) for name in ("baseline","candidate")}
    deltas={name:(scores["candidate"]["metrics"][name]-value
        if value is not None and scores["candidate"]["metrics"][name] is not None else None)
        for name,value in scores["baseline"]["metrics"].items()}
    auto=allow_auto_labeled or any(r.get("query_adjudication")!="human_adjudicated" for r in rows)
    return {"status":"diagnostic_only" if auto else "paired_evaluation","gold_eligible":not auto,
      "label_provenance":"model_auto_validated_pseudo_qrels" if auto else "human_adjudicated",
      "corpus_sha256":corpus_hash,"dataset_sha256":data_hash,"split":split,"k":k,"query_count":len(rows),
      "baseline":{"retriever":runs["baseline"]["retriever"],**scores["baseline"]},
      "candidate":{"retriever":runs["candidate"]["retriever"],**scores["candidate"]},
      "candidate_minus_baseline":deltas,
      "limits":(["auto-generated source labels are diagnostics, never human gold"] if auto else [])+
       ["retrieval-only comparison; does not establish entity extraction quality, durability, isolation, or security",
        "metric improvement alone does not accept a graph candidate"]}
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--chunks",type=Path,nargs="+",required=True)
    p.add_argument("--dataset",type=Path,required=True); p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--candidate",type=Path,required=True); p.add_argument("--split",choices=("development","test"),default="test")
    p.add_argument("--k",type=int,default=10); p.add_argument("--allow-auto-labeled",action="store_true")
    p.add_argument("--output",type=Path); a=p.parse_args()
    result=compare(dataset_path=a.dataset,chunk_paths=a.chunks,baseline_path=a.baseline,candidate_path=a.candidate,
      split=a.split,k=a.k,allow_auto_labeled=a.allow_auto_labeled); rendered=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output: a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(rendered+"\n",encoding="utf-8")
    print(rendered)
if __name__=="__main__": main()
