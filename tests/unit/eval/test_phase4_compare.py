from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
import pytest
MODULE=Path(__file__).resolve().parents[3]/"eval/suites/phase4/compare_rankings.py"
SPEC=importlib.util.spec_from_file_location("b2c_phase4_compare_test",MODULE); assert SPEC and SPEC.loader
ev=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ev)
def case(tmp_path,auto=False):
 chunks=tmp_path/"chunks.jsonl"; c={"chunk_id":"c1","book_id":"main","role":"spine","pages":[3],"text":"A vector has magnitude and direction in this example."}
 chunks.write_text(json.dumps(c)+"\n",encoding="utf-8")
 dataset=tmp_path/"queries.jsonl"; label="model_auto_validated" if auto else "human_adjudicated"
 row={"schema_version":"b2c-retrieval-gold/1.0","query_id":"q1","query":"What properties define a vector?","split":"test","question_type":"direct","query_adjudication":label,"qrels":[{"chunk_id":"c1","relevance":3,"book_id":"main","role":"spine","pages":[3],"evidence_quote":c["text"],"adjudication":label}]}
 dataset.write_text(json.dumps(row)+"\n",encoding="utf-8")
 common={"corpus_sha256":ev.corpus_fingerprint([chunks]),"dataset_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),"rankings":{"q1":["c1"]}}
 base=tmp_path/"base.json"; cand=tmp_path/"cand.json"
 base.write_text(json.dumps({"retriever":"lexical",**common}),encoding="utf-8"); cand.write_text(json.dumps({"retriever":"graph",**common}),encoding="utf-8")
 return chunks,dataset,base,cand
def test_paired_pins_and_delta(tmp_path):
 chunks,dataset,base,cand=case(tmp_path)
 got=ev.compare(dataset_path=dataset,chunk_paths=[chunks],baseline_path=base,candidate_path=cand,k=5)
 assert got["status"]=="paired_evaluation" and got["gold_eligible"]
 assert got["candidate_minus_baseline"]["mrr@1"]==0
def test_auto_labels_are_diagnostic_only(tmp_path):
 chunks,dataset,base,cand=case(tmp_path,auto=True)
 with pytest.raises(ValueError):
  ev.compare(dataset_path=dataset,chunk_paths=[chunks],baseline_path=base,candidate_path=cand)
 got=ev.compare(dataset_path=dataset,chunk_paths=[chunks],baseline_path=base,candidate_path=cand,allow_auto_labeled=True)
 assert got["status"]=="diagnostic_only" and not got["gold_eligible"]
def test_fails_on_changed_corpus_or_query_set(tmp_path):
 chunks,dataset,base,cand=case(tmp_path); data=json.loads(cand.read_text()); data["rankings"]={"other":["c1"]}; cand.write_text(json.dumps(data))
 with pytest.raises(ev.ComparisonError,match="query IDs"):
  ev.compare(dataset_path=dataset,chunk_paths=[chunks],baseline_path=base,candidate_path=cand)
 data["rankings"]={"q1":["c1"]}; data["corpus_sha256"]="0"*64; cand.write_text(json.dumps(data))
 with pytest.raises(ev.ComparisonError,match="corpus hash"):
  ev.compare(dataset_path=dataset,chunk_paths=[chunks],baseline_path=base,candidate_path=cand)
