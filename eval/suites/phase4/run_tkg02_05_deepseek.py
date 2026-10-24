"""Run bounded, source-validated Phase 4 ER/cross-book diagnostic labels.

No human-gold claim, no fallback, no retries. Provider response text and source
quotes are stored only in the isolated B2C evaluation data volume.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re, time, unicodedata, urllib.request
from collections import Counter
from pathlib import Path

MODEL = "deepseek/deepseek-v4-flash-0731"
URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOKENS = 4096


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s).split())


def call(key: str, task: str, payload: dict, remaining_usd: float) -> dict:
    user = json.dumps({"task": task, "data": payload}, ensure_ascii=False)
    raw = json.dumps({
        "model": MODEL, "temperature": 0, "max_tokens": MAX_TOKENS,
        "reasoning_effort": "none", "response_format": {"type": "json_object"},
        "provider": {"sort": "price", "allow_fallbacks": False,
                     "max_price": {"prompt": 0.0152, "completion": 1.28}},
        "messages": [{"role": "system", "content": "Return only JSON object. Use only provided source evidence. Do not infer unsupported identity or relevance. If uncertain, abstain. Echo citations exactly; every quote must be copied literally from the supplied source."},
                     {"role": "user", "content": user}],
    }, ensure_ascii=False).encode()
    reserve = (len(raw) / 3 * 0.0152 + MAX_TOKENS * 1.28) / 1_000_000
    if reserve > remaining_usd:
        raise RuntimeError(f"budget_exceeded_before_request:{reserve:.8f}")
    req = urllib.request.Request(URL, data=raw, headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json",
        "HTTP-Referer": "https://book2course.local", "X-Title": "B2C Phase 4 diagnostic",
    }, method="POST")
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as response:
        outer = json.loads(response.read())
    choice = outer["choices"][0]; content = choice["message"].get("content")
    if outer.get("model") != MODEL: raise RuntimeError("route_model_mismatch")
    if choice.get("finish_reason") == "length" or not isinstance(content, str): raise RuntimeError("truncated_or_missing_content")
    result = json.loads(content)
    usage = outer.get("usage") or {}
    result["_usage"] = {
        "model": outer["model"], "finish_reason": choice.get("finish_reason"),
        "reasoning_effort": "none", "provider_fallbacks_allowed": False,
        "input_tokens": usage.get("prompt_tokens", usage.get("input_tokens")),
        "output_tokens": usage.get("completion_tokens", usage.get("output_tokens")),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
        "cost_usd": usage.get("cost", usage.get("cost_usd")),
        "conservative_reservation_usd": reserve,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "request_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return result


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def score(rows: list[dict], rankings: dict[str, list[str]]) -> dict:
    recalls=[]; mrr=[]; ndcg=[]; complete=[]
    for row in rows:
        gains={q["chunk_id"]:q["relevance"] for q in row["qrels"]}
        ranked=rankings.get(row["query_id"], [])[:10]
        hits=[x for x in ranked if x in gains]
        recalls.append(len(set(hits))/len(gains))
        mrr.append(next((1/i for i,x in enumerate(ranked,1) if x in gains),0.0))
        dcg=sum((2**gains.get(x,0)-1)/math.log2(i+1) for i,x in enumerate(ranked,1))
        ideal=sorted(gains.values(),reverse=True)[:10]
        idcg=sum((2**v-1)/math.log2(i+1) for i,v in enumerate(ideal,1))
        ndcg.append(dcg/idcg if idcg else 0.0)
        complete.append(set(gains)<=set(ranked))
    avg=lambda xs:sum(xs)/len(xs) if xs else None
    return {"queries":len(rows),"recall@10":avg(recalls),"mrr@10":avg(mrr),
            "ndcg@10":avg(ndcg),"complete_evidence@10":avg(complete)}


def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir",type=Path,required=True)
    ap.add_argument("--key-file",type=Path,default=Path("/root/.config/book2course/openrouter-api-key"))
    ap.add_argument("--prior-phase4-cost",type=float,required=True)
    ap.add_argument("--cap-usd",type=float,default=.25)
    args=ap.parse_args(); d=args.data_dir
    if args.prior_phase4_cost + 2*.006 > args.cap_usd: raise SystemExit("two-call reserve exceeds Phase 4 cap")
    key=args.key_file.read_text().strip()
    if not key: raise SystemExit("empty key file")

    # T-KG-02: candidate pairs are all in the 30 Lane documents retained by the isolated LightRAG eval store.
    pairfile=d/"tkg02-pair-candidates-lane-30.json"; candidates=json.loads(pairfile.read_text())
    source_labels=json.loads((d/"phase4-deepseek-3book-entities-final.json").read_text())
    source_chunks={c["chunk_id"]:c for c in load_jsonl(Path("/mnt/wsl/b2cdata/phase1-sources/lane-primary-30-chunks.jsonl"))}
    for pair in candidates["pairs"]:
        for side in ("left","right"):
            e=pair[side]; src=source_chunks.get(e["chunk_id"])
            if not src or e["evidence_quote"] not in src["text"] or e["name"].casefold() not in e["evidence_quote"].casefold():
                raise SystemExit("pair candidate failed exact source preflight")
    er=call(key,"Adjudicate every entity-resolution pair. Return {pairs:[{pair_id,decision:same|different|unresolved,left_quote,right_quote}]}. Decide identity, not string similarity. Echo both evidence quotes exactly. Use unresolved when source evidence is insufficient.",{"pairs":candidates["pairs"]},args.cap_usd-args.prior_phase4_cost)
    er_by={r.get("pair_id"):r for r in er.get("pairs",[]) if isinstance(r,dict)}
    if len(er_by)!=50 or set(er_by)!=set(x["pair_id"] for x in candidates["pairs"]): raise SystemExit("ER result IDs/count do not match")
    for pair in candidates["pairs"]:
        row=er_by[pair["pair_id"]]
        if row.get("decision") not in {"same","different","unresolved"}: raise SystemExit("invalid ER decision")
        if row.get("left_quote")!=pair["left"]["evidence_quote"] or row.get("right_quote")!=pair["right"]["evidence_quote"]: raise SystemExit("ER exact-quote echo mismatch")

    store=Path("/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261004/data/rag_storage")
    status=json.loads((store/"kv_store_doc_status.json").read_text()); full=json.loads((store/"kv_store_full_entities.json").read_text())
    docid_by_chunk={v.get("file_path"):k for k,v in status.items() if v.get("status")=="processed"}
    def graph_name(side):
        docid=docid_by_chunk.get(side["chunk_id"]); names=full.get(docid,{}).get("entity_names",[]) if docid else []
        wanted=norm(side["name"])
        return wanted if any(norm(n)==wanted for n in names) else None
    tp=fp=fn=tn=0; excluded=0
    for pair in candidates["pairs"]:
        decision=er_by[pair["pair_id"]]["decision"]
        if decision=="unresolved": excluded+=1; continue
        pred=bool(graph_name(pair["left"]) and graph_name(pair["right"]) and norm(pair["left"]["name"])==norm(pair["right"]["name"]))
        truth=decision=="same"
        if pred and truth:tp+=1
        elif pred:fp+=1
        elif truth:fn+=1
        else:tn+=1
    precision=tp/(tp+fp) if tp+fp else 0.0; recall=tp/(tp+fn) if tp+fn else 0.0
    er_final={"schema":"b2c-tkg02-entity-resolution-diagnostic/1","status":"diagnostic_only","gold_eligible":False,
        "model":MODEL,"source_candidate_sha256":sha(pairfile),"source_label_sha256":sha(d/"phase4-deepseek-3book-entities-final.json"),
        "decision_provenance":"automatic DeepSeek pair adjudication against source quotes; not independent human gold",
        "candidate_system":"LightRAG v1.5.7 per-document full_entities; pair is resolved only when both mentions are present and normalized names equal",
        "candidate_corpus_status_sha256":sha(store/"kv_store_doc_status.json"),"candidate_entity_store_sha256":sha(store/"kv_store_full_entities.json"),
        "pair_count":50,"resolved_label_count":50-excluded,"unresolved_excluded":excluded,"confusion_matrix":{"tp":tp,"fp":fp,"fn":fn,"tn":tn},
        "precision":precision,"recall":recall,"f1":2*precision*recall/(precision+recall) if precision+recall else 0.0,
        "threshold_f1":.9,"threshold_pass":(2*precision*recall/(precision+recall) if precision+recall else 0.0)>=.9,
        "strata_counts":dict(Counter(x["sampling_stratum"] for x in candidates["pairs"])),"usage":er["_usage"],
        "limits":["Model-generated labels are diagnostic, not gold.","Pair sample is candidate-enriched (12 repeated surface, 1 ambiguous formula, 37 lexical near-matches), not a random corpus sample.","The measured candidate is LightRAG exact normalized-name overlap, not semantic alias resolution."]}
    (d/"tkg02-entity-resolution-result.json").write_text(json.dumps(er_final,indent=2)+"\n")
    (d/"tkg02-machine-pair-labels.json").write_text(json.dumps({"schema":"b2c-tkg02-machine-labels/1","gold_eligible":False,"model":MODEL,"labels":er_by,"usage":er["_usage"]},indent=2)+"\n")

    # T-KG-05: add only supplement qrels to the exact frozen primary queries; no new query text is invented.
    t05in=json.loads((d/"tkg05-crossbook-adjudication-input-39x7.json").read_text())
    cross=call(key,"For each query, determine whether any supplied Think Stats supplementary chunk independently supports answering that exact query. Return {assessments:[{query_id,supported:boolean,supplementary_qrel:null|{chunk_id,book_id,role,pages,evidence_quote}}]}. Give at most one best supplementary qrel per query. The quote must be a verbatim span >=12 chars and identify the exact chunk. If support is tangential or absent, supported=false and qrel=null.",t05in,args.cap_usd-args.prior_phase4_cost-er["_usage"].get("cost_usd",0) if er["_usage"].get("cost_usd") is not None else args.cap_usd-args.prior_phase4_cost-.006)
    baseq=json.loads((Path("/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261004/queries.json")).read_text())
    qby={q["query_id"]:q for q in baseq}; suppby={c["chunk_id"]:c for c in load_jsonl(Path("/mnt/wsl/b2cdata/phase1-sources/thinkstats-supplement-7-chunks.jsonl"))}
    assessments={x.get("query_id"):x for x in cross.get("assessments",[]) if isinstance(x,dict)}
    if set(assessments)!={q["query_id"] for q in t05in["queries"]}: raise SystemExit("T-KG-05 assessment IDs do not match")
    crossrows=[]
    for inputq in t05in["queries"]:
        qid=inputq["query_id"]; a=assessments[qid]
        if not a.get("supported"): continue
        r=a.get("supplementary_qrel") or {}; src=suppby.get(r.get("chunk_id"))
        if not src or r.get("book_id")!=src["book_id"] or r.get("role")!="supplementary" or r.get("pages")!=src["pages"] or not isinstance(r.get("evidence_quote"),str) or len(r["evidence_quote"].strip())<12 or r["evidence_quote"] not in src["text"]:
            raise SystemExit("T-KG-05 exact source/page validation failed")
        original=qby[qid]; spine=[x for x in original["qrels"] if x["role"]=="spine"]
        if not spine: raise SystemExit("source query missing spine qrel")
        crossrows.append({**original,"question_type":"cross_book","query_adjudication":"system_machine_evaluated","gold_eligible":False,
            "system_review":{"model_id":MODEL,"prompt_sha256":cross["_usage"]["request_sha256"],"scope":"machine semantic answerability/completeness and exact source citation; not human gold"},
            "qrels":spine+[{'chunk_id':src['chunk_id'],'relevance':2,'book_id':src['book_id'],'role':'supplementary','pages':src['pages'],'evidence_quote':r['evidence_quote'],'adjudication':'system_machine_evaluated'}]})
    light=json.loads(Path("/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261004/comparison-final-candidate-run.json").read_text())["rankings"]
    baseline=json.loads(Path("/mnt/wsl/b2cdata/phase4-lightrag-openrouter-20261004/comparison-final-baseline-run.json").read_text())["rankings"]
    lscore=score(crossrows,light); bscore=score(crossrows,baseline)
    t05={"schema":"b2c-tkg05-crossbook-retrieval-diagnostic/1","status":"diagnostic_only" if crossrows else "not_evaluable","gold_eligible":False,
        "model":MODEL,"source_input_sha256":sha(d/"tkg05-crossbook-adjudication-input-39x7.json"),"original_query_dataset_sha256":t05in["dataset_sha256"],
        "light_ranking_sha256":t05in["lightrag_rankings_sha256"],"baseline_ranking_sha256":t05in["lexical_rankings_sha256"],
        "paired_query_count":len(crossrows),"assessed_primary_queries":len(assessments),"accepted_crossbook_qrels":len(crossrows),
        "source_quote_validation":"all accepted supplementary qrel quotes match the cited frozen chunk byte-for-byte; original spine qrels retained",
        "lightRAG_mix":lscore,"lexical_baseline":bscore,"usage":cross["_usage"],
        "queries":[{"query_id":q["query_id"],"query":q["query"],"qrels":[{"chunk_id":r["chunk_id"],"book_id":r["book_id"],"role":r["role"],"pages":r["pages"],"evidence_quote_sha256":hashlib.sha256(r["evidence_quote"].encode()).hexdigest()} for r in q["qrels"]]} for q in crossrows],
        "limits":["Qrels were machine-adjudicated and are not gold.","The labelger was the same model family used to build entity diagnostics.","Only the 39 frozen spine queries were checked; unsupported supplemental pairs were excluded."]}
    (d/"tkg05-crossbook-retrieval-result.json").write_text(json.dumps(t05,ensure_ascii=False,indent=2)+"\n")
    (d/"tkg05-machine-crossbook-labels.json").write_text(json.dumps({"schema":"b2c-tkg05-machine-labels/1","gold_eligible":False,"labels":crossrows,"assessments":assessments,"usage":cross["_usage"]},ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"tkg02":{k:er_final[k] for k in ("pair_count","resolved_label_count","unresolved_excluded","f1","threshold_pass")},"tkg05":{"assessed":len(assessments),"crossbook":len(crossrows),"lightRAG_mix":lscore,"lexical_baseline":bscore},"cost_usd":{"er":er["_usage"].get("cost_usd"),"crossbook":cross["_usage"].get("cost_usd"),"total_prior":args.prior_phase4_cost},"outputs":[str(d/"tkg02-entity-resolution-result.json"),str(d/"tkg05-crossbook-retrieval-result.json")]},ensure_ascii=False))

if __name__=="__main__": main()
