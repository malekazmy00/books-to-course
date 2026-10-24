"""Recover committed outputs associated with a workflow after history retention."""
from __future__ import annotations
import hashlib
import json
from asyncio import to_thread
from b2c_indexing import chunk_book
from b2c_contracts import ArtifactRef, ParsedBook, CourseOutline, StructuredChunk, stage_key

async def associated_record(store, workflow_id: str, stage: str, course_id: str | None = None):
    paginator = store.s3.get_paginator("list_objects_v2")
    prefix = f"{course_id}/book-progress/" if course_id else ""
    pages = await to_thread(lambda: list(paginator.paginate(Bucket=store.bucket, Prefix=prefix)))
    for page in pages:
        for item in page.get("Contents", []):
            key = item["Key"]
            if "/book-progress/" not in key or not key.endswith(".json"):
                continue
            record = await to_thread(lambda: json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read()))
            if record.get("stages", {}).get(stage, {}).get("workflow_id") == workflow_id:
                if course_id and record.get("course_id") != course_id:
                    continue
                return record
    return None

async def checked_ref(store, cid, raw_ref, stage):
    ref = ArtifactRef.model_validate(raw_ref)
    if ref.stage != stage or ref.uri != f"s3://{store.bucket}/{cid}/{stage}/{ref.key}/":
        raise ValueError("stored artifact has invalid provenance")
    committed = await to_thread(store.exists, cid, stage, ref.key)
    if committed is None or committed.sha256 != ref.sha256 or committed.uri != ref.uri:
        raise ValueError("stored artifact is not committed")
    return ref

async def approved_envelope(store, record):
    raw = record.get("stages", {}).get("outline", {}).get("approved_outline_ref")
    if not raw:
        return None
    ref = await checked_ref(store, record["course_id"], raw, "approve-outline")
    envelope = json.loads(await to_thread(store.read, ref, "approved-outline.json"))
    outline = CourseOutline.model_validate(envelope["outline"])
    if outline.course_id != record["course_id"] or outline.spine_book_id != record.get("artifact_book_id", record["book_id"]):
        raise ValueError("approved outline identity mismatch")
    return ref, envelope, outline

async def recover_extraction(store, client, workflow_id: str, *, course_id: str | None = None):
    """Only recover a reference explicitly associated with this exact ingestion."""
    record = await associated_record(store, workflow_id, "ingest", course_id)
    if record is None:
        return None
    cid, bid = record["course_id"], record.get("artifact_book_id", record["book_id"])
    raw_ref = record["stages"]["ingest"].get("parsed_book_ref")
    accepted = await approved_envelope(store, record)
    if not raw_ref and accepted:
        raw_ref = accepted[1].get("spine_parsed_ref")
    if not raw_ref:
        index_id = record["stages"].get("index", {}).get("workflow_id")
        if not index_id:
            return None
        history = await client.get_workflow_handle(index_id).fetch_history()
        for event in history.events:
            if event.HasField("workflow_execution_started_event_attributes"):
                values = await client.data_converter.decode(event.workflow_execution_started_event_attributes.input.payloads)
                inp = values[0] if values else {}
                if inp.get("course_id") != cid:
                    return None
                raw_ref = inp.get("parsed_ref")
                break
    if not raw_ref:
        return None
    ref = await checked_ref(store, cid, raw_ref, "ingest-parse-pdf")
    book = ParsedBook.model_validate_json(await to_thread(store.read, ref, "parsed-book.json"))
    if book.book_id != bid:
        raise ValueError("recovered extraction book identity mismatch")
    draft = None
    if accepted and accepted[1].get("draft_ref"):
        draft_ref = await checked_ref(store, cid, accepted[1]["draft_ref"], "build-outline")
        draft = {"ref": draft_ref.model_dump(mode="json"), "cache_hit": True}
    return {"parsed_book": {"ref": ref.model_dump(mode="json"), "cache_hit": True,
            "page_count": book.page_count, "block_count": len(book.blocks)}, "outline": draft}

async def recover_index(store, chunk_store, workflow_id):
    record = await associated_record(store, workflow_id, "index")
    if record is None:
        return None
    accepted = await approved_envelope(store, record)
    if not accepted:
        return None
    approved, envelope, outline = accepted
    params = record["stages"]["index"].get("index_parameters", {"target_tokens": 500, "overlap_ratio": 0.12, "embedding_model": "unassigned"})
    identity = {"course_id": record["course_id"], "spine_book_id": record.get("artifact_book_id", record["book_id"]),
                "outline": envelope["outline"], "target_tokens": params["target_tokens"], "overlap_ratio": params["overlap_ratio"]}
    expected_id = "b2c-index-" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if expected_id != workflow_id:
        return None
    sources = {outline.spine_book_id: envelope["spine_parsed_ref"], **envelope.get("supplementary_parsed_refs", {})}
    books, pages = {}, {}
    for bid, raw in sources.items():
        role = "spine" if bid == outline.spine_book_id else "supplementary"
        source = await checked_ref(store, record["course_id"], raw, "ingest-parse-pdf")
        snapshot_key = stage_key("index-approved-book", "snapshot-1", [source.sha256, approved.sha256],
            {"book_id": bid, "role": role, **params})
        snapshot = await to_thread(store.exists, record["course_id"], "index-approved-book", snapshot_key)
        if snapshot is None:
            # Legacy workers predate immutable index snapshots. Verify the
            # database against a deterministic reconstruction, without writes.
            parsed = ParsedBook.model_validate_json(await to_thread(store.read, source, "parsed-book.json"))
            if parsed.book_id != bid or parsed.role != role:
                raise ValueError("legacy index source identity mismatch")
            chunks = await to_thread(chunk_book, parsed, target_tokens=params["target_tokens"],
                overlap_ratio=params["overlap_ratio"], embedding_model=params["embedding_model"],
                outline=outline if role == "spine" else None)
        else:
            chunks = [StructuredChunk.model_validate(x) for x in json.loads(await to_thread(store.read, snapshot, "chunks.json"))]
        persisted = await to_thread(chunk_store.list_chunks, record["course_id"], book_ids={bid})
        if any(c.book_id != bid or c.role != role for c in chunks):
            raise ValueError("index snapshot identity mismatch")
        if {c.chunk_id: c.model_dump(mode="json") for c in persisted} != {c.chunk_id: c.model_dump(mode="json") for c in chunks}:
            return None
        books[bid] = {"role": role, "chunk_count": len(chunks), "chunk_ids": [c.chunk_id for c in chunks], "artifact_ref": snapshot.model_dump(mode="json") if snapshot else None, "cache_hit": True}
        pages[bid] = sorted({p for c in chunks for p in c.pages})
    return {"course_id": record["course_id"], "outline_sha256": approved.sha256,
            "book_count": len(books), "chunk_count": sum(b["chunk_count"] for b in books.values()), "books": books, "pages_by_book": pages}
