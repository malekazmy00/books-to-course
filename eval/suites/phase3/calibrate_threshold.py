"""Calibrate/evaluate a lexical no-answer score on disjoint auto-labeled groups.

This is diagnostic only: generated labels are not human gold and selected
thresholds are not production defaults.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo",type=Path,default=Path(__file__).resolve().parents[3])
    parser.add_argument("--data-root",type=Path,default=Path("/mnt/wsl/b2cdata/phase1-sources"))
    parser.add_argument("--output",type=Path,default=None)
    args=parser.parse_args()
    repo=args.repo.resolve(); data=args.data_root
    manifest=repo/"eval/golden/phase1/source-manifest.json"
    files={"positive":data/"phase3-qa-50.jsonl","negative":data/"phase3-unanswerable-10.jsonl",
      "lane-primary-30-chunks.jsonl":data/"lane-primary-30-chunks.jsonl",
      "thinkstats-supplement-7-chunks.jsonl":data/"thinkstats-supplement-7-chunks.jsonl"}
    raw=[]
    for name in ["lane-primary-30-chunks.jsonl","thinkstats-supplement-7-chunks.jsonl"]:
        raw.extend(json.loads(line) for line in files[name].read_text(encoding="utf-8").splitlines() if line.strip())
    if len(raw)!=37: raise ValueError(f"expected 37 frozen chunks, got {len(raw)}")
    from b2c_contracts.models import StructuredChunk
    from b2c_indexing import lexical_search
    chunks=[StructuredChunk(chunk_id=x["chunk_id"],book_id=x["book_id"],role=x["role"],
      section_path=x.get("section_path",[]),block_ids=x.get("block_ids") or [x["chunk_id"]],
      pages=x["pages"],text=x["text"],token_count=max(1,int(x.get("token_count",len(x["text"].split())))),
      embedding_model="phase3-threshold-diagnostic") for x in raw]
    rows=[]
    for path,label in [(files["positive"],"supported"),(files["negative"],"absent")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            item=json.loads(line); query=item.get("query",item.get("question"))
            group=item["qrels"][0]["chunk_id"] if label=="supported" else item["query_id"]
            ranked=lexical_search(query,chunks,top_k=len(chunks))
            rows.append({"query_id":item["query_id"],"query":query,"class":label,
              "split_group":group,"score":ranked[0][1] if ranked else 0.0,
              "top_chunk":ranked[0][0].chunk_id if ranked else None,"gold_eligible":False})
    for label,fraction in [("supported",.2),("absent",.5)]:
        groups=sorted({x["split_group"] for x in rows if x["class"]==label},
          key=lambda group:hashlib.sha256(("b2c-tidx03-v2|"+label+"|"+group).encode()).hexdigest())
        held=set(groups[:max(1,round(len(groups)*fraction))])
        for row in rows:
            if row["class"]==label:
                row["split"]="heldout" if row["split_group"] in held else "calibration"
    cal=[x for x in rows if x["split"]=="calibration"]; test=[x for x in rows if x["split"]=="heldout"]
    def rates(selected:list[dict[str,Any]],threshold:float)->dict[str,Any]:
        pos=[x for x in selected if x["class"]=="supported"]; neg=[x for x in selected if x["class"]=="absent"]
        answered=[x for x in selected if x["score"]>=threshold]
        false_answers=sum(x["score"]>=threshold for x in neg)
        false_abstentions=sum(x["score"]<threshold for x in pos)
        return {"n":len(selected),"supported_n":len(pos),"absent_n":len(neg),
          "answered_n":len(answered),"coverage":len(answered)/len(selected),
          "false_answer_count":false_answers,"false_answer_rate_absent":false_answers/len(neg) if neg else None,
          "false_abstention_count":false_abstentions,
          "false_abstention_rate_supported":false_abstentions/len(pos) if pos else None,
          "risk_false_answer_among_answered":false_answers/len(answered) if answered else None}
    def youden(threshold:float)->tuple[float,float,float]:
        pos=[x for x in cal if x["class"]=="supported"]; neg=[x for x in cal if x["class"]=="absent"]
        tpr=sum(x["score"]>=threshold for x in pos)/len(pos)
        fpr=sum(x["score"]>=threshold for x in neg)/len(neg)
        return tpr-fpr,tpr,fpr
    scores=sorted({x["score"] for x in cal})
    candidates=[0.0]+[(a+b)/2 for a,b in zip(scores,scores[1:])]+[scores[-1]+1e-9]
    threshold=max(candidates,key=lambda value:(youden(value)[0],value))
    j,tpr,fpr=youden(threshold)
    result={"schema":"b2c-tidx03-threshold/1.0","gold_eligible":False,
      "method":"lexical max score; positives grouped by cited chunk; absent queries grouped by query; calibration maximizes Youden J (TPR-FPR), higher threshold wins ties",
      "split_seed":"b2c-tidx03-v2","threshold":threshold,
      "calibration":{"youden_j":j,"tpr":tpr,"fpr":fpr,**rates(cal,threshold)},
      "heldout":rates(test,threshold),
      "sha256":{"positive":sha(files["positive"]),"negative":sha(files["negative"]),
        "manifest":sha(manifest),"chunks":{n:sha(files[n]) for n in ["lane-primary-30-chunks.jsonl","thinkstats-supplement-7-chunks.jsonl"]}},
      "limitations":["automatic labels, not human gold; semantic correctness is not independently adjudicated",
        "only 10 absent examples (5 calibration, 5 held out); rates are imprecise, not production guarantees",
        "no partially supported, irrelevant-only, or conflicting evidence examples",
        "Youden J assumes equal cost for false answers and false abstentions; no operating target was specified",
        "lexical score is corpus-dependent; recalibrate after corpus or retrieval changes"],
      "items":[{**x,"gold_eligible":False} for x in rows]}
    output=args.output or data/"phase3-threshold-calibration-10x50.json"
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!="items"},ensure_ascii=False,indent=2))
if __name__=="__main__": main()
