#!/usr/bin/env python3
"""Read-only audit of the first lesson/body and QA tracks; never sends inference."""
import argparse,json,time,hashlib,textwrap
from pathlib import Path
from b2c_core import ArtifactStore,Settings
from b2c_contracts import ArtifactRef
from b2c_core.source_identity import artifact_source_identity
parser=argparse.ArgumentParser();parser.add_argument("--workflow-id",required=True);parser.add_argument("--request",required=True);parser.add_argument("--output",required=True);args=parser.parse_args()
s=ArtifactStore.from_settings(Settings());req=json.loads(Path(args.request).read_text());output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
def asset(field,name):return json.loads(s.read(ArtifactRef.model_validate(req[field]),name))
outline=asset("approved_outline_ref","approved-outline.json");parsed=asset("source_book_ref","parsed-book.json");qa=asset("source_qa_artifact_ref","course-qa.json");body=asset("source_book_translation_ref","translation.json");attachment=asset("source_artifact_ref","translation.json")
expected_identity=artifact_source_identity(s,ArtifactRef.model_validate(req["source_book_ref"]));assert expected_identity["source_fingerprint_verified"]
sid=req["lesson_ids"][0];lesson=next(l for m in outline["outline"]["modules"] for l in m["lessons"] if l["lesson_id"]==sid);positions={b["block_id"]:i for i,b in enumerate(parsed["blocks"])};span=lesson["spine_span"];translated={x["segment_id"]:x["text"] for x in body["segments"]};blocks=parsed["blocks"][positions[span["start_block"]]:positions[span["end_block"]]+1];expected_body="\n".join(translated[b["block_id"]] for b in blocks if b["text"].strip())
scope=next(x for x in qa["scopes"] if x["scope_id"]==sid);assert scope["status"]=="final";tr={x["segment_id"]:x["text"] for x in attachment["segments"]};lines=[]
for i,q in enumerate(scope["items"],1):lines.extend(["سؤال: "+tr[f"{sid}:lesson-qa:{i}:question"],"الإجابة: "+tr[f"{sid}:lesson-qa:{i}:answer"]])
expected={sid+"-source-text":expected_body,sid+"-attachment":"\n".join(lines)};completed={};last=None
for attempt in range(4320):
    try:manifest=json.loads(s.s3.get_object(Bucket=s.bucket,Key=f"_system/audio-progress/{args.workflow_id}.json")["Body"].read())
    except s.s3.exceptions.NoSuchKey:time.sleep(20);continue
    tracks=manifest.get("tracks",[])
    for track in tracks:
        tid=track["track_id"]
        if tid not in expected or tid in completed:continue
        reference=ArtifactRef.model_validate(track["artifact_ref"]);text=s.read(reference,"track.txt").decode();audio=s.read(reference,"track.mp3");identity=json.loads(s.read(reference,"source-identity.json"))
        assert text==expected[tid],f"transcript mismatch {tid}"
        assert identity["source_parsed_sha256"]==req["source_book_ref"]["sha256"]
        assert identity["source_file_sha256"]==expected_identity["source_file_sha256"]
        assert track["language"]=="ar" and len(audio)>256 and (audio[:3]==b"ID3" or audio[0]==255)
        (output/(tid+".mp3")).write_bytes(audio);(output/(tid+".txt")).write_text(text)
        completed[tid]={"sha256":hashlib.sha256(audio).hexdigest(),"bytes":len(audio),"text_chars":len(text),"exact_translated_text_match":True,"source_identity":identity,"cache_hit":track.get("cache_hit")}
    state={"workflow_id":args.workflow_id,"tracks_completed":len(tracks),"planned":manifest["track_count"],"audited_tracks":completed,"first_pair_complete":len(completed)==2,"attachment_coverage":manifest.get("attachment_coverage")}
    (output/"audit.json").write_text(json.dumps(state,ensure_ascii=False,indent=2))
    short={"tracks_completed":len(tracks),"audited_tracks":list(completed)}
    if short!=last:print(json.dumps(short),flush=True);last=short
    if len(completed)==2:break
    time.sleep(20)
else:raise TimeoutError("first audio pair did not finish within24hours")
