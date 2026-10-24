import sys,json,hashlib,urllib.request,urllib.error
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,"/tmp/phase5-live-src/synthesis")
sys.path.extend(["/app/packages/core/src","/app/packages/contracts/src"])
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0,"/tmp/phase5-live-src")
from b2c_pedagogy.lesson_enrichment import LessonEnrichment
BASE="https://openrouter.ai/api/v1";MODEL="deepseek/deepseek-v4-flash-0731"
COURSE="phase5-seatweaving-saved-raw-rebuilt-v1";ART="69d2694bb4f5b5a2bc1569322c5f68fe2eff2c8af85d91aaa4768421519025a4"
ARTKEY="77700e9b931e1f7a65dd6eea03318ceb6c887f395b38333e843382c2e41ddb95"
PROMPT="phase5-saved-raw-rebuilt-v1";MAX=400;CAP=Decimal("0.005")
def get_json(url,key):
 req=urllib.request.Request(url,headers={"Authorization":"Bearer "+key})
 with urllib.request.urlopen(req,timeout=25) as r:return r.status,json.loads(r.read())
def main():
 key=sys.stdin.read().strip()
 if not key:raise SystemExit("OpenRouter key required")
 store=ArtifactStore.from_settings(Settings())
 blob=store.s3.get_object(Bucket=store.bucket,Key=f"{COURSE}/phase5-enrichment-canary/{ARTKEY}/lesson-enrichment.json")["Body"].read()
 if hashlib.sha256(blob).hexdigest()!=ART:raise RuntimeError("saved artifact digest mismatch")
 lesson=LessonEnrichment.model_validate_json(blob)
 mcqs=[{"qid":q.qid,"stem":q.stem,"options":q.options} for q in lesson.questions]
 blind_messages=[
  {"role":"system","content":"Answer each multiple-choice question with no lesson, source, or evidence. Return exactly one JSON object with a choices array of zero-based option indices in question order. No explanation or extra keys."},
  {"role":"user","content":json.dumps({"mcqs":mcqs,"expected_count":len(mcqs)},ensure_ascii=False)}]
 status,credits=get_json(BASE+"/credits",key);_,catalog=get_json(BASE+"/models",key)
 model=next((m for m in catalog.get("data",[]) if m.get("id")==MODEL),None)
 if not model:raise RuntimeError("current DeepSeek model not found in catalog")
 pricing=model.get("pricing") or {};pin=Decimal(str(pricing.get("prompt","")));pout=Decimal(str(pricing.get("completion","")))
 if pin<=0 or pout<=0:raise RuntimeError("current model price metadata missing")
 context_key=hashlib.sha256((ART+PROMPT+"with_context").encode()).hexdigest()
 raw_obj=f"{COURSE}/phase5-raw-completions/{context_key}/raw-completion.json"
 previous=json.loads(store.s3.get_object(Bucket=store.bucket,Key=raw_obj)["Body"].read())
 previous_usage=previous.get("usage") or {}
 previous_cost=Decimal(str(previous_usage.get("cost",0)))
 chars=sum(len(json.dumps(m,ensure_ascii=False)) for m in blind_messages)
 input_bound=2*chars
 estimated=Decimal(input_bound)*pin+Decimal(MAX)*pout
 total_est=previous_cost+estimated
 data=credits.get("data",{});remaining=Decimal(str(data.get("total_credits",0)))-Decimal(str(data.get("total_usage",0)))
 preflight={"credits_http":status,"total_credits":str(data.get("total_credits")),"total_usage":str(data.get("total_usage")),
  "remaining":str(remaining),"prompt_price":str(pin),"completion_price":str(pout),"blind_prompt_chars":chars,
  "conservative_input_token_bound":input_bound,"output_cap":MAX,"prior_context_reported_cost":str(previous_cost),
  "blind_max_estimated_cost":str(estimated),"pair_total_conservative_cost":str(total_est),"cost_cap":str(CAP)}
 if total_est>CAP or estimated>remaining:raise RuntimeError("pair cost ceiling cannot be guaranteed")
 payload=json.dumps({"model":MODEL,"messages":blind_messages,"temperature":0,"max_tokens":MAX,
    "reasoning_effort":"none","provider":{"allow_fallbacks":False}},ensure_ascii=False).encode()
 req=urllib.request.Request(BASE+"/chat/completions",data=payload,method="POST",
  headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=180) as resp:response=json.loads(resp.read())
 except urllib.error.HTTPError as exc:
  body=exc.read().decode("utf-8",errors="replace");raw=json.dumps({"lane":"blind","http_status":exc.code,"body":body},ensure_ascii=False).encode()
  digest=hashlib.sha256(raw).hexdigest();ref=store.put(COURSE,"phase5-live-solver-evaluation",digest,{"blind-error.json":raw});store.read(ref,"blind-error.json")
  print(json.dumps({"status":"blind_http_failure","http_status":exc.code,"preflight":preflight,"raw_sha256":digest}))
  return
 choices=response.get("choices") or [];content=choices[0].get("message",{}).get("content") if choices else None
 usage=response.get("usage") or {};finish=choices[0].get("finish_reason") if choices else None
 raw=json.dumps({"lane":"blind","model":MODEL,"artifact_sha256":ART,"prompt_version":PROMPT+":blind",
  "usage":usage,"finish_reason":finish,"raw_completion":content},ensure_ascii=False,sort_keys=True).encode()
 digest=hashlib.sha256(raw).hexdigest();ref=store.put(COURSE,"phase5-raw-completions",
  hashlib.sha256((ART+PROMPT+"blind").encode()).hexdigest(),{"raw-completion.json":raw})
 if store.read(ref,"raw-completion.json")!=raw:raise RuntimeError("blind raw readback mismatch")
 result={"lane":"blind","usage":usage,"finish_reason":finish,"raw_sha256":digest,"raw_object_key":ref.key}
 if content is None:
  result["status"]="missing_text_content"
 else:
  normalized=content.strip();f=chr(96)*3
  if normalized.startswith(f+"json"):normalized=normalized[len(f+"json"):]
  elif normalized.startswith(f):normalized=normalized[len(f):]
  if normalized.endswith(f):normalized=normalized[:-len(f)]
  try:answers=json.loads(normalized.strip()).get("choices")
  except Exception:answers=None
  if not isinstance(answers,list) or len(answers)!=20 or any(not isinstance(x,int) for x in answers):
   result["status"]="invalid_or_truncated_json"
  elif any(x<0 or x>=len(q.options) for x,q in zip(answers,lesson.questions,strict=True)):
   result["status"]="invalid_choice_index"
  else:
   accuracy=sum(x==q.answer_index for x,q in zip(answers,lesson.questions,strict=True))/20
   result.update({"status":"scored","choices":answers,"accuracy":accuracy})
 cost=usage.get("cost")
 if cost is None:
  inp=usage.get("prompt_tokens");out=usage.get("completion_tokens")
  cost=(Decimal(str(inp))*pin+Decimal(str(out))*pout) if isinstance(inp,int) and isinstance(out,int) else None
 else:cost=Decimal(str(cost))
 result["calculated_cost_usd"]=None if cost is None else str(cost)
 after_status,after=get_json(BASE+"/credits",key);afterdata=after.get("data",{})
 evaluation={"artifact_sha256":ART,"model":MODEL,"preflight":preflight,"context_call":{
  "status":"truncated_no_message_content","usage":previous_usage,"raw_sha256":hashlib.sha256(json.dumps(previous,sort_keys=True,ensure_ascii=False).encode()).hexdigest()},
  "blind_call":result,"pair_gate_pass":False,"credits_after_http":after_status,
  "credits_after":{"total_credits":afterdata.get("total_credits"),"total_usage":afterdata.get("total_usage")}}
 out=json.dumps(evaluation,ensure_ascii=False,sort_keys=True,indent=2).encode();outkey=hashlib.sha256(out).hexdigest()
 ref_eval=store.put(COURSE,"phase5-live-solver-evaluation",outkey,{"solver-evaluation.json":out})
 back=store.read(ref_eval,"solver-evaluation.json")
 if back!=out:raise RuntimeError("solver report readback mismatch")
 print(json.dumps({"status":result["status"],"accuracy":result.get("accuracy"),
  "usage":usage,"reported_cost":usage.get("cost"),"calculated_cost_usd":result.get("calculated_cost_usd"),
  "preflight":preflight,"credits_after":{"total_credits":afterdata.get("total_credits"),"total_usage":afterdata.get("total_usage")},
  "raw_sha256":digest,"evaluation_artifact_ref":ref_eval.model_dump(mode="json"),
  "evaluation_readback_sha256":hashlib.sha256(back).hexdigest(),"gate_pass":False},sort_keys=True))
if __name__=="__main__":main()
