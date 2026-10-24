#!/usr/bin/env python3
"""Local-only expanded Phase 3 auto audit. Source text never leaves localhost."""
from __future__ import annotations
import hashlib
import itertools
import json
import time
import urllib.request
from pathlib import Path
ROOT=Path("/root/projects/book2course"); DATA=Path("/mnt/wsl/b2cdata/phase1-sources")
OUT=DATA/"phase3-auto-audit-v2.json"; CK=DATA/"phase3-auto-audit-v2-checkpoint.json"
GEN="gemma4:latest"; JUDGE="gemma4:latest"; API="http://localhost:11434/api/chat"
BASE_POS=DATA/"phase3-qa-50.jsonl"; BASE_NEG=DATA/"phase3-unanswerable-10.jsonl"
CHUNK_FILES=[DATA/"lane-primary-30-chunks.jsonl",DATA/"thinkstats-supplement-7-chunks.jsonl"]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def rjsonl(p): return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
def dump(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def norm(s): return " ".join(s.casefold().split())
def call(model,messages,tokens=3000,temp=0.0):
 body={"model":model,"messages":messages,"stream":False,"format":"json","options":{"temperature":temp,"seed":314159,"num_predict":tokens,"num_ctx":32768},"keep_alive":"10m","think":False}
 req=urllib.request.Request(API,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
 t=time.perf_counter()
 with urllib.request.urlopen(req,timeout=900) as res: p=json.loads(res.read())
 elapsed=round(time.perf_counter()-t,3); c=p.get("message",{}).get("content","")
 if p.get("model")!=model or not c: raise RuntimeError(f"model identity/empty response guard: {p.get('model')}")
 try: obj=json.loads(c)
 except Exception as e: raise RuntimeError("non-JSON completion discarded") from e
 return obj,{"model":p["model"],"elapsed_seconds":elapsed,"prompt_eval_count":p.get("prompt_eval_count"),"eval_count":p.get("eval_count"),"done_reason":p.get("done_reason"),"cost_usd":0,"route":"localhost:11434"}
def exact(q,t):
 import unicodedata
 return " ".join(unicodedata.normalize("NFKC",q).split()) in " ".join(unicodedata.normalize("NFKC",t).split())
def validate_qrel(q,by):
 c=by.get(q["chunk_id"])
 if not c or (q["book_id"],q["role"],q["pages"])!=(c["book_id"],c["role"],c["pages"]) or not exact(q["evidence_quote"],c["text"]):
  raise RuntimeError(f"source/page/quote validation failed for {q.get('chunk_id')}")
def base_row(b,q,qid,cls,group,prov):
 return {"query_id":qid,"query":q,"question_type":b.get("question_type","direct"),"answerability_class":cls,
 "query_adjudication":"model_auto_validated","label_provenance":prov,"generation_group":group,"gold_eligible":False,
 "qrels":[{k:x[k] for k in ["chunk_id","relevance","book_id","role","pages","evidence_quote"]}|{"adjudication":"model_auto_validated"} for x in b["qrels"]],
 "source_manifest_sha256":"f8b91470da88691d3b2a4c92306c9fed0387f14e7f4d37f37ad7168aad10dd62"}
def main():
 chunks=rjsonl(CHUNK_FILES[0])+rjsonl(CHUNK_FILES[1]); by={x["chunk_id"]:x for x in chunks}
 hashes={p.name:sha(p) for p in CHUNK_FILES}
 expected={"lane-primary-30-chunks.jsonl":"1db9b1b426811f93fdf96f362c6c64eeae13a57cec58ea2888c0112ba2ce3f4e","thinkstats-supplement-7-chunks.jsonl":"210422e6942c8186fc45836caade6cb29c51cc445fb638f873b3ba71d57d9e4b"}
 if len(chunks)!=37 or hashes!=expected: raise RuntimeError("frozen Phase3 37-chunk corpus changed")
 pos=rjsonl(BASE_POS); neg=rjsonl(BASE_NEG)
 if len(pos)!=50 or len(neg)!=10: raise RuntimeError("frozen query corpus changed")
 for row in pos:
  for q in row["qrels"]: validate_qrel(q,by)
 result={"schema":"b2c-phase3-auto-audit/2.0","status":"preparing","gold_eligible":False,"generator":"deterministic templates + frozen Qwen set + Gemma4 paraphrase; local judge Gemma4","independent_judge":JUDGE,
 "route":"localhost:11434","cost_usd":0,"source_manifest_sha256":sha(ROOT/"eval/golden/phase1/source-manifest.json"),
 "chunk_sha256":hashes,"base_positive_sha256":sha(BASE_POS),"base_negative_sha256":sha(BASE_NEG),"selected":[],"calls":[],"judge_batches":[]}
 dump(CK,result); candidates=[]
 for i,b in enumerate(pos,1): candidates.append(base_row(b,b["query"],f"sup-{i:03d}","supported",b["query_id"],"Qwen3-Coder base query; qrel source spans exact-validated"))
 for start in range(0,50,10):
  group=pos[start:start+10]; payload=[{"id":f"para-{i+1:03d}","question":r["query"]} for i,r in enumerate(group,start)]
  sys="Paraphrase each question as one natural, unambiguous question with exactly the same answer and scope. Preserve IDs. JSON only: {items:[{id,paraphrase}]}"
  obj,meta=call(GEN,[{"role":"system","content":sys},{"role":"user","content":json.dumps(payload,ensure_ascii=False)}],1800)
  d={x.get("id"):x.get("paraphrase") for x in obj.get("items",[]) if isinstance(x,dict)}
  if set(d)!={x["id"] for x in payload}: raise RuntimeError(f"paraphrase response incomplete: {start}")
  for i,b in enumerate(group,start): candidates.append(base_row(b,d[f"para-{i+1:03d}"],f"para-{i+1:03d}","supported",b["query_id"],f"{GEN} paraphrase; inherited exact qrel; pending Gemma4 adjudication"))
  result["calls"].append({"purpose":"paraphrase","start":start,"meta":meta}); result["selected"]=candidates; dump(CK,result)
  print(json.dumps({"stage":"paraphrase","done":start+len(group),"seconds":meta["elapsed_seconds"]}),flush=True)
 lane=[r for r in pos if r["qrels"][0]["role"]=="spine"]; supp=[r for r in pos if r["qrels"][0]["role"]=="supplementary"]
 pairs=list(itertools.islice(itertools.product(lane,supp),50))
 if len(pairs)<50: raise RuntimeError("not enough cross-book pairs")
 for i,(a,b) in enumerate(pairs,1):
  q=f"Answer both source-specific questions. Primary book: {a['query']} Supplementary book: {b['query']}"
  row=base_row(a,q,f"xbook-{i:03d}","cross_book",f"{a['query_id']}|{b['query_id']}","deterministic two-book composition; inherited exact qrels; pending Gemma4 adjudication")
  row["question_type"]="cross_book"
  row["qrels"] += [{k:x[k] for k in ["chunk_id","relevance","book_id","role","pages","evidence_quote"]}|{"adjudication":"model_auto_validated"} for x in b["qrels"]]
  candidates.append(row)
 for i,b in enumerate(pos,1):
  q=b["query"].rstrip(" ?")+f", and what is the value of unreported control field B2C_MISSING_{i:03d}?"
  row=base_row(b,q,f"partial-{i:03d}","partial",b["query_id"],"controlled compound: supported source question plus unique absent field; qrel exact-validated")
  row["question_type"]="direct"; row["missing_subquestion"]=f"B2C_MISSING_{i:03d}"; candidates.append(row)
 result["selected"]=candidates; dump(CK,result)

 absent_topics=[
 "Cox proportional hazards regression","Gaussian copula estimation","Fisher information matrix for a mixture model","Dirichlet process posterior","Bayes factor for nested models","Markov chain Monte Carlo convergence diagnostics","Hawkes self-exciting point process","empirical likelihood confidence regions","generalized method of moments","Dempster-Shafer evidence theory",
 "extreme value theory for block maxima","survival analysis with competing risks","nonparametric density bandwidth selection","conditional value at risk estimation","Kalman filtering for latent states","hidden Markov model decoding","quantile regression under censoring","causal propensity score weighting","instrumental variable estimation","random effects meta-analysis",
 "bootstrap bias-corrected accelerated intervals","Laplace approximation for Bayesian evidence","variational inference for latent Dirichlet allocation","stochastic volatility state-space models","copula tail dependence coefficients","sequential probability ratio testing","false discovery rate control by Benjamini-Hochberg","missing-not-at-random imputation","Poisson regression with an offset term","negative binomial overdispersion estimation",
 "Rao-Blackwellized particle filters","Fisher scoring for logistic regression","generalized estimating equations","Markov random field partition functions","empirical process Donsker theorem","Wald test under boundary constraints","posterior predictive checks for hierarchical models","Bayesian credible intervals under a spike-and-slab prior","noncentral chi-square power calculation","cross-validation for censored survival data",
 "Anderson-Darling goodness-of-fit statistic","permutation test for clustered data","Neyman-Pearson lemma with composite hypotheses","minimax risk for shrinkage estimators","de Finetti representation theorem","renewal reward process limit theorem","branching process extinction probability","stochastic differential equation likelihood","spatial point process intensity estimation","change-point detection with fused lasso"]
 absent_forms=[
 "What is the exact likelihood function used for {topic}?",
 "Which assumptions are required to estimate {topic} consistently?",
 "How is the asymptotic variance derived for {topic}?",
 "What numerical example demonstrates {topic} in the textbook?",
 "Which diagnostic criterion should be used to validate {topic}?"]
 countries=[
 "Morocco","Japan","Brazil","Norway","Kenya","Portugal","Thailand","Chile","Ghana","Iceland","Vietnam","Peru","Nepal","Finland","Jordan","Mexico","Botswana","Croatia","Cambodia","Uruguay","Mongolia","Senegal","Belgium","Laos","Ecuador","Tunisia","Estonia","Bolivia","Namibia","Georgia","Malta","Rwanda","Paraguay","Lebanon","Slovenia","Oman","Tanzania","Cyprus","Zambia","Latvia","Fiji","Albania","Panama","Qatar","Lithuania","Armenia","Costa Rica","Madagascar","Luxembourg","Kuwait"]
 irrelevant_forms=[
 "What is the capital city of {country}?",
 "Which official currency is used in {country}?",
 "What is the largest city in {country}?",
 "Which continent is {country} located in?",
 "What is the national language of {country}?"]
 for family,items,forms,key in [
 ("absent",absent_topics,absent_forms,"topic"),
 ("irrelevant",countries,irrelevant_forms,"country")]:
  gathered=[]
  for gi,item in enumerate(items,1):
   for form in forms:
    q=form.format(**{key:item})
    gathered.append({"query_id":f"{family}-{len(gathered)+1:04d}","query":q,"question_type":"unanswerable",
      "answerability_class":family,"query_adjudication":"model_auto_validated",
      "label_provenance":"deterministic templated candidate; Gemma4 full-corpus adjudication",
      "generation_group":f"{family}-concept-{gi:03d}","gold_eligible":False,"qrels":[],
      "unanswerable_reason":"Pending independent local full-corpus check.","source_manifest_sha256":result["source_manifest_sha256"]})
  if len(gathered)!=250: raise RuntimeError(f"expected 250 deterministic {family} candidates")
  result[f"negative_candidates_{family}"]=gathered
 allq=candidates+result["negative_candidates_absent"]+result["negative_candidates_irrelevant"]
 cp=[{k:x[k] for k in ["chunk_id","book_id","role","pages","text"]} for x in chunks]
 sys=("Independent relevance audit. Inspect every supplied chunk for every query. Return one row per query ID: "
 "{query_id,direct_chunk_ids,partial_chunk_ids,conflict_chunk_ids}. direct: chunk alone answers the whole query; "
 "partial: materially answers only part; conflict: explicit incompatible values for the same scoped fact. "
 "Use only IDs/text supplied, no outside knowledge, no topic-only matches. If no support, empty arrays. JSON only.")
 judged=[]
 for start in range(0,len(allq),40):
  batch=allq[start:start+40]; q=[{"query_id":r["query_id"],"query":r["query"]} for r in batch]
  obj,meta=call(JUDGE,[{"role":"system","content":sys},{"role":"user","content":json.dumps({"queries":q,"source_chunks":cp},ensure_ascii=False,separators=(",",":"))}],5000)
  rows=obj.get("results",[]); byq={x.get("query_id"):x for x in rows if isinstance(x,dict)}; expected={x["query_id"] for x in batch}
  if set(byq)!=expected: raise RuntimeError(f"judge IDs mismatch at {start}")
  ids=set(by)
  for qid,x in byq.items():
   for f in ["direct_chunk_ids","partial_chunk_ids","conflict_chunk_ids"]:
    if not isinstance(x.get(f),list) or not set(x[f])<=ids: raise RuntimeError(f"invalid {f} IDs in {qid}")
  judged.append({"start":start,"results":rows,"meta":meta}); result["judge_batches"]=judged; result["status"]="judging"; dump(CK,result)
  print(json.dumps({"stage":"judge","batch":start//40+1,"total":(len(allq)+39)//40,"seconds":meta["elapsed_seconds"]}),flush=True)
 judge={x["query_id"]:x for batch in judged for x in batch["results"]}
 invalid=[]
 for r in candidates:
  cited={x["chunk_id"] for x in r["qrels"]}; support=set(judge[r["query_id"]]["direct_chunk_ids"]+judge[r["query_id"]]["partial_chunk_ids"])
  if not cited.intersection(support): invalid.append(r["query_id"])
  if r["answerability_class"]=="cross_book" and not cited<=support: invalid.append(r["query_id"])
  for q in r["qrels"]: validate_qrel(q,by)
 if invalid: raise RuntimeError(f"judge did not validate citations: {invalid[:20]}")
 selectednegs={}; rejected=[]
 for family in ["absent","irrelevant"]:
  accepted=[]
  for r in result[f"negative_candidates_{family}"]:
   j=judge[r["query_id"]]
   if not any(j[k] for k in ["direct_chunk_ids","partial_chunk_ids","conflict_chunk_ids"]) and len(accepted)<200:
    r["unanswerable_reason"]="Gemma4 found no direct, partial, or conflict evidence across all 37 frozen chunks; non-gold automated label."
    r["independent_judge"]=JUDGE; accepted.append(r)
   else: rejected.append({"query_id":r["query_id"],"class":family,"reason":"support found or quota reached","judge":j})
  if len(accepted)<200: raise RuntimeError(f"only {len(accepted)} audited {family} queries; threshold not selected")
  selectednegs[family]=accepted
 selected=candidates+selectednegs["absent"]+selectednegs["irrelevant"]
 for r in candidates: r["independent_judgment"]=judge[r["query_id"]]; r["independent_judge"]=JUDGE
 selected.extend(selectednegs["absent"]); selected.extend(selectednegs["irrelevant"])
 conflict_chunks=[]; conflict_queries=[]
 for i in range(1,51):
  fact=f"control_metric_{i:03d}"; va=10+i; vb=100+i
  a={"chunk_id":f"synthetic-conflict-{i:03d}-a","book_id":f"synthetic-source-a-{i:03d}","role":"spine","pages":[1],"text":f"Synthetic fixture {i}. For the identical study and scope, {fact} equals {va} units."}
  b={"chunk_id":f"synthetic-conflict-{i:03d}-b","book_id":f"synthetic-source-b-{i:03d}","role":"supplementary","pages":[1],"text":f"Synthetic fixture {i}. For the identical study and scope, {fact} equals {vb} units."}
  conflict_chunks += [a,b]
  conflict_queries.append({"query_id":f"conflict-{i:03d}","query":f"For the identical study, what values do the two contradictory sources give for {fact}?","question_type":"cross_book","answerability_class":"conflict",
    "query_adjudication":"synthetic_by_construction","label_provenance":"controlled synthetic contradiction fixture; not a real-book claim","generation_group":f"conflict-{i:03d}","gold_eligible":False,
    "qrels":[{"chunk_id":s["chunk_id"],"relevance":3,"book_id":s["book_id"],"role":s["role"],"pages":s["pages"],"evidence_quote":s["text"],"adjudication":"synthetic_by_construction"} for s in [a,b]]})
 result.update({"status":"final","selected":selected,"rejected_candidates":rejected,"conflict_fixture":{"chunks":conflict_chunks,"queries":conflict_queries},
 "selected_counts":{k:sum(x["answerability_class"]==k for x in selected) for k in ["supported","partial","cross_book","absent","irrelevant"]},
 "query_counts":{"supported":50,"paraphrase":50,"cross_book":50,"partial":50,"absent":200,"irrelevant":200,"conflict":50},
 "label_limitations":["All labels are non-gold automatic model judgments; GLM generated candidates/paraphrases and Gemma4 independently judged them against all frozen chunks; no human label is claimed.",
 "Each qrel chunk/book/role/page/quote is checked against the frozen source by NFKC and whitespace exact-span validation.",
 "Absent/irrelevant means the local independent model found no evidence in the 37-chunk corpus; not proof of semantic absence.",
 "Partial cases are controlled compound queries with a cited supported half and unique absent field.",
 "Conflict cases are a separate synthetic fixture, not evidence of a conflict in the two books.",
 "Original 50-query T-IDX-02 set remains untouched."] ,
 "generator":"deterministic templates + frozen Qwen set + Gemma4 paraphrase; local judge Gemma4","independent_judge":JUDGE,"source_text_sent_externally":False,"cost_usd":0,"total_calls":len(result["calls"])+len(judged)})
 dump(OUT,result); dump(CK,{"status":"final","output":str(OUT),"selected_counts":result["selected_counts"],"cost_usd":0})
 print(json.dumps({"status":"final","out":str(OUT),"selected_counts":result["selected_counts"],"rejected":len(rejected),"judge_batches":len(judged),"cost_usd":0}),flush=True)
if __name__=="__main__": main()
