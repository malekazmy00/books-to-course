"""One Atria proposal call plus product-path context/blind solver calls; no retries."""
from __future__ import annotations
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")
from b2c_contracts import (Block, CourseOutline, Lesson, LessonSpan, Module, PageRef, ParsedBook, StructuredChunk)
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_pedagogy.lesson_enrichment import generate_lesson_enrichments
MODEL="Atria-Dawn-Preview"
BASE_URL="https://api.atria-asi.ai/v1/chat/completions"
COURSE="phase5-seatweaving-atria-dawn-v24-20q"
LESSON="seatweaving.steps2-7"
PROMPT="phase5-seatweaving-atria-v24-proposal-only-json-text"
MAX_OUTPUT=4096
class AtriaGateway:
    model_id=MODEL
    def __init__(self,key,store,source_hash):
        self._key=key; self.store=store; self.source_hash=source_hash; self.calls=0; self.raw_refs=[]
        self.usage_totals={"api_calls":0,"input_tokens":0,"output_tokens":0,"cost_usd":None,"reported_cost_calls":0}
    def _persist(self,prompt_version,content,metadata):
        raw=json.dumps({"model_id":MODEL,"prompt_version":prompt_version,"source_sha256":self.source_hash,**metadata,"raw_completion":content},ensure_ascii=False,sort_keys=True).encode()
        key=hashlib.sha256((self.source_hash+prompt_version+content).encode()).hexdigest()
        ref=self.store.put(COURSE,"phase5-raw-completions",key,{"raw-completion.json":raw})
        if self.store.read(ref,"raw-completion.json")!=raw: raise RuntimeError("Atria raw completion readback mismatch")
        self.raw_refs.append({**ref.model_dump(mode="json"),"raw_sha256":hashlib.sha256(raw).hexdigest()})
    def structured(self,messages,schema,*,prompt_version,temperature=0.0,max_tokens=None):
        if self.calls>=3: raise RuntimeError("Atria canary reached its three-call cap")
        cap=min(MAX_OUTPUT,int(max_tokens or MAX_OUTPUT))
        payload=json.dumps({"model":MODEL,"messages":messages,"temperature":temperature,"max_tokens":cap},ensure_ascii=False).encode()
        req=urllib.request.Request(BASE_URL,data=payload,method="POST",headers={"Authorization":"Bearer "+self._key,"Content-Type":"application/json"})
        self.calls+=1
        try:
            with urllib.request.urlopen(req,timeout=180) as response:
                response_data=json.loads(response.read()); self.usage_totals["api_calls"]=int(self.usage_totals["api_calls"] or 0)+1
        except urllib.error.HTTPError as exc:
            body=exc.read().decode("utf-8",errors="replace")
            self._persist(prompt_version,body,{"http_status":exc.code,"error_response":True})
            raise RuntimeError(f"Atria chat-completions returned HTTP {exc.code}") from None
        choices=response_data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message",{}).get("content"),str):
            self._persist(prompt_version,json.dumps(response_data,ensure_ascii=False),{"malformed_response":True,"usage":response_data.get("usage")})
            raise ValueError("Atria returned no text completion")
        content=choices[0]["message"]["content"]; usage=response_data.get("usage") or {}
        in_tokens=usage.get("prompt_tokens",usage.get("input_tokens")); out_tokens=usage.get("completion_tokens",usage.get("output_tokens"))
        if isinstance(in_tokens,int): self.usage_totals["input_tokens"]=int(self.usage_totals["input_tokens"] or 0)+in_tokens
        if isinstance(out_tokens,int): self.usage_totals["output_tokens"]=int(self.usage_totals["output_tokens"] or 0)+out_tokens
        cost=usage.get("cost")
        if isinstance(cost,(int,float)):
            self.usage_totals["cost_usd"]=float(self.usage_totals["cost_usd"] or 0)+float(cost)
            self.usage_totals["reported_cost_calls"]=int(self.usage_totals["reported_cost_calls"] or 0)+1
        self._persist(prompt_version,content,{"usage":usage,"finish_reason":choices[0].get("finish_reason"),"response_model":response_data.get("model")})
        normalized=content.strip(); fence=chr(96)*3
        if normalized.startswith(fence+"json"): normalized=normalized[len(fence+"json"):]
        elif normalized.startswith(fence): normalized=normalized[len(fence):]
        if normalized.endswith(fence): normalized=normalized[:-len(fence)]
        return schema.model_validate(json.loads(normalized.strip()))
