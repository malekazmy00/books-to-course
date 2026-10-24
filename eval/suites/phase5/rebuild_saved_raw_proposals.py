import sys
import json
import re
import hashlib
import collections
from pathlib import Path
sys.path.extend(["/app/packages/core/src","/app/packages/contracts/src"])
sys.path.insert(0,"/tmp/phase5-live-src/synthesis")
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore
from b2c_contracts import StructuredChunk
import b2c_pedagogy
b2c_pedagogy.__path__.insert(0,"/tmp/phase5-live-src")
from b2c_pedagogy.lesson_enrichment import (_AssetsCandidate,_MCQProposal,_Sentence,_SummaryPoint,
 _normalize_proven_one_based_references,_source_detail_supported,_detail_tokens,
 _normalized_question_stem,_build_source_grounded_mcqs,_make_lesson_assets,LessonEnrichment)

store=ArtifactStore.from_settings(Settings())
raw_pages=[(12,Path("/tmp/phase5-live-src/seat-weaving-j016.txt").read_text(encoding="utf-8").strip()),
           (13,Path("/tmp/phase5-live-src/seat-weaving-j017.txt").read_text(encoding="utf-8").strip())]
source_hash=hashlib.sha256("\n\n".join(f"{p}\n{t}" for p,t in raw_pages).encode()).hexdigest()
expected_hash="69d16728b42a67cacea11b9ebbbeb8962de28f3088a10a4d1d9d628d9d1e681a"
if source_hash!=expected_hash: raise RuntimeError("fixture source hash changed")
book_id="seat-weaving-1917"; lesson_id="seatweaving.steps2-7"; course_id="phase5-seatweaving-saved-raw-rebuilt-v1"
pages={}; page_texts={}
for page,text in raw_pages:
 chunk_id=f"seat-weaving-1917:j{page}"
 normalized=re.sub(r"\s+"," ",text).strip()
 pages[chunk_id]=normalized; page_texts[page]=normalized
chunks=[StructuredChunk(chunk_id=f"seat-weaving-1917:j{p}",book_id=book_id,role="spine",
 section_path=["Caning; The Seven Steps"],block_ids=[f"seatweaving-j{p}"],pages=[p],
 text=t,token_count=max(1,len(t.split())),embedding_model="evaluation-source-not-embedded")
 for p,t in raw_pages]

# Split only at punctuation, then pack adjacent source passages so refs point to exact page text.
source_sentences=[]
for page,text in raw_pages:
 chunk_id=f"seat-weaving-1917:j{page}"
 normalized=re.sub(r"\s+"," ",text).strip()
 fragments=[x.strip() for x in re.split(r"(?<=[.!?])\s+",normalized) if x.strip()]
 packed=[]; current=""
 for fragment in fragments:
  # Merge OCR fragments/abbreviations and pack into at most 360-character cited spans.
  words=fragment.split()
  subparts=[]; sub=""
  for word in words:
   if len(sub)+len(word)+1>360 and sub:
    subparts.append(sub); sub=word
   else: sub=(sub+" "+word).strip()
  if sub: subparts.append(sub)
  for part in subparts:
   if current and len(current)+len(part)+1>360:
    packed.append(current); current=part
   else: current=(current+" "+part).strip()
 if current: packed.append(current)
 for excerpt in packed:
  if len(excerpt)<12:
   if source_sentences and source_sentences[-1]["citations"]==[chunk_id]:
    source_sentences[-1]["text_en"]=(source_sentences[-1]["text_en"]+" "+excerpt).strip()
   else: continue
  else: source_sentences.append({"text_en":excerpt,"citations":[chunk_id],"kind":"primary"})
if not 3<=len(source_sentences)<=20: raise RuntimeError(f"source excerpt sentence count outside schema: {len(source_sentences)}")
sentences=[_Sentence.model_validate(x) for x in source_sentences]

model_priority={"poolside/laguna-s-2.1:free":0,"poolside/laguna-xs-2.1:free":1,
 "cohere/north-mini-code:free":2,"apodex/apodex-1.1-mini:free":3,
 "nvidia/nemotron-3-super-120b-a12b:free":4,"nvidia/nemotron-3.5-lightning:free":5}
