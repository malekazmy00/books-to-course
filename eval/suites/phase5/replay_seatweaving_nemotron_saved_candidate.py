from __future__ import annotations
import hashlib,json,re,sys
from pathlib import Path
from pydantic import Field
sys.path.insert(0,"/tmp/phase5-live-src/llm")
sys.path.insert(0,"/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0,"/tmp/phase5-live-src")
from b2c_contracts import (Block,CourseOutline,Lesson,LessonSpan,Module,PageRef,ParsedBook,StructuredChunk)
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_llm_gateway.errors import InvalidLLMOutput,LLMOutputTruncated
from b2c_pedagogy.lesson_enrichment import (_AssetsCandidate,_MCQCandidate,_exact_list_schema,
    _make_lesson_assets,generate_lesson_enrichments)

MODEL="nvidia/nemotron-3.5-lightning:free"
BASE_URL="https://openrouter.ai/api/v1"
COURSE="phase5-seatweaving-nemotron-5q-replay"
LESSON="seatweaving.steps2-7"
PROMPT="phase5-seatweaving-nemotron-v23-deterministic-glossary-alias"
ORIG_PREFIX="phase5-seatweaving-nemotron-lightning-once/phase5-raw-completions/"
store=ArtifactStore.from_settings(Settings())

def load_and_adapt():
    found=[]
    for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=store.bucket,Prefix=ORIG_PREFIX):
        for obj in page.get("Contents",[]):
            if obj["Key"].endswith("raw-completion.json"):
                rawbytes=store.s3.get_object(Bucket=store.bucket,Key=obj["Key"])["Body"].read()
                envelope=json.loads(rawbytes)
                if envelope.get("prompt_version","").endswith(":generate:attempt-1"):
                    found.append((obj["Key"],rawbytes,envelope))
    if len(found)!=1:
        raise RuntimeError(f"expected one saved free completion, found {len(found)}")
    old_key,old_bytes,envelope=found[0]
    value=json.loads(envelope["raw_completion"])
    # The original response is internally consistent as one-based indices: every
    # reference is in 1..N, none is zero, and at least one equals N. Convert as a
    # deterministic adapter only under that unambiguous condition.
    sentence_count=len(value.get("sentences",[]))
    refs=[]
    for item in value.get("summary_points",[])+value.get("questions",[]):
        refs.extend(item.get("sentence_indices",[]))
    for item in value.get("glossary",[]):
        if "sentence_index" in item:
            refs.append(item["sentence_index"])
        refs.extend(item.get("sentence_indices",[]))
    one_based=(bool(refs) and 0 not in refs and min(refs)>=1 and max(refs)==sentence_count)
    if one_based:
        for item in value.get("summary_points",[])+value.get("questions",[]):
            if "sentence_indices" in item:
                item["sentence_indices"]=[i-1 for i in item["sentence_indices"]]
        for item in value.get("glossary",[]):
            if "sentence_index" in item:
                item["sentence_index"]-=1
            if "sentence_indices" in item:
                item["sentence_indices"]=[i-1 for i in item["sentence_indices"]]
    aliases=0
    dropped_multi_sentence_glossary=0
    glossary=[]
    for term in value.get("glossary",[]):
        if "sentence_index" not in term and "sentence_indices" in term:
            indices=term.pop("sentence_indices")
            if not isinstance(indices,list) or not indices:
                raise ValueError("glossary sentence_indices alias must contain at least one index")
            if len(indices)>1:
                dropped_multi_sentence_glossary+=1
                continue
            term["sentence_index"]=indices[0]
            aliases+=1
        glossary.append(term)
    value["glossary"]=glossary
    adapted=json.dumps(value,ensure_ascii=False,separators=(",",":"))
    adapted_bytes=json.dumps({"model_id":MODEL,"prompt_version":PROMPT,
        "adapter":{"unambiguous_one_based_sentence_indices_converted":one_based,
                   "glossary_sentence_indices_to_sentence_index":aliases,
                   "dropped_ambiguous_multi_sentence_glossary_entries":dropped_multi_sentence_glossary,
                   "question_count_scope":"five-item diagnostic replay"},
        "source_sha256":envelope["source_sha256"],
        "original_raw_key":old_key.split("/")[-2],
        "original_raw_sha256":hashlib.sha256(old_bytes).hexdigest(),
        "raw_completion":adapted},ensure_ascii=False,sort_keys=True).encode()
    key=hashlib.sha256((envelope["source_sha256"]+PROMPT+adapted).encode()).hexdigest()
    ref=store.put(COURSE,"phase5-adapted-raw-completions",key,{"raw-completion.json":adapted_bytes})
    if store.read(ref,"raw-completion.json")!=adapted_bytes:
        raise RuntimeError("adapted raw readback mismatch")
    return old_key,hashlib.sha256(old_bytes).hexdigest(),envelope,adapted,ref,hashlib.sha256(adapted_bytes).hexdigest(),aliases,dropped_multi_sentence_glossary,one_based

