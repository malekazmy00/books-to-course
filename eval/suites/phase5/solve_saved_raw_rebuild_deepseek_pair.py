import sys,json,hashlib,urllib.request,urllib.error,decimal
from pathlib import Path
sys.path.extend(["/app/packages/core/src","/app/packages/contracts/src"]);sys.path.insert(0,"/tmp/phase5-live-src/synthesis")
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0,"/tmp/phase5-live-src")
from b2c_pedagogy.lesson_enrichment import LessonEnrichment
from decimal import Decimal
MODEL="deepseek/deepseek-v4-flash-0731"
BASE="https://openrouter.ai/api/v1"
COURSE="phase5-seatweaving-procedure-template-v2"
ARTIFACT_KEY="0a1dd1fad8c675543dd1885fa63bb47ed0172b5b39a80d0bc3b794c27b3497a0"
ARTIFACT_SHA="2a5f8c7dec13ee3f6779f6ca795e1b1fbd0205841e814ac4803ee828e9dcb88d"
PROMPT="phase5-numbered-procedure-v2"
MAX_TOKENS=300
COST_CAP=Decimal("0.005")
def get_json(url,key):
 req=urllib.request.Request(url,headers={"Authorization":"Bearer "+key})
 with urllib.request.urlopen(req,timeout=25) as r:return r.status,json.loads(r.read())
