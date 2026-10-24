#!/usr/bin/env python3
"""Build frozen, local, non-gold Phase3 retrieval controls and lexical calibration."""
from __future__ import annotations
import hashlib
import itertools
import json
import unicodedata
from pathlib import Path
from b2c_contracts.models import StructuredChunk
from b2c_indexing import lexical_search

ROOT=Path("/root/projects/book2course"); D=Path("/mnt/wsl/b2cdata/phase1-sources")
CHUNK_FILES=[D/"lane-primary-30-chunks.jsonl",D/"thinkstats-supplement-7-chunks.jsonl"]
POS=D/"phase3-qa-50.jsonl"; CHECKPOINT=D/"phase3-auto-audit-v2-checkpoint.json"
OUT=D/"phase3-auto-audit-v2.jsonl"; SUMMARY=D/"phase3-auto-audit-v2-summary.json"
CAL=D/"phase3-threshold-calibration-expanded.json"; CONFLICT=D/"phase3-conflict-fixture-50.json"
EXPECTED={"lane-primary-30-chunks.jsonl":"1db9b1b426811f93fdf96f362c6c64eeae13a57cec58ea2888c0112ba2ce3f4e",
"thinkstats-supplement-7-chunks.jsonl":"210422e6942c8186fc45836caade6cb29c51cc445fb638f873b3ba71d57d9e4b"}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def readj(p): return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
def writej(p,obj): p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def norm(s): return " ".join(unicodedata.normalize("NFKC",s).casefold().split())
def exact(q,t): return norm(q) in norm(t)
def main():
    chunks=readj(CHUNK_FILES[0])+readj(CHUNK_FILES[1]); by={c["chunk_id"]:c for c in chunks}
    hashes={p.name:sha(p) for p in CHUNK_FILES}
    if len(chunks)!=37 or hashes!=EXPECTED: raise RuntimeError("Frozen 37-chunk corpus changed; abort")
    base=readj(POS)
    if len(base)!=50 or sha(POS)!="4361df96655fb0a2ba70466ca16936c67fc409c74368fbc44925270bd19d8b99": raise RuntimeError("Frozen 50-query base changed")
    cp=json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    paras=[r for r in cp.get("selected",[]) if r.get("query_id","").startswith("para-")]
    if len(paras)!=20: raise RuntimeError(f"Expected 20 saved Gemma paraphrases, got {len(paras)}")
    rows=[]
    def copyqrel(q):
        x={k:q[k] for k in ["chunk_id","relevance","book_id","role","pages","evidence_quote"]}
        x["adjudication"]="model_auto_validated"; return x
    def add(qid,q,cls,group,qrels,qtype="direct",provenance=""):
        for x in qrels:
            s=by.get(x["chunk_id"])
            if not s or (x["book_id"],x["role"],x["pages"])!=(s["book_id"],s["role"],s["pages"]) or not exact(x["evidence_quote"],s["text"]):
                raise RuntimeError(f"exact source/page provenance failed: {qid}/{x.get('chunk_id')}")
        rows.append({"schema_version":"b2c-retrieval-gold/1.0","query_id":qid,"query":q,"split":"development",
        "question_type":qtype,"answerability_class":cls,"query_adjudication":"model_auto_validated",
        "label_origin":"deterministic_control_or_model_auto","generation_group":group,"gold_eligible":False,
        "label_provenance":provenance,"qrels":qrels,"source_manifest_sha256":"f8b91470da88691d3b2a4c92306c9fed0387f14e7f4d37f37ad7168aad10dd62"})
    # Existing supported examples are retained; exact source/page/quote validated.
    for i,b in enumerate(base,1):
        add(f"sup-{i:03d}",b["query"],"supported",b["query_id"],[copyqrel(x) for x in b["qrels"]],
            qtype="direct",provenance="Frozen Qwen3-Coder QA; exact source/page/quote validation")
    # 20 model-generated paraphrases saved before Gemma resource pivot; same-model semantic self-check is not claimed.
    for r in paras:
        add(r["query_id"],r["query"],"paraphrase",r.get("generation_group",r["query_id"]),[copyqrel(x) for x in r["qrels"]],
            qtype="paraphrase",provenance="Gemma4 generated; citation inherited and exact-validated; no independent semantic judge")
    # Two-source retrieval controls are deterministic composites of verified questions.
    lane=[r for r in base if r["qrels"][0]["role"]=="spine"]; supp=[r for r in base if r["qrels"][0]["role"]=="supplementary"]
    for i,(a,b) in enumerate(itertools.islice(itertools.product(lane,supp),50),1):
        q=f"Answer both source-specific questions: primary book: {a['query']} supplementary book: {b['query']}"
        add(f"xbook-{i:03d}",q,"cross_book",f"{a['query_id']}|{b['query_id']}",
            [copyqrel(a["qrels"][0]),copyqrel(b["qrels"][0])],"cross_book",
            "Deterministic composition of two distinct, exact-validated source-cited questions")
    # Controlled partial support: exact supported question plus unique missing fact ID.
    for i,b in enumerate(base,1):
        q=b["query"].rstrip(" ?")+f", and what is the value of unreported control field B2C_MISSING_{i:03d}?"
        add(f"partial-{i:03d}",q,"partial",b["query_id"],[copyqrel(b["qrels"][0])],
            qtype="direct",provenance="Controlled compound query; supported clause exact-cited, second field absent by unique identifier")
        rows[-1]["missing_subquestion"]=f"B2C_MISSING_{i:03d}"
    # Deterministic hard-negative candidates: every core concept/name is checked
    # absent from every source chunk before retaining; labels are lexical controls,
    # not claims of human semantic adjudication.
    topics=[
    "Cox proportional hazards regression","Gaussian copula estimation","Fisher information matrix for mixture models","Dirichlet process posterior","Bayes factor for nested models","Markov chain Monte Carlo convergence diagnostics","Hawkes self-exciting point process","empirical likelihood confidence regions","generalized method of moments","Dempster Shafer evidence theory",
    "extreme value theory for block maxima","survival analysis with competing risks","nonparametric density bandwidth selection","conditional value at risk estimation","Kalman filtering for latent states","hidden Markov model decoding","quantile regression under censoring","causal propensity score weighting","instrumental variable estimation","random effects meta-analysis",
    "bootstrap bias corrected accelerated intervals","Laplace approximation for Bayesian evidence","variational inference for latent Dirichlet allocation","stochastic volatility state space models","copula tail dependence coefficients","sequential probability ratio testing","false discovery rate control by Benjamini Hochberg","missing not at random imputation","Poisson regression with an offset term","negative binomial overdispersion estimation",
    "Rao Blackwellized particle filters","Fisher scoring for logistic regression","generalized estimating equations","Markov random field partition functions","empirical process Donsker theorem","Wald test under boundary constraints","posterior predictive checks for hierarchical models","spike and slab prior credible intervals","noncentral chi square power calculation","cross validation for censored survival data",
    "Anderson Darling goodness of fit statistic","permutation test for clustered data","Neyman Pearson lemma with composite hypotheses","minimax risk for shrinkage estimators","de Finetti representation theorem","renewal reward process limit theorem","branching process extinction probability","stochastic differential equation likelihood","spatial point process intensity estimation","change point detection with fused lasso"]
    absent_forms=["What exact likelihood is used for {x}?","Which assumptions are required to estimate {x} consistently?",
    "How is asymptotic variance derived for {x}?","Which numerical example demonstrates {x} in this book?"]
    countries=["Morocco","Japan","Brazil","Norway","Kenya","Portugal","Thailand","Chile","Ghana","Iceland","Vietnam","Peru","Nepal","Finland","Jordan","Mexico","Botswana","Croatia","Cambodia","Uruguay","Mongolia","Senegal","Belgium","Laos","Ecuador","Tunisia","Estonia","Bolivia","Namibia","Georgia","Malta","Rwanda","Paraguay","Lebanon","Slovenia","Oman","Tanzania","Cyprus","Zambia","Latvia","Fiji","Albania","Panama","Qatar","Lithuania","Armenia","Costa Rica","Madagascar","Luxembourg","Kuwait"]
    irr_forms=["What is the capital city of {x}?","Which official currency is used in {x}?","What is the largest city in {x}?","Which continent is {x} located in?"]
    def add_negative(qid,q,cls,group):
        # Machine-auditable exact term absence across the complete frozen corpus.
        subject=group.split(":",1)[1]
        if any(norm(subject) in norm(c["text"]) for c in chunks):
            return
        add(qid,q,cls,group,[],qtype="unanswerable",
            provenance="Deterministic controlled negative; named concept absent from every frozen source chunk; non-gold")
        rows[-1]["unanswerable_reason"]="Controlled evaluation query over a named concept absent from the complete frozen chunk text."
    for i,x in enumerate(topics,1):
        for j,form in enumerate(absent_forms,1):
            add_negative(f"absent-{(i-1)*4+j:04d}",form.format(x=x),"absent",f"absent-topic:{x}")
    for i,x in enumerate(countries,1):
        for j,form in enumerate(irr_forms,1):
            add_negative(f"irrelevant-{(i-1)*4+j:04d}",form.format(x=x),"irrelevant",f"irrelevant-country:{x}")
    # Separate contradiction corpus is a controlled test fixture, never merged
    # with the licensed book corpus or called real-book evidence.
    conf_chunks=[]; conf_queries=[]
    for i in range(1,51):
        fact=f"control_metric_{i:03d}"; va=10+i; vb=100+i
        a={"chunk_id":f"synthetic-conflict-{i:03d}-a","book_id":f"synthetic-source-a-{i:03d}","role":"spine","pages":[1],
           "text":f"Synthetic fixture {i}. For the identical study and scope, {fact} equals {va} units."}
        b={"chunk_id":f"synthetic-conflict-{i:03d}-b","book_id":f"synthetic-source-b-{i:03d}","role":"supplementary","pages":[1],
           "text":f"Synthetic fixture {i}. For the identical study and scope, {fact} equals {vb} units."}
        conf_chunks.extend([a,b]); conf_queries.append({"query_id":f"conflict-{i:03d}",
        "query":f"For the identical study, what values do the two contradictory sources give for {fact}?",
        "question_type":"cross_book","answerability_class":"conflict","query_adjudication":"synthetic_by_construction",
        "label_provenance":"Contradictory values inserted by deterministic fixture; not a real-book claim",
        "qrels":[{"chunk_id":s["chunk_id"],"relevance":3,"book_id":s["book_id"],"role":s["role"],"pages":s["pages"],
        "evidence_quote":s["text"],"adjudication":"synthetic_by_construction"} for s in [a,b]]})
    writej(CONFLICT,{"schema":"b2c-phase3-conflict-fixture/1.0","gold_eligible":False,"chunks":conf_chunks,"queries":conf_queries})
    # score lexical baseline on immutable book chunks
    sc=[StructuredChunk(chunk_id=x["chunk_id"],book_id=x["book_id"],role=x["role"],section_path=x.get("section_path",[]),
    block_ids=x.get("block_ids") or [x["chunk_id"]],pages=x["pages"],text=x["text"],
    token_count=max(1,int(x.get("token_count",len(x["text"].split())))),embedding_model="phase3-control") for x in chunks]
    scored=[]
    for r in rows:
        ranked=lexical_search(r["query"],sc,top_k=len(sc))
        scored.append({**r,"score":float(ranked[0][1]) if ranked else 0.0,"top_chunk":ranked[0][0].chunk_id if ranked else None})
    # Group-preserving deterministic holdout: query variants of the same source
    # stay together; negative variants of one concept/country stay together.
    groups=sorted({r["generation_group"] for r in scored},key=lambda g:hashlib.sha256(("b2c-phase3-audit-v2|"+g).encode()).hexdigest())
    hold_groups=set(groups[:max(1,round(len(groups)*.25))])
    for r in scored: r["split"]="test" if r["generation_group"] in hold_groups else "development"
    train=[r for r in scored if r["split"]=="development" and r["answerability_class"] in {"supported","paraphrase","absent","irrelevant"}]
    test=[r for r in scored if r["split"]=="test" and r["answerability_class"] in {"supported","paraphrase","absent","irrelevant"}]
    def rates(xs,t):
        pos=[r for r in xs if r["answerability_class"] in {"supported","paraphrase"}]; neg=[r for r in xs if r["answerability_class"] in {"absent","irrelevant"}]
        fa=[r for r in neg if r["score"]>=t]; fn=[r for r in pos if r["score"]<t]
        return {"n":len(xs),"supported_n":len(pos),"negative_n":len(neg),
        "false_answer_n":len(fa),"false_answer_rate":len(fa)/len(neg) if neg else None,
        "false_abstention_n":len(fn),"false_abstention_rate":len(fn)/len(pos) if pos else None,
        "coverage":sum(r["score"]>=t for r in xs)/len(xs) if xs else None}
    values=sorted({r["score"] for r in train}); thresholds=[0.0]+[(a+b)/2 for a,b in zip(values,values[1:])]+[values[-1]+1e-9]
    def J(t):
        pos=[r for r in train if r["answerability_class"] in {"supported","paraphrase"}]
        neg=[r for r in train if r["answerability_class"] in {"absent","irrelevant"}]
        return (sum(r["score"]>=t for r in pos)/len(pos) if pos else 0)-(sum(r["score"]>=t for r in neg)/len(neg) if neg else 0)
    # Safety-first candidate: among thresholds with zero calibration false answers,
    # maximize supported coverage; if impossible, use above max negative score.
    safe=[t for t in thresholds if not any(r["score"]>=t for r in train if r["answerability_class"] in {"absent","irrelevant"})]
    threshold=max(safe,key=lambda t:sum(r["score"]>=t for r in train if r["answerability_class"] in {"supported","paraphrase"})) if safe else max(r["score"] for r in train if r["answerability_class"] in {"absent","irrelevant"})+1e-9
    cal={"schema":"b2c-tidx03-expanded-control/1.0","gold_eligible":False,"threshold":threshold,
    "method":"lexical max score; grouped 25% holdout by source-query group or named negative concept; safety-first calibration requires zero false answers on development negatives, then maximizes positive coverage",
    "calibration":rates(train,threshold),"test":rates(test,threshold),"test_classes":{},
    "split_groups":{"all":len(groups),"heldout":len(hold_groups),"seed":"b2c-phase3-audit-v2"},
    "sample_sizes":{"supported":sum(r["answerability_class"]=="supported" for r in scored),
    "paraphrase":sum(r["answerability_class"]=="paraphrase" for r in scored),"cross_book":sum(r["answerability_class"]=="cross_book" for r in scored),
    "partial":sum(r["answerability_class"]=="partial" for r in scored),"absent":sum(r["answerability_class"]=="absent" for r in scored),
    "irrelevant":sum(r["answerability_class"]=="irrelevant" for r in scored),"conflict_synthetic":len(conf_queries)},
    "source_sha256":hashes,"base_positive_sha256":sha(POS),
    "limitations":["All results are controlled automated labels, not human gold or empirical user questions.",
    "The absence control proves exact topic/name terms are missing from all frozen chunks; it does not prove every paraphrastic semantic match absent.",
    "Paraphrases were generated by Gemma4 and have no independent semantic adjudication; source citations remain exact-validated.",
    "Partial and conflict query classes are constructed fixtures; conflict uses separate synthetic passages.",
    "Threshold is diagnostic for this fixed 37-chunk lexical corpus. It is not installed in production and is not portable to dense/RRF."],
    "confidence_bound_note":"Zero errors among N held-out cases gives only an approximate rule-of-three 95% upper bound of 3/N; report exact counts; do not claim zero population risk."}
    for cls in ["supported","paraphrase","absent","irrelevant"]:
        cal["test_classes"][cls]=rates([r for r in test if r["answerability_class"]==cls],threshold)
    writej(CAL,cal)
    with OUT.open("w",encoding="utf-8") as f:
        for r in scored: f.write(json.dumps(r,ensure_ascii=False,separators=(",",":"))+"\n")
    # Validate the conflict fixture retrieves both contradictory chunks in top-2.
    conflict_check=[]
    for q in conf_queries:
        cset=[]
        for c in conf_chunks:
            cset.append(StructuredChunk(chunk_id=c["chunk_id"],book_id=c["book_id"],role=c["role"],section_path=[],block_ids=[c["chunk_id"]],pages=c["pages"],text=c["text"],token_count=len(c["text"].split()),embedding_model="synthetic"))
        ranked=lexical_search(q["query"],cset,top_k=2)
        hit={x.chunk_id for x,_ in ranked}; wanted={x["chunk_id"] for x in q["qrels"]}
        conflict_check.append(hit==wanted)
    summary={"status":"diagnostic_pass","dataset":str(OUT),"dataset_sha256":sha(OUT),"calibration":cal,
    "counts":cal["sample_sizes"],"source_qrel_exact_checks":sum(len(r["qrels"]) for r in rows),
    "conflict_fixture_recall_top2":sum(conflict_check)/len(conflict_check),"conflict_fixture_n":len(conflict_check),
    "phrase_absence_controls":sum(r["answerability_class"] in {"absent","irrelevant"} for r in rows),
    "model":{"paraphrase_generator":"gemma4:latest (same model as prior generation attempt)","local_route":"Ollama localhost:11434","source_sent_externally":False},
    "gate_decision":"T-IDX-03 diagnostic expanded; no production threshold selected. T-IDX-01 still requires an actually accepted outline artifact or a product-flow fixture explicitly labelled automated.",
    "limitations":cal["limitations"]}
    writej(SUMMARY,summary)
    print(json.dumps({"status":summary["status"],"counts":cal["sample_sizes"],"dataset_sha256":summary["dataset_sha256"],
    "threshold":threshold,"calibration":cal["calibration"],"test":cal["test"],"test_classes":cal["test_classes"],
    "conflict_fixture_recall_top2":summary["conflict_fixture_recall_top2"],"exact_qrel_checks":summary["source_qrel_exact_checks"]},ensure_ascii=False,indent=2))
if __name__=="__main__":main()
