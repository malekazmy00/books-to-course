#!/usr/bin/env python3
"""Verify an uploaded PDF and fill missing root identity; dry-run by default."""
import argparse
import hashlib
import json
from b2c_core import ArtifactStore, Settings
from b2c_core.legacy_source_backfill import _verified_artifact

p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--course-id",required=True);p.add_argument("--book-id",required=True);p.add_argument("--apply",action="store_true")
a=p.parse_args();s=ArtifactStore.from_settings(Settings())
key=f"{a.course_id}/book-progress/{hashlib.sha256(a.book_id.encode()).hexdigest()}.json"
obj=s.s3.get_object(Bucket=s.bucket,Key=key);raw=obj["Body"].read();record=json.loads(raw)
if record.get("book_id")!=a.book_id or record.get("course_id")!=a.course_id:raise ValueError("registry identity mismatch")
refs={}
for page in s.s3.get_paginator("list_objects_v2").paginate(Bucket=s.bucket,Prefix=a.course_id+"/book-upload/",Delimiter="/"):
    for entry in page.get("CommonPrefixes",[]):
        artifact_key=entry["Prefix"].strip("/").split("/")[-1]
        ref=s.exists(a.course_id,"book-upload",artifact_key)
        if not ref:continue
        meta=json.loads(s.read(ref,"book-upload.json"))
        digest=meta.get("file_sha256","")
        if a.book_id!="book-"+digest[:24]:continue
        checked,hashes=_verified_artifact(s,a.course_id,"book-upload",ref.key,ref.sha256)
        if hashes.get("source.pdf")!=digest:raise ValueError("PDF bytes do not match upload fingerprint")
        refs[digest]=(ref,meta)
if len(refs)!=1:raise ValueError("expected one unambiguous verified source fingerprint")
digest,(ref,meta)=next(iter(refs.items()))
if record.get("source_file_sha256") not in (None,digest):raise ValueError("conflicting registry PDF fingerprint")
for name,stage in record.get("stages",{}).items():
    if stage.get("source_file_sha256") not in (None,digest):raise ValueError("conflicting stage PDF fingerprint: "+name)
if record.get("source_upload_ref") not in (None,ref.model_dump(mode="json")):raise ValueError("conflicting root upload reference")
updated={**record,"source_file_sha256":digest,"source_upload_ref":ref.model_dump(mode="json"),
    "artifact_book_id":meta["book_id"]}
assert updated["stages"]==record["stages"] and updated.get("updated_at")==record.get("updated_at")
if a.apply and updated!=record:
    s.s3.put_object(Bucket=s.bucket,Key=key,Body=json.dumps(updated,sort_keys=True,ensure_ascii=False).encode(),
        ContentType="application/json",IfMatch=obj["ETag"])
    saved=json.loads(s.s3.get_object(Bucket=s.bucket,Key=key)["Body"].read())
    assert saved==updated
print(json.dumps({"mode":"apply" if a.apply else "dry-run","book_id":a.book_id,"source_file_sha256":digest,
    "source_upload_key":ref.key,"changed":updated!=record,"stages_unchanged":True,"updated_at_unchanged":True}))