def main():
 key=sys.stdin.read().strip()
 if not key: raise SystemExit("OpenRouter key required")
 store=ArtifactStore.from_settings(Settings())
 obj_key=f"{COURSE}/phase5-enrichment-canary/{ARTIFACT_KEY}/lesson-enrichment.json"
 artifact=store.s3.get_object(Bucket=store.bucket,Key=obj_key)["Body"].read()
 if hashlib.sha256(artifact).hexdigest()!=ARTIFACT_SHA: raise RuntimeError("lesson artifact digest mismatch")
 lesson=LessonEnrichment.model_validate_json(artifact)
 chunks=[]
 pages=[(12,Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
        (13,Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
 for page,text in pages:
  chunks.append({"chunk_id":f"seat-weaving-1917:j{page}","page":page,"role":"spine","text":text})
 mcqs=[{"qid":q.qid,"stem":q.stem,"options":q.options} for q in lesson.questions]
 if len(mcqs)!=20: raise RuntimeError("unexpected MCQ count")
 context_messages=[
  {"role":"system","content":"Solve each multiple-choice question using only the provided lesson and source passages. Return exactly one JSON object with a choices array of zero-based option indices in question order. Do not include explanations or extra keys."},
  {"role":"user","content":json.dumps({"lesson_id":lesson.lesson_id,
    "lesson_sentences":[{"sid":s.sid,"text_en":s.text_en,"citations":s.citations} for s in lesson.draft.sentences],
    "source_chunks":chunks,"mcqs":mcqs,"expected_count":len(mcqs)},ensure_ascii=False)}]
 blind_messages=[
  {"role":"system","content":"Answer every multiple-choice question with no lesson, source, or evidence. If uncertain, choose uniformly among options. Return exactly one JSON object with a choices array of zero-based option indices in question order."},
  {"role":"user","content":json.dumps({"mcqs":mcqs,"expected_count":len(mcqs)},ensure_ascii=False)}]
 char_bound=sum(len(json.dumps(m,ensure_ascii=False)) for m in context_messages+blind_messages)
 s,c=get_json(BASE+"/credits",key)
 _,catalog=get_json(BASE+"/models",key)
 entry=next((x for x in catalog.get("data",[]) if x.get("id")==MODEL),None)
 if entry is None: raise RuntimeError("DeepSeek model missing from current catalog")
 pricing=entry.get("pricing") or {}
 input_price=Decimal(str(pricing.get("prompt","")))
 output_price=Decimal(str(pricing.get("completion","")))
 if input_price<=0 or output_price<=0: raise RuntimeError("positive current model prices not available")
 conservative_input_tokens=2*char_bound
 worst=Decimal(conservative_input_tokens)*input_price+Decimal(2*MAX_TOKENS)*output_price
 credits=c.get("data",{})
 remaining=Decimal(str(credits.get("total_credits",0)))-Decimal(str(credits.get("total_usage",0)))
 if worst>COST_CAP or worst>remaining:
  raise RuntimeError("conservative two-call estimate exceeds cost cap or remaining credits")
 preflight={"credits_http":s,"total_credits":str(credits.get("total_credits")),
  "total_usage":str(credits.get("total_usage")),"remaining":str(remaining),
  "input_price":str(input_price),"output_price":str(output_price),
  "input_char_count":char_bound,"conservative_input_token_bound":conservative_input_tokens,
  "max_output_tokens_per_call":MAX_TOKENS,"estimated_max_cost_usd":str(worst),"cost_cap_usd":str(COST_CAP)}
 results=[]
 for lane,messages in (("with_context",context_messages),("blind",blind_messages)):
  payload=json.dumps({"model":MODEL,"messages":messages,"temperature":0,
      "max_tokens":MAX_TOKENS,"reasoning_effort":"none",
      "provider":{"allow_fallbacks":False}},ensure_ascii=False).encode()
  req=urllib.request.Request(BASE+"/chat/completions",data=payload,method="POST",
      headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
  try:
   with urllib.request.urlopen(req,timeout=180) as resp:
    response=json.loads(resp.read())
  except urllib.error.HTTPError as exc:
   body=exc.read().decode("utf-8",errors="replace")
   raw=json.dumps({"lane":lane,"http_status":exc.code,"body":body},ensure_ascii=False).encode()
   digest=hashlib.sha256(raw).hexdigest();ref=store.put(COURSE,"phase5-live-solver-evaluation",digest,{"provider-error.json":raw})
   store.read(ref,"provider-error.json")
   print(json.dumps({"status":"solver_http_failure","lane":lane,"http_status":exc.code,
      "calls_completed":len(results),"preflight":preflight,"error_object_sha256":digest}))
   return
  usage=response.get("usage") or {}; choice=response.get("choices") or []
  content=choice[0].get("message",{}).get("content","") if choice else ""
  request_audit={"mcq_item_fields":["qid","stem","options"],
      "answer_index_sent":False,"rationale_sent":False,"citation_or_source_evidence_sent":lane=="with_context",
      "raw_payload_sha256":hashlib.sha256(payload).hexdigest()}
  raw=json.dumps({"lane":lane,"model":MODEL,"prompt_version":PROMPT+":"+lane,
      "artifact_sha256":ARTIFACT_SHA,"request_audit":request_audit,
      "usage":usage,"response_model":response.get("model"),
      "finish_reason":choice[0].get("finish_reason") if choice else None,
      "raw_completion":content},ensure_ascii=False,sort_keys=True).encode()
  digest=hashlib.sha256(raw).hexdigest();obj=store.put(COURSE,"phase5-raw-completions",hashlib.sha256((ARTIFACT_SHA+PROMPT+lane).encode()).hexdigest(),{"raw-completion.json":raw})
  if store.read(obj,"raw-completion.json")!=raw:raise RuntimeError("solver raw readback mismatch")
  reported=usage.get("cost")
  if reported is None:
   inp=usage.get("prompt_tokens",usage.get("input_tokens"));out=usage.get("completion_tokens",usage.get("output_tokens"))
   calc=(Decimal(str(inp))*input_price+Decimal(str(out))*output_price) if isinstance(inp,int) and isinstance(out,int) else None
  else:calc=Decimal(str(reported))
  normalized=content.strip();fence=chr(96)*3
  if normalized.startswith(fence+"json"):normalized=normalized[len(fence+"json"):]
  elif normalized.startswith(fence):normalized=normalized[len(fence):]
  if normalized.endswith(fence):normalized=normalized[:-len(fence)]
  try:choices_data=json.loads(normalized.strip()).get("choices")
  except Exception:
   results.append({"lane":lane,"accuracy":None,"status":"invalid_json_or_truncated",
      "request_audit":request_audit,"reported_usage":usage,
      "calculated_cost_usd":None if calc is None else str(calc),"raw_sha256":digest,"raw_object_key":obj.key})
   if calc is not None and sum(Decimal(x["calculated_cost_usd"]) for x in results if x["calculated_cost_usd"] is not None)>COST_CAP:
    print(json.dumps({"status":"cost_cap_stop","lane":lane,"results":results,"preflight":preflight}));return
   continue
  if not isinstance(choices_data,list) or len(choices_data)!=20 or any(not isinstance(x,int) for x in choices_data):
   results.append({"lane":lane,"accuracy":None,"status":"invalid_response_shape",
      "request_audit":request_audit,"reported_usage":usage,
      "calculated_cost_usd":None if calc is None else str(calc),"raw_sha256":digest,"raw_object_key":obj.key})
   if calc is not None and sum(Decimal(x["calculated_cost_usd"]) for x in results if x["calculated_cost_usd"] is not None)>COST_CAP:
    print(json.dumps({"status":"cost_cap_stop","lane":lane,"results":results,"preflight":preflight}));return
   continue
  if any(x<0 or x>=len(q.options) for x,q in zip(choices_data,lesson.questions,strict=True)):
   results.append({"lane":lane,"accuracy":None,"status":"invalid_choice_index",
      "request_audit":request_audit,"reported_usage":usage,
      "calculated_cost_usd":None if calc is None else str(calc),"raw_sha256":digest,"raw_object_key":obj.key})
   if calc is not None and sum(Decimal(x["calculated_cost_usd"]) for x in results if x["calculated_cost_usd"] is not None)>COST_CAP:
    print(json.dumps({"status":"cost_cap_stop","lane":lane,"results":results,"preflight":preflight}));return
   continue
  accuracy=sum(x==q.answer_index for x,q in zip(choices_data,lesson.questions,strict=True))/20
  results.append({"lane":lane,"choices":choices_data,"accuracy":accuracy,"request_audit":request_audit,"reported_usage":usage,
      "calculated_cost_usd":None if calc is None else str(calc),"raw_sha256":digest,
      "raw_object_key":obj.key})
  if calc is not None and sum(Decimal(x["calculated_cost_usd"]) for x in results if x["calculated_cost_usd"] is not None)>COST_CAP:
   print(json.dumps({"status":"cost_cap_stop","lane":lane,"results":results,"preflight":preflight}))
   return
 # Read credits after the pair; this is not a usage source if provider accounting lags.
 s_after,c_after=get_json(BASE+"/credits",key); after=c_after.get("data",{})
 eval_data={"artifact_sha256":ARTIFACT_SHA,"model_id":MODEL,"preflight":preflight,
   "calls":results,"with_context_threshold":0.95,"blind_ceiling":0.30,
   "gate_pass":len(results)==2 and results[0]["accuracy"]>=0.95 and results[1]["accuracy"]<=0.30,
   "credits_after_http":s_after,"credits_after":{"total_credits":after.get("total_credits"),"total_usage":after.get("total_usage")}}
 out=json.dumps(eval_data,ensure_ascii=False,sort_keys=True,indent=2).encode()
 ref=store.put(COURSE,"phase5-live-solver-evaluation",hashlib.sha256(out).hexdigest(),{"solver-evaluation.json":out})
 back=store.read(ref,"solver-evaluation.json")
 if back!=out:raise RuntimeError("solver evaluation readback mismatch")
 print(json.dumps({"status":"solver_pair_complete","artifact_ref":ref.model_dump(mode="json"),
    "readback_sha256":hashlib.sha256(back).hexdigest(),"preflight":preflight,
    "results":[{"lane":x["lane"],"accuracy":x["accuracy"],"request_audit":x["request_audit"],"usage":x["reported_usage"],
      "status":x.get("status","scored"),"calculated_cost_usd":x.get("calculated_cost_usd"),"raw_sha256":x["raw_sha256"]} for x in results],
    "gate_pass":eval_data["gate_pass"],"credits_after":eval_data["credits_after"]},sort_keys=True))
if __name__=="__main__":main()