class ReplayGateway:
    def __init__(self,inner,adapted,store,source_hash):
        self.inner=inner; self.adapted=adapted; self.store=store; self.source_hash=source_hash
        self.model_id=MODEL; self.raw_refs=[]; self.replay_pending=True
    @property
    def usage_totals(self): return self.inner.usage_totals
    def save(self,version,text,usage,attempts=1):
        data=json.dumps({"model_id":MODEL,"prompt_version":version,"source_sha256":self.source_hash,
                         "usage":usage,"attempts":attempts,"raw_completion":text},
                        ensure_ascii=False,sort_keys=True).encode()
        key=hashlib.sha256((self.source_hash+version+text).encode()).hexdigest()
        ref=self.store.put(COURSE,"phase5-raw-completions",key,{"raw-completion.json":data})
        if self.store.read(ref,"raw-completion.json")!=data: raise RuntimeError("solver raw readback mismatch")
        self.raw_refs.append({**ref.model_dump(mode="json"),"raw_sha256":hashlib.sha256(data).hexdigest()})
    def structured(self,messages,schema,*,prompt_version,temperature=0.0,max_tokens=None):
        if self.replay_pending and schema.__name__=="ExactQuestions":
            self.replay_pending=False
            return schema.model_validate_json(self.adapted)
        try:
            result,parsed=self.inner.structured_with_result(messages,schema,prompt_version=prompt_version,
                temperature=temperature,max_tokens=max_tokens)
        except LLMOutputTruncated as exc:
            self.save(prompt_version,exc.last_output,exc.usage)
            raise
        except InvalidLLMOutput as exc:
            self.save(prompt_version,exc.last_output,None,exc.attempts)
            raise
        self.save(prompt_version,result.text,result.usage,result.attempts)
        return parsed

