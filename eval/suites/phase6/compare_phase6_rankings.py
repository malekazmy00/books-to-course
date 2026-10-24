"""Compare frozen Phase 6 rankings on automatically judged Phase 3 semantic qrels."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
from typing import Any

def _phase3():
    path=Path(__file__).resolve().parents[1]/"phase3"/"retrieval_eval.py"
    spec=importlib.util.spec_from_file_location("b2c_phase3_eval_for_phase6",path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {path}")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

def corpus_fingerprint(paths: list[Path])->str:
    h=hashlib.sha256()
    for path in sorted(paths,key=lambda p:p.name):
        h.update(path.name.encode()); h.update(b"\0"); h.update(path.read_bytes()); h.update(b"\0")
    return h.hexdigest()

def _json(path:Path)->dict[str,Any]:
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise ValueError(f"{path} must contain a JSON object")
    return value

def compare(dataset_path:Path,chunk_paths:list[Path],baseline_path:Path,candidate_path:Path,split:str,k:int):
    ev=_phase3(); chunks=[row for path in chunk_paths for row in ev.load_chunks(path)]
    if len({r["chunk_id"] for r in chunks})!=len(chunks): raise ValueError("duplicate chunk_id")
    data_hash=hashlib.sha256(dataset_path.read_bytes()).hexdigest(); corpus_hash=corpus_fingerprint(chunk_paths)
    rows=ev.validate_dataset(dataset_path,chunks,require_adjudicated=False,
        allow_auto_labeled=True,allow_system_evaluated=True)
    rows=[r for r in rows if r["split"]==split]
    if not rows: raise ValueError(f"no queries in split {split!r}")
    runs={"baseline":_json(baseline_path),"candidate":_json(candidate_path)}
    expected={r["query_id"] for r in rows}; known={r["chunk_id"] for r in chunks}; ranked={}
    for label,run in runs.items():
        if run.get("corpus_sha256")!=corpus_hash: raise ValueError(f"{label} corpus hash mismatch")
        if run.get("dataset_sha256")!=data_hash: raise ValueError(f"{label} dataset hash mismatch")
        if run.get("retriever")==runs["baseline" if label=="candidate" else "candidate"].get("retriever"):
            raise ValueError("retrievers must have distinct names")
        values=run.get("rankings")
        if not isinstance(values,dict) or set(values)!=expected: raise ValueError(f"{label} query IDs differ from selected query set")
        ranked[label]={}
        for qid,ids in values.items():
            if not isinstance(ids,list) or any(not isinstance(cid,str) for cid in ids): raise ValueError(f"{label}/{qid}: invalid ranking")
            if len(ids)!=len(set(ids)) or set(ids)-known: raise ValueError(f"{label}/{qid}: duplicate or unknown chunk IDs")
            ranked[label][qid]=ids[:k]
    scores={name:ev.score_ranked_ids(rows,ranked[name],ks=(1,3,5,k)) for name in ("baseline","candidate")}
    deltas={name:(scores["candidate"]["metrics"][name]-value if value is not None and scores["candidate"]["metrics"][name] is not None else None)
        for name,value in scores["baseline"]["metrics"].items()}
    return {"status":"diagnostic_only","gold_eligible":False,
        "label_provenance":"system_machine_evaluated_pseudo_qrels",
        "dataset_sha256":data_hash,"corpus_sha256":corpus_hash,"split":split,"k":k,"query_count":len(rows),
        "baseline":{"retriever":runs["baseline"]["retriever"],**scores["baseline"]},
        "candidate":{"retriever":runs["candidate"]["retriever"],**scores["candidate"]},
        "candidate_minus_baseline":deltas,
        "limits":["automatic semantic judgments are diagnostic, not human gold", "retrieval-only comparison; no production quality gate"]}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--chunks",type=Path,nargs="+",required=True)
    p.add_argument("--dataset",type=Path,required=True); p.add_argument("--baseline",type=Path,required=True)
    p.add_argument("--candidate",type=Path,required=True); p.add_argument("--split",choices=("development","test"),default="development")
    p.add_argument("--k",type=int,default=10); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    result=compare(a.dataset,a.chunks,a.baseline,a.candidate,a.split,a.k)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"query_count":result["query_count"],"candidate":result["candidate"]["metrics"]},ensure_ascii=False))
if __name__=="__main__": main()