raw_objects=[]
for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=store.bucket):
 for obj in page.get("Contents",[]):
  if "/phase5-raw-completions/" not in obj["Key"] or not obj["Key"].endswith("/raw-completion.json"): continue
  body=store.s3.get_object(Bucket=store.bucket,Key=obj["Key"])["Body"].read()
  envelope=json.loads(body)
  model=envelope.get("model_id",""); prompt=envelope.get("prompt_version","")
  if model not in model_priority or ":generate:" not in str(prompt): continue
  if envelope.get("source_sha256")!=source_hash: continue
  raw_objects.append((model_priority[model],obj["Key"],body,envelope))
raw_objects.sort(key=lambda x:(x[0],x[1]))
proposals=[]; provenance=[]; seen_answers=set(); seen_stems=set(); rejects=collections.Counter()
for priority,key,body,envelope in raw_objects:
 try: candidate=_AssetsCandidate.model_validate_json(envelope["raw_completion"])
 except Exception: rejects["raw_schema_invalid"]+=1; continue
 candidate,normalization=_normalize_proven_one_based_references(candidate)
 for ordinal,q in enumerate(candidate.questions,1):
  refs=q.sentence_indices
  if any(i<0 or i>=len(candidate.sentences) for i in refs):
   rejects["reference_out_of_range"]+=1; continue
  source_sentences_from_raw=[candidate.sentences[i] for i in refs]
  ids={cid for sentence in source_sentences_from_raw for cid in sentence.citations}
  if not ids or not ids.issubset(pages):
   rejects["unknown_source_ids"]+=1; continue
  source_text=" ".join(pages[cid] for cid in sorted(ids))
  answer=q.options[q.answer_index]
  if len(answer)>180:
   rejects["answer_too_long_for_contract"]+=1; continue
  if not _source_detail_supported(answer,source_text):
   rejects["answer_not_supported_by_source_and_citation"]+=1; continue
  if q.bloom not in {"apply","analyze"}:
   rejects["bloom_below_gate"]+=1; continue
  answer_tokens=_detail_tokens(answer); stem_tokens=_detail_tokens(q.stem)
  if answer_tokens and len(answer_tokens&stem_tokens)/len(answer_tokens)>=0.8:
   rejects["stem_reveals_answer"]+=1; continue
  if len(stem_tokens&_detail_tokens(source_text))<2:
   rejects["stem_not_source_tied"]+=1; continue
  fact_key=_normalized_question_stem(answer); stem_key=_normalized_question_stem(q.stem)
  if fact_key in seen_answers or stem_key in seen_stems:
   rejects["duplicate_fact_or_stem"]+=1; continue
  mapped=next((i for i,sentence in enumerate(sentences)
      if sentence.citations[0] in ids and _source_detail_supported(answer,sentence.text_en)),None)
  if mapped is None:
   rejects["answer_not_in_source_excerpt"]+=1; continue
  # Use the exact source excerpt as rationale evidence; no new fact or wording is introduced.
  rationale=sentences[mapped].text_en
  proposal=_MCQProposal(stem=q.stem,bloom=q.bloom,answer_text=answer,
      rationale=rationale,sentence_indices=[mapped])
  proposals.append(proposal); seen_answers.add(fact_key); seen_stems.add(stem_key)
  provenance.append({"model_id":envelope["model_id"],"prompt_version":envelope.get("prompt_version"),
      "raw_object_key":key,"raw_object_sha256":hashlib.sha256(body).hexdigest(),
      "raw_completion_sha256":hashlib.sha256(envelope["raw_completion"].encode()).hexdigest(),
      "question_ordinal":ordinal,"reference_normalization":normalization,
      "answer_sentence_index":mapped,"source_chunk_ids":sorted(ids)})
if len(proposals)<20:
 print(json.dumps({"status":"insufficient_valid_candidates","valid_unique":len(proposals),
   "raw_generation_objects":len(raw_objects),"rejected":dict(rejects),
   "source_excerpt_count":len(sentences)})); raise SystemExit(2)
