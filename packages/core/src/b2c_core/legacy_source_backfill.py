"""Explicit, verified legacy source backfill; preparation never mutates storage."""
from __future__ import annotations
import hashlib
import json
from typing import Any
from b2c_contracts import ArtifactRef, ParsedBook, CourseOutline, stage_key
from .artifacts import _content_sha256_from_hashes

OLD_BOOK_MAPPING = {
    "course_id": "b2c-demo", "artifact_book_id": "book-main",
    "file_sha256": "aaea5c4b55e6a7519bdea580fcab7075b0778da690bbe3534e97279a5c447e01",
    "upload_key": "cde04e1eb268f8bf1abb558729bd7959ef5f1403047e5d57856995a02dc510f7",
    "upload_sha256": "979d5c3919aad160744061adc51f96d19904c90ed71a829ef508f962b6e63432",
    "parsed_key": "e40a641ee1016034e6f24b1c191c32725472dc3edad6ae6ad0aecf9db0d67dcf",
    "parsed_sha256": "719c00096d1658f53980a48b4410a8cec30009e47908c0daeb9a3918352253d3",
    "approved_key": "8a4a762123950944953d19517db21d1ba8247f6181a46aebc3390d48fe0b0325",
    "approved_sha256": "03780a086d2f8462a0a2d1c94455179fabe717e5007c4d14f808dfe06670ba01",
    "ingest_workflow_id": "b2c-ingest-7fa128cc314aa3c679bdd75e93d6b746eb0663a0db713997356755af23f4ade8",
    "page_count": 677, "block_count": 776,
    "evidence": "eval/reports/ocr-status-view-recovery-2026-10-08.md",
    "index_workflow_id": "b2c-index-d1e3fac85d5d4b3058a8008fc3afa35c9359dd7dba7205020b02be6620501873",
    "qa_workflow_id": "b2c-qa-9146da0974d35c6cd3199c5a652f1ca147d14f0f7b4635a84b7a4bc2fdcac6ef",
    "qa_approved_key": "8f187c7834c44e0a91ad40197e6d26ddb50a547a352563531bbb64a385eb1c57",
    "qa_approved_sha256": "7834da2269d578b015eac4d06e310d7729f4f1dfad9740b2c2b63107e15cd0f0",
    "qa_key": "38d9a5700e5729d713376221a88c16bf45d141e34a0805b838301bb1849298cf",
    "qa_sha256": "f626ae40478741f2edcbab1c7b03781ad7da6842ad02dac183622f447bcaa81a",
    "translation_workflow_id": "b2c-translate-fbd9747581997b28280a99c104138c8183f0834ddc5841c0f2f94f0c828ba9c5",
    "translation_key": "d6bff832b3edca958c9f7b5e048e58ef94c957148a594af70ab8aeee1a664973",
    "translation_sha256": "4add240b676535a19d704a4992cc75a8e53c643ed80e3e6336bb284bb111d121",
}


def _verified_artifact(store, cid, stage, key, digest):
    ref = store.exists(cid, stage, key)
    if not ref or ref.sha256 != digest or ref.uri != f"s3://{store.bucket}/{cid}/{stage}/{key}/":
        raise ValueError(f"explicit {stage} manifest mismatch")
    hashes = {}
    for name in store.list_files(ref):
        obj = store.s3.get_object(Bucket=store.bucket, Key=store._prefix_of(ref)+name)
        h = hashlib.sha256()
        for chunk in iter(lambda: obj["Body"].read(1024*1024), b""):
            h.update(chunk)
        hashes[name] = h.hexdigest()
    if _content_sha256_from_hashes(hashes) != digest:
        raise ValueError(f"explicit {stage} content corruption")
    return ref, hashes