def main():
    key=sys.stdin.read().strip()
    if not key: raise SystemExit("Atria key required via stdin")
    raw_pages=[(12,Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),(13,Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
    source_hash=hashlib.sha256("\n\n".join(f"{p}\n{t}" for p,t in raw_pages).encode()).hexdigest()
    pages=[(p,re.sub(r"[\x00-\x08\x0b-\x1f\x7f]"," ",t)) for p,t in raw_pages]
    book_id="seat-weaving-1917"
    blocks=[Block(block_id=f"seatweaving-j{page}",type="paragraph",text=text,refs=[PageRef(book_id=book_id,page_no=page)],section_path=["Caning; The Seven Steps"],ocr=False) for page,text in pages]
    book=ParsedBook(book_id=book_id,title="Seat Weaving (1917), pp.12-13",role="spine",page_count=2,toc_source="headings",parser={"name":"stored-groundtruth","version":"internal-test-only"},blocks=blocks)
    chunks=[StructuredChunk(chunk_id=f"seat-weaving-1917:j{page}",book_id=book_id,role="spine",section_path=["Caning; The Seven Steps"],block_ids=[f"seatweaving-j{page}"],pages=[page],text=text,token_count=max(1,len(text.split())),embedding_model="evaluation-source-not-embedded") for page,text in pages]
    outline=CourseOutline(course_id=COURSE,spine_book_id=book_id,supplementary_book_ids=[],requires_review=False,modules=[Module(module_id="m1",title="Caning",order=1,lessons=[Lesson(lesson_id=LESSON,title="Caning: The Seven Steps",order=1,spine_span=LessonSpan(start_block="seatweaving-j12",end_block="seatweaving-j13",pages=(12,13)),provenance="headings")])])
    store=ArtifactStore.from_settings(Settings()); gateway=AtriaGateway(key,store,source_hash)
    print(json.dumps({"event":"generation_started","model":MODEL,"source_sha256":source_hash,"api_call_cap":3,"generation_output_cap":MAX_OUTPUT}),file=sys.stderr,flush=True)
    try:
        [lesson]=generate_lesson_enrichments(outline,book,chunks,gateway,question_count=20,max_tokens=MAX_OUTPUT,prompt_version=PROMPT,lesson_ids=[LESSON],question_repair_limit=0,max_question_top_up=0)
    except Exception as exc:
        print(json.dumps({"status":"product_path_failed","error_type":type(exc).__name__,"error":str(exc),"calls":gateway.calls,"provider_usage":gateway.usage_totals,"raw_completion_refs":gateway.raw_refs},sort_keys=True),flush=True); raise
    payload=lesson.model_dump(mode="json")
    payload["evaluation_metadata"]={"canary":True,"gold_eligible":False,"model_id":MODEL,"source":"Seat Weaving (1917), pp.12-13, stored internal_test_only OCR reference","source_sha256":source_hash,"source_chunk_ids":[chunk.chunk_id for chunk in chunks],"provider_usage":gateway.usage_totals,"raw_completion_refs":gateway.raw_refs}
    artifact_bytes=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    digest=hashlib.sha256((source_hash+MODEL+PROMPT+"final-artifact-v1").encode()).hexdigest()
    ref=store.put(COURSE,"phase5-enrichment-canary",digest,{"lesson-enrichment.json":artifact_bytes})
    readback=store.read(ref,"lesson-enrichment.json")
    if readback!=artifact_bytes: raise RuntimeError("Atria lesson artifact readback mismatch")
    print(json.dumps({"status":"artifact_saved","artifact_ref":ref.model_dump(mode="json"),"readback_sha256":hashlib.sha256(readback).hexdigest(),"calls":gateway.calls,"question_count":len(lesson.questions),"higher_bloom":sum(q.bloom!="remember" for q in lesson.questions),"automated_evaluation":lesson.automated_evaluation,"provider_usage":gateway.usage_totals,"raw_completion_refs":gateway.raw_refs},sort_keys=True))
if __name__=="__main__": main()