def main():
    key=sys.stdin.read().strip()
    if not key: raise SystemExit("OpenRouter key required via stdin")
    old_key,old_sha,envelope,adapted,adapted_ref,adapted_sha,aliases,dropped_multi,one_based=load_and_adapt()
    pages=[(12,Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
           (13,Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
    pages=[(p,re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"," ",text)) for p,text in pages]
    source_hash=hashlib.sha256("\n\n".join(f"{p}\n{text}" for p,text in pages).encode()).hexdigest()
    book_id="seat-weaving-1917"
    blocks=[Block(block_id=f"seatweaving-j{p}",type="paragraph",text=text,refs=[PageRef(book_id=book_id,page_no=p)],
        section_path=["Caning; The Seven Steps"],ocr=False) for p,text in pages]
    book=ParsedBook(book_id=book_id,title="Seat Weaving (1917), pp.12-13",role="spine",page_count=2,
        toc_source="headings",parser={"name":"stored-groundtruth","version":"internal-test-only"},blocks=blocks)
    chunks=[StructuredChunk(chunk_id=f"seat-weaving-1917:j{p}",book_id=book_id,role="spine",
        section_path=["Caning; The Seven Steps"],block_ids=[f"seatweaving-j{p}"],pages=[p],text=text,
        token_count=max(1,len(text.split())),embedding_model="evaluation-source-not-embedded") for p,text in pages]
    outline=CourseOutline(course_id=COURSE,spine_book_id=book_id,supplementary_book_ids=[],requires_review=False,
        modules=[Module(module_id="m1",title="Caning",order=1,lessons=[Lesson(lesson_id=LESSON,
        title="Caning: The Seven Steps",order=1,spine_span=LessonSpan(start_block="seatweaving-j12",
        end_block="seatweaving-j13",pages=(12,13)),provenance="headings")])])
    candidate_schema=_exact_list_schema(_AssetsCandidate,"questions",_MCQCandidate,5)
    candidate=candidate_schema.model_validate_json(adapted)
    assets=_make_lesson_assets(LESSON,candidate,chunks,prompt_version=PROMPT)
    gateway_client.MAX_RETRIES=0
    gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS=()
    inner=LLMGateway(BASE_URL,MODEL,api_key=key,allow_remote=True,timeout_s=120,capture_content=False,reasoning_effort="none")
    gateway=ReplayGateway(inner,adapted,store,source_hash)
    print(json.dumps({"event":"saved_candidate_lint_pass","model":MODEL,"questions":len(assets.questions),
        "raw_sha256":old_sha,"adapted_sha256":adapted_sha,"glossary_aliases":aliases,"dropped_ambiguous_glossary":dropped_multi,
        "one_based_indices_converted":one_based,
        "raw_saved_key":old_key.split("/")[-2],"adapted_ref":adapted_ref.model_dump(mode="json")}),
        file=sys.stderr,flush=True)
    [lesson]=generate_lesson_enrichments(outline,book,chunks,gateway,question_count=5,max_tokens=6144,
        prompt_version=PROMPT,lesson_ids=[LESSON])
    payload=lesson.model_dump(mode="json")
    payload["evaluation_metadata"]={"canary":True,"gold_eligible":False,"model_id":MODEL,
        "source":"Seat Weaving (1917), pp.12-13, stored internal_test_only OCR reference",
        "source_sha256":source_hash,"source_chunk_ids":[c.chunk_id for c in chunks],
        "question_count_scope":5,"derivation":{"original_raw_key":old_key.split("/")[-2],
        "original_raw_sha256":old_sha,"adapter":"unambiguous one-based indices converted to zero-based; single glossary alias; ambiguous multi-sentence entry dropped",
        "one_based_indices_converted":one_based,
        "dropped_ambiguous_glossary_entries":dropped_multi},
        "provider_usage":gateway.usage_totals,"raw_completion_refs":gateway.raw_refs,
        "adapted_raw_ref":adapted_ref.model_dump(mode="json"),"adapted_raw_sha256":adapted_sha}
    out=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    digest=hashlib.sha256((source_hash+MODEL+PROMPT+"replay-artifact-v1").encode()).hexdigest()
    ref=store.put(COURSE,"phase5-enrichment-canary",digest,{"lesson-enrichment.json":out})
    read=store.read(ref,"lesson-enrichment.json")
    if read!=out: raise RuntimeError("artifact readback mismatch")
    print(json.dumps({"status":"artifact_saved","artifact_ref":ref.model_dump(mode="json"),
        "readback_sha256":hashlib.sha256(read).hexdigest(),"questions":len(lesson.questions),
        "automated_evaluation":lesson.automated_evaluation,"provider_usage":gateway.usage_totals,
        "solver_raw_refs":gateway.raw_refs,"adapted_raw_sha256":adapted_sha},sort_keys=True))

if __name__=="__main__": main()