def prepare_backfill(store: Any, mapping: dict[str, Any]) -> dict[str, Any]:
    """Only process caller-pinned refs; no most-recent-artifact discovery."""
    cid, internal, digest = mapping["course_id"], mapping["artifact_book_id"], mapping["file_sha256"]
    alias = "book-"+digest[:24]
    upload, upload_hashes = _verified_artifact(store,cid,"book-upload",mapping["upload_key"],mapping["upload_sha256"])
    metadata = json.loads(store.read(upload,"book-upload.json"))
    if metadata.get("book_id") != internal or metadata.get("file_sha256") != digest or upload_hashes.get("source.pdf") != digest:
        raise ValueError("explicit PDF upload identity mismatch")
    parsed_ref, _ = _verified_artifact(store,cid,"ingest-parse-pdf",mapping["parsed_key"],mapping["parsed_sha256"])
    parsed = ParsedBook.model_validate_json(store.read(parsed_ref,"parsed-book.json"))
    if (parsed.book_id != internal or parsed.page_count != mapping["page_count"] or
            len(parsed.blocks) != mapping["block_count"]):
        raise ValueError("explicit parsed-book identity or dimensions mismatch")
    approved_ref, _ = _verified_artifact(store,cid,"approve-outline",mapping["approved_key"],mapping["approved_sha256"])
    approval = json.loads(store.read(approved_ref,"approved-outline.json"))
    outline = CourseOutline.model_validate(approval["outline"])
    if (outline.course_id != cid or outline.spine_book_id != internal or
            ArtifactRef.model_validate(approval["spine_parsed_ref"]) != parsed_ref):
        raise ValueError("explicit outline source mismatch")
    migration_id = hashlib.sha256(json.dumps(mapping,sort_keys=True).encode()).hexdigest()
    identity = {"external_book_id":alias,"artifact_book_id":internal,
        "source_file_sha256":digest,"source_parsed_sha256":parsed_ref.sha256,
        "source_upload_ref":upload.model_dump(mode="json"),
        "source_parsed_ref":parsed_ref.model_dump(mode="json"),
        "approved_outline_ref":approved_ref.model_dump(mode="json"),
        "migration_id":migration_id,"evidence":mapping["evidence"]}
    stages = {"ingest":{"workflow_id":mapping["ingest_workflow_id"],
        "status_url":f"/v1/ingestions/{mapping['ingest_workflow_id']}",
        "parsed_book_ref":parsed_ref.model_dump(mode="json"),
        "parsed_artifact_ref":parsed_ref.model_dump(mode="json"),
        "source_file_sha256":digest,"source_parsed_sha256":parsed_ref.sha256,
        "artifact_book_id":internal,"status":"COMPLETED"},
        "outline":{"workflow_id":mapping["ingest_workflow_id"],"status":"COMPLETED",
        "approved_outline_ref":approved_ref.model_dump(mode="json"),
        "source_parsed_sha256":parsed_ref.sha256,"source_file_sha256":digest}}
    if mapping.get("index_workflow_id"):
        params = {"target_tokens": 500, "overlap_ratio": 0.12, "embedding_model": "unassigned"}
        index_identity = {"course_id":cid,"spine_book_id":internal,"outline":approval["outline"],
            "target_tokens":params["target_tokens"],"overlap_ratio":params["overlap_ratio"]}
        expected_id = "b2c-index-"+hashlib.sha256(json.dumps(index_identity,sort_keys=True).encode()).hexdigest()
        if expected_id != mapping["index_workflow_id"]:
            raise ValueError("explicit index workflow identity mismatch")
        stages["index"] = {"workflow_id":expected_id,"status_url":f"/v1/indexings/{expected_id}",
            "approved_outline_ref":approved_ref.model_dump(mode="json"),"outline_sha256":approved_ref.sha256,
            "source_parsed_sha256":parsed_ref.sha256,"source_file_sha256":digest,
            "artifact_book_id":internal,"index_parameters":params}
    if mapping.get("qa_key"):
        qa_approval_ref,_ = _verified_artifact(store,cid,"approve-outline",mapping["qa_approved_key"],mapping["qa_approved_sha256"])
        qa_approval=json.loads(store.read(qa_approval_ref,"approved-outline.json"))
        if ArtifactRef.model_validate(qa_approval["spine_parsed_ref"]) != parsed_ref:
            raise ValueError("explicit QA outline source mismatch")
        qa_ref,_ = _verified_artifact(store,cid,"generate-course-qa",mapping["qa_key"],mapping["qa_sha256"])
        qa=json.loads(store.read(qa_ref,"course-qa.json"))
        stages["qa"]={"workflow_id":mapping["qa_workflow_id"],"status_url":f"/v1/course-qa/{mapping['qa_workflow_id']}",
            "approved_outline_ref":qa_approval_ref.model_dump(mode="json"),"outline_sha256":qa_approval_ref.sha256,
            "source_parsed_sha256":parsed_ref.sha256,"source_file_sha256":digest,"artifact_book_id":internal,
            "artifact_ref":qa_ref.model_dump(mode="json"),"result_status":qa.get("status")}
        translated_ref,_ = _verified_artifact(store,cid,"translate-content",mapping["translation_key"],mapping["translation_sha256"])
        translated_source=json.loads(store.read(translated_ref,"translation-source.json"))
        if ArtifactRef.model_validate(translated_source["source_artifact_ref"]) != qa_ref:
            raise ValueError("explicit translation QA source mismatch")
        wid=mapping["translation_workflow_id"]
        stages["translation"]={"workflow_id":wid,"status_url":f"/v1/translations/{wid}",
            "source_parsed_sha256":parsed_ref.sha256,"source_file_sha256":digest,"artifact_book_id":internal,
            "source_artifact_sha256":qa_ref.sha256,"source_kind":"course","target_language":"ar",
            "artifact_ref":translated_ref.model_dump(mode="json")}
    legacy_key = f"{cid}/book-progress/{hashlib.sha256(internal.encode()).hexdigest()}.json"
    try:
        legacy = json.loads(store.s3.get_object(Bucket=store.bucket,Key=legacy_key)["Body"].read())
    except Exception as exc:
        code = getattr(exc,"response",{}).get("ResponseMetadata",{}).get("HTTPStatusCode")
        if code != 404 and type(exc).__name__ != "NoSuchKey": raise
        legacy = {}
    candidates = list(legacy.get("stages",{}).items())
    for history in legacy.get("stage_history",[]):
        if history.get("stage"): candidates.append((history["stage"],history))
        candidates.extend(history.get("stages",{}).items())
    for name, stage in candidates:
        if name in {"ingest", "outline"} or stage.get("source_parsed_sha256") != parsed_ref.sha256:
            continue
        if name in {"index", "qa", "translation"} and stage.get("outline_sha256") != approved_ref.sha256:
            continue
        if stage.get("source_file_sha256") not in (None,digest): continue
        existing = stages.get(name)
        if not existing or str(stage.get("requested_at",stage.get("recorded_at",""))) >= str(existing.get("requested_at","")):
            stages[name] = {**stage,"source_file_sha256":digest,"artifact_book_id":internal}
    progress_key = f"{cid}/book-progress/{hashlib.sha256(alias.encode()).hexdigest()}.json"
    progress = {"schema_version":"1.0","course_id":cid,"book_id":alias,
        "artifact_book_id":internal,"source_file_sha256":digest,
        "source_upload_ref":upload.model_dump(mode="json"),"source_identity":identity,
        "migration_id":migration_id,"stages":stages}
    verified_title = None
    if digest == OLD_BOOK_MAPPING["file_sha256"]:
        leading = " ".join(" ".join(b.text.split()) for b in parsed.blocks[:5]).upper()
        if "STRUCTURED ANALYTIC TECHNIQUES" not in leading or "FOR INTELLIGENCE ANALYSIS" not in leading or "THIRD EDITION" not in leading:
            raise ValueError("explicit source cover title mismatch")
        verified_title = "Structured Analytic Techniques for Intelligence Analysis, 3rd ed."
    library = {**metadata,"course_id":cid,"book_id":alias,"artifact_book_id":internal,
        "workflow_id":mapping["ingest_workflow_id"],"file_sha256":digest,
        "source_artifact_ref":upload.model_dump(mode="json"),
        "parsed_artifact_ref":parsed_ref.model_dump(mode="json"),"migration_id":migration_id}
    if verified_title:
        library.update(title=verified_title, title_verified=True, title_provenance="source-cover-blocks-1:0-1:2")
    return {"identity_key":f"_system/source-identities/{cid}/{digest}.json",
        "parsed_identity_key":f"_system/source-identities/{cid}/by-parsed/{parsed_ref.sha256}.json",
        "identity":identity,"progress_key":progress_key,"progress":progress,
        "library":library,"library_key":stage_key("book-library","legacy-backfill-title-2",[digest],{"migration_id":migration_id,"title":library.get("title")})}