# Take a balanced subset: preserve source order among candidates but search windows until the product builder passes.
selected=None; selected_questions=None
for start in range(0,len(proposals)-19):
 group=proposals[start:start+20]
 try:
  built=_build_source_grounded_mcqs(group,sentences,20)
  candidate=_AssetsCandidate(sentences=sentences,
      summary_points=[_SummaryPoint(text=sentences[i].text_en,sentence_indices=[i]) for i in range(3)],
      glossary=[],questions=built)
  lesson=_make_lesson_assets(lesson_id,candidate,chunks,prompt_version="saved-raw-rebuild-v1")
  selected=group; selected_questions=built; provenance=provenance[start:start+20]
  break
 except Exception:
  rejects["product_builder_or_asset_validation"]+=1
if selected is None:
 print(json.dumps({"status":"no_passing_20_question_window","valid_unique":len(proposals),
   "raw_generation_objects":len(raw_objects),"rejected":dict(rejects),
   "source_excerpt_count":len(sentences)})); raise SystemExit(3)
# Rebuild provenance indexing if a later window passed.
# The zipped slice above tracks the exact same proposal window.
lesson_bytes=lesson.model_dump_json(indent=2).encode()
validated=LessonEnrichment.model_validate_json(lesson_bytes)
if len(validated.questions)!=20: raise RuntimeError("rebuilt artifact count mismatch")
bloom=collections.Counter(q.bloom for q in validated.questions)
higher=sum(bloom[x] for x in ("apply","analyze"))
if higher<16: raise RuntimeError("rebuilt artifact fails 80-percent Bloom threshold")
for question in validated.questions:
 if not question.option_evidence_sids or len(question.option_evidence_sids)!=len(question.options):
  raise RuntimeError("missing per-option source attribution")
artifact_sha=hashlib.sha256(lesson_bytes).hexdigest()
prov={"status":"candidate_rebuilt_from_saved_raw","gold_eligible":False,"solver_status":"pending",
 "source_sha256":source_hash,"source_pages":[12,13],"source_chunks":[x.chunk_id for x in chunks],
 "raw_generation_objects_scanned":len(raw_objects),"retained_question_provenance":provenance,
 "rejected_counts":dict(rejects),"question_count":20,"bloom_distribution":dict(bloom),
 "higher_bloom_count":higher,"source_excerpts_are_verbatim_page_text_after_whitespace_normalization":True,
 "original_free_model_options_discarded":True,"new_options_built_from_distinct_cited_answer_facts":True,
 "source_grounding_and_contract_checks":"passed by _make_lesson_assets",
 "artifact_sha256":artifact_sha}
prov_bytes=json.dumps(prov,ensure_ascii=False,sort_keys=True,indent=2).encode()
key=hashlib.sha256(lesson_bytes+prov_bytes).hexdigest()
ref=store.put(course_id,"phase5-enrichment-canary",key,{"lesson-enrichment.json":lesson_bytes,"rebuild-provenance.json":prov_bytes})
readback=store.read(ref,"lesson-enrichment.json"); prov_readback=store.read(ref,"rebuild-provenance.json")
if readback!=lesson_bytes or prov_readback!=prov_bytes: raise RuntimeError("MinIO readback mismatch")
LessonEnrichment.model_validate_json(readback)
print(json.dumps({"status":"candidate_rebuilt_readback_pass_solver_pending","artifact_ref":ref.model_dump(mode="json"),
 "lesson_sha256":hashlib.sha256(readback).hexdigest(),"provenance_sha256":hashlib.sha256(prov_readback).hexdigest(),
 "source_excerpt_count":len(sentences),"raw_generation_objects_scanned":len(raw_objects),
 "candidate_unique_before_window":len(proposals),"question_count":len(validated.questions),
 "bloom_distribution":dict(bloom),"rejected":dict(rejects),
 "retained_models":dict(collections.Counter(x["model_id"] for x in provenance)),
 "provider_usage_from_retained_raws":[x.get("usage") for x in []]},sort_keys=True))
