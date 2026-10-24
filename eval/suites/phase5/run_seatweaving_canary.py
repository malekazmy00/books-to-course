"""Free Phase 5 canary using one stored, internal-test-only Seat Weaving page."""
from __future__ import annotations
import hashlib, json, sys
import re
from pathlib import Path
from typing import Annotated, Literal
sys.path.insert(0, "/tmp/phase5-live-src/llm")
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")
from pydantic import Field, create_model
from b2c_contracts import Block, PageRef, ParsedBook, StructuredChunk
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_llm_gateway import LLMGateway
from b2c_llm_gateway import client as gateway_client
from b2c_llm_gateway.errors import InvalidLLMOutput
from b2c_pedagogy.lesson_enrichment import (_AssetsCandidate, _GlossaryCandidate, _MCQCandidate,
                                             _Sentence, _SummaryPoint, _make_lesson_assets)

MODEL="nvidia/nemotron-3-super-120b-a12b:free"
COURSE="phase5-seatweaving-sequence-canary"
LESSON="seatweaving.steps2-7"
PROMPT="phase5-seatweaving-steps-v4-source-backed-options"

def main():
    key=sys.stdin.read().strip()
    if not key: raise SystemExit("OpenRouter key required via stdin")
    raw_pages=[(12,Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
               (13,Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
    original_source_hash=hashlib.sha256("\n\n".join(f"{page}\n{text}" for page,text in raw_pages).encode()).hexdigest()
    # OCR page separators/form-feed controls are layout, not prose; normalize them so
    # the model cannot return invalid raw JSON containing unescaped control characters.
    pages=[(page,re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", text)) for page,text in raw_pages]
    source_hash=hashlib.sha256("\n\n".join(f"{page}\n{text}" for page,text in pages).encode()).hexdigest()
    book_id="seat-weaving-1917"
    book=ParsedBook(book_id=book_id,title="Seat Weaving (1917), pp.12-13",role="spine",
        page_count=2,toc_source="headings",parser={"name":"stored-groundtruth","version":"internal-test-only"},
        blocks=[Block(block_id=f"seatweaving-j{page}",type="paragraph",text=text,
                      refs=[PageRef(book_id=book_id,page_no=page)],
                      section_path=["Caning; The Seven Steps"],ocr=False) for page,text in pages])
    chunks=[StructuredChunk(chunk_id=f"seat-weaving-1917:j{page}",book_id=book_id,role="spine",
        section_path=["Caning; The Seven Steps"],block_ids=[f"seatweaving-j{page}"],pages=[page],text=text,
        token_count=max(1,len(text.split())),embedding_model="evaluation-source-not-embedded")
        for page,text in pages]
    source_ids=tuple(chunk.chunk_id for chunk in chunks)
    citation=Literal.__getitem__(source_ids)
    Sentence=create_model("SeatWeavingSentence",__base__=_Sentence,
        citations=(list[citation],Field(min_length=1)))
    BoundedIndex = Annotated[int, Field(ge=0, le=7)]
    SummaryPoint=create_model("SeatWeavingSummaryPoint",__base__=_SummaryPoint,
        sentence_indices=(list[BoundedIndex],Field(min_length=1)))
    GlossaryCandidate=create_model("SeatWeavingGlossary",__base__=_GlossaryCandidate,
        sentence_index=(int,Field(ge=0,le=7)))
    QuestionCandidate=create_model("SeatWeavingMCQ",__base__=_MCQCandidate,
        sentence_indices=(list[BoundedIndex],Field(min_length=1)))
    Assets=create_model("SeatWeavingAssets",__base__=_AssetsCandidate,
        sentences=(list[Sentence],Field(min_length=8,max_length=8)),
        summary_points=(list[SummaryPoint],Field(min_length=3,max_length=10)),
        glossary=(list[GlossaryCandidate],Field(max_length=30)),
        questions=(list[QuestionCandidate],Field(min_length=10,max_length=10)))
    schema=Assets
    messages=[
      {"role":"system","content":(
        "Create an English lesson only from this stored book-page source. Return exactly 8 factual sentences (indices 0-7), at least 3 summary points, a concise glossary, and exactly 10 distinct MCQs. Every summary, glossary item, and MCQ must reference sentence indices only from 0 to 7. "
        "Every sentence, summary, glossary term and MCQ must cite the supplied source through sentence indices/chunk IDs. All sentences are primary. At least 8 questions are understand/apply/analyze/evaluate/create. "
        "This is the specialized 1917 seat-caning procedure across steps 2-7. Distribute ten questions across at least five different steps/transitions, with no more than two questions per step. Each question must test a source-specific fact-detail a student cannot answer from general weaving knowledge: exact named tool/material, stated material grade/number, operation order, condition, or exception. Ask directly for that detail, not a general concept. "
        "Do not invent or reorder operations, ask arithmetic, or use numbers unless the cited sentence states the exact number and role. Every correct option must preserve an exact name/number/detail appearing in its cited sentence. Each of the three distractors must be another verbatim phrase or minimal paraphrase from a DIFFERENT fact that is explicitly stated in the supplied passages. Never invent plausible-sounding false claims (for example, a hook, knife, spoon, or different length unless that exact item/length occurs in the source). If you cannot find three source-present alternatives of comparable specificity for a detail, choose another fact that has enough source-present alternatives. Example: for a tool question, all options should be tools that actually occur in these pages, such as needle, pegs, wet sponge, or hand; never add an unmentioned hook. Keep options short and comparable in form, but retain source-specific wording. No answer-length clues or restating the answer in the stem. The correct answer and every distractor must be grounded in the supplied source text. Each answer must be directly supported by one cited sentence retaining every condition. "
        "Use four distinct unlabeled options and one correct answer. Ensure rationale agrees with the selected option. Return exact schema only." )},
      {"role":"user","content":json.dumps({"lesson_id":LESSON,"lesson_title":"Caning: The Seven Steps",
        "source_chunks":[{"chunk_id":chunk.chunk_id,"book_id":chunk.book_id,"role":chunk.role,
                          "pages":chunk.pages,"text":chunk.text} for chunk in chunks],"question_count":10},ensure_ascii=False)}]
    gateway_client.MAX_RETRIES=0;gateway_client.OPENROUTER_RETRYABLE_POOL_MARKERS=()
    gateway=LLMGateway("https://openrouter.ai/api/v1",MODEL,api_key=key,allow_remote=True,
                       timeout_s=120,capture_content=False,reasoning_effort="none")
    print(json.dumps({"event":"generation_started","model":MODEL,"source_sha256":source_hash}),file=sys.stderr,flush=True)
    store=ArtifactStore.from_settings(Settings())
    try:
        result,candidate=gateway.structured_with_result(messages,schema,prompt_version=PROMPT,
                                                         temperature=0.0,max_tokens=4096)
    except InvalidLLMOutput as exc:
        failed_raw=json.dumps({"model_id":MODEL,"attempts":exc.attempts,"errors":exc.errors,
                               "raw_completion":exc.last_output},ensure_ascii=False,sort_keys=True).encode()
        failed_digest=hashlib.sha256((source_hash+exc.last_output+PROMPT+"schema-failure").encode()).hexdigest()
        failed_ref=store.put(COURSE,"phase5-raw-completions",failed_digest,{"raw-completion.json":failed_raw})
        if store.read(failed_ref,"raw-completion.json")!=failed_raw:
            raise RuntimeError("failed raw completion readback mismatch") from exc
        print(json.dumps({"status":"schema_failure_raw_saved","artifact_ref":failed_ref.model_dump(mode="json"),
            "raw_sha256":hashlib.sha256(failed_raw).hexdigest(),"completion_sha256":hashlib.sha256(exc.last_output.encode()).hexdigest(),
            "errors":exc.errors,"attempts":exc.attempts},sort_keys=True),flush=True)
        raise SystemExit("provider completion rejected by strict schema; raw preserved for diagnosis") from exc
    raw_payload=json.dumps({"model_id":MODEL,"usage":result.usage,"usage_totals":gateway.usage_totals,
                            "raw_completion":result.text},ensure_ascii=False,sort_keys=True).encode()
    raw_digest=hashlib.sha256((source_hash+result.text+PROMPT).encode()).hexdigest()
    raw_ref=store.put(COURSE,"phase5-raw-completions",raw_digest,{"raw-completion.json":raw_payload})
    raw_readback=store.read(raw_ref,"raw-completion.json")
    if raw_readback!=raw_payload: raise RuntimeError("raw completion readback mismatch")
    candidate=candidate.model_copy(update={"sentences":[
       sentence.model_copy(update={"kind":"primary"}) for sentence in candidate.sentences]})
    assets=_make_lesson_assets(LESSON,candidate,chunks,prompt_version=PROMPT)
    higher=sum(q.bloom!="remember" for q in assets.questions)
    if higher/len(assets.questions)<0.8: raise SystemExit("automated Bloom gate failed")
    payload=assets.model_dump(mode="json")
    payload["evaluation_metadata"]={"canary":True,"gold_eligible":False,"model_id":MODEL,
      "source":"Seat Weaving (1917), pp.12-13, stored internal_test_only OCR reference",
      "source_sha256":source_hash,"original_source_sha256":original_source_hash,
      "source_normalization":"OCR control characters replaced with spaces",
      "source_chunk_ids":list(source_ids),"provider_usage":gateway.usage_totals,
      "raw_completion_artifact_ref":raw_ref.model_dump(mode="json"),
      "raw_completion_sha256":hashlib.sha256(raw_readback).hexdigest()}
    artifact_bytes=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    digest=hashlib.sha256((source_hash+MODEL+PROMPT+"final-artifact-v4").encode()).hexdigest()
    ref=store.put(COURSE,"phase5-enrichment-canary",digest,{"lesson-enrichment.json":artifact_bytes})
    readback=store.read(ref,"lesson-enrichment.json")
    if readback!=artifact_bytes: raise RuntimeError("lesson artifact readback mismatch")
    print(json.dumps({"status":"artifact_saved","course_id":COURSE,"lesson_id":LESSON,
      "artifact_ref":ref.model_dump(mode="json"),"readback_sha256":hashlib.sha256(readback).hexdigest(),
      "source_sha256":source_hash,"original_source_sha256":original_source_hash,
      "source_normalization":"OCR control characters replaced with spaces",
      "source_chunk_ids":list(source_ids),"question_count":len(assets.questions),"higher_bloom":higher,
      "summary_count":len(assets.summary.points),"glossary_count":len(assets.glossary),
      "provider_usage":gateway.usage_totals,"raw_completion_artifact_ref":raw_ref.model_dump(mode="json"),
      "raw_completion_sha256":hashlib.sha256(raw_readback).hexdigest()},sort_keys=True))
if __name__=="__main__": main()