def apply_backfill(store: Any, prepared: dict[str, Any]) -> dict[str, Any]:
    """Idempotently add the explicit alias; preserve any later alias stage progress."""
    pending = []
    for key, payload in [(prepared["identity_key"],prepared["identity"]),
                         (prepared["parsed_identity_key"],prepared["identity"]),
                         (prepared["progress_key"],prepared["progress"])]:
        try:
            current=json.loads(store.s3.get_object(Bucket=store.bucket,Key=key)["Body"].read())
        except Exception as exc:
            code=getattr(exc,"response",{}).get("ResponseMetadata",{}).get("HTTPStatusCode")
            if code != 404 and type(exc).__name__ != "NoSuchKey": raise
            current=None
        if current:
            if current.get("migration_id") != payload["migration_id"]:
                raise ValueError("refusing to overwrite an existing different source mapping")
            continue
        pending.append((key,payload))
    # Check all destinations before writing any record.
    for key,payload in pending:
        store.s3.put_object(Bucket=store.bucket,Key=key,
            Body=json.dumps(payload,sort_keys=True,ensure_ascii=False).encode(),ContentType="application/json")
    cid=prepared["progress"]["course_id"]
    library_ref=store.put(cid,"book-library",prepared["library_key"],
        {"book-library.json":json.dumps(prepared["library"],sort_keys=True,ensure_ascii=False).encode()})
    return {"external_book_id":prepared["progress"]["book_id"],"library_ref":library_ref.model_dump(mode="json")}
