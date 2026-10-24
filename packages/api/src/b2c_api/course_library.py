"""Durable course names and source-book memberships, independent of processing."""
from __future__ import annotations
import json
import uuid
import threading
import hashlib
from typing import Any, Callable, Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

_metadata_lock = threading.RLock()

class CourseName(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class BookDisplayName(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class Membership(BaseModel):
    source_course_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    book_id: str = Field(min_length=1, max_length=160)
    role: Literal["spine", "supplementary"] = "spine"

def book_name_key(course_id: str, book: dict) -> str:
    digest = str(book.get("file_sha256", ""))
    identity = digest if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) else hashlib.sha256(
        (course_id + "\\0" + book["book_id"]).encode()).hexdigest()
    return "_system/book-names/" + identity + ".json"

def apply_book_names(store, bucket: str, course_id: str, books: list[dict]) -> list[dict]:
    """Display metadata only: leave source hashes, artifact refs and workflow IDs intact."""
    for book in books:
        local_name = str(book.get("display_name", "")).strip() if book.get("source_course_id") else ""
        if local_name:
            book["title"] = local_name
            continue
        try:
            record = json.loads(store.s3.get_object(Bucket=bucket, Key=book_name_key(course_id, book))["Body"].read())
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"} or isinstance(exc, KeyError):
                continue
            raise
        name = str(record.get("name", "")).strip()
        if name:
            book["original_title"] = book.get("original_title", book.get("title", ""))
            book["title"] = name
            book["display_name"] = name
    return books

def course_library_router(store_provider: Callable, bucket: str, list_books: Callable) -> APIRouter:
    router = APIRouter()

    def valid(cid: str) -> str:
        if not cid or len(cid)>80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in cid):
            raise HTTPException(422, "invalid course_id")
        return cid

    def course_ids() -> list[str]:
        store = store_provider()
        found = set()
        args = {"Bucket": bucket, "Delimiter": "/"}
        while True:
            page = store.s3.list_objects_v2(**args)
            found.update(e["Prefix"].rstrip("/") for e in page.get("CommonPrefixes", []))
            token=page.get("NextContinuationToken")
            if not token: break
            args["ContinuationToken"]=token
        return sorted(cid for cid in found if not cid.startswith("_"))

    def read_metadata(cid: str) -> dict:
        store=store_provider()
        key=cid+"/course-settings.json"
        try:
            return json.loads(store.s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except Exception as exc:
            code=getattr(exc,"response",{}).get("Error",{}).get("Code")
            if code in {"NoSuchKey","404","NotFound"} or isinstance(exc, KeyError):
                return {"course_id":cid,"name":cid,"books":[]}
            raise

    def save_metadata(cid: str, data: dict) -> None:
        store_provider().s3.put_object(Bucket=bucket, Key=cid+"/course-settings.json",
            Body=json.dumps(data,ensure_ascii=False).encode(),ContentType="application/json")

    @router.get("/v1/library/books")
    async def global_books(q: str = Query("",max_length=200)) -> dict:
        by_id={}
        for cid in await run_in_threadpool(course_ids):
            result=await list_books(cid)
            for item in result["books"]:
                identity=item.get("file_sha256") or cid+":"+item["book_id"]
                if identity not in by_id:
                    by_id[identity]={**item,"source_course_id":cid,"available_in_courses":[cid]}
                elif cid not in by_id[identity]["available_in_courses"]:
                    by_id[identity]["available_in_courses"].append(cid)
        query=q.strip().casefold()
        items=[x for x in by_id.values() if not query or query in
            " ".join(str(x.get(k,"")) for k in ("title","display_name","source_filename","book_id")).casefold()]
        return {"books":items,"total":len(items)}

    @router.get("/v1/courses")
    async def courses() -> dict:
        result=[]
        for cid in await run_in_threadpool(course_ids):
            data=await run_in_threadpool(read_metadata,cid)
            native=(await list_books(cid))["books"]
            known={m["book_id"] for m in data.get("books",[])}
            data["books"]=data.get("books",[])+[{"book_id":b["book_id"],"source_course_id":cid,
                "title":b.get("display_name") or b.get("title") or b["book_id"],
                "role":b.get("role","spine"),"file_sha256":b.get("file_sha256","")}
                for b in native if b["book_id"] not in known]
            data["books"] = await run_in_threadpool(apply_book_names, store_provider(), bucket, cid, data["books"])
            result.append(data)
        return {"courses":result}

    @router.post("/v1/courses",status_code=201)
    async def create_course(body: CourseName) -> dict:
        cid="course-"+uuid.uuid4().hex[:24]
        record={"course_id":cid,"name":body.name.strip(),"books":[]}
        if not record["name"]: raise HTTPException(422,"course name cannot be blank")
        await run_in_threadpool(save_metadata,cid,record)
        return record

    @router.put("/v1/courses/{course_id}")
    @router.patch("/v1/courses/{course_id}")
    async def rename_course(course_id: str, body: CourseName) -> dict:
        cid=valid(course_id)
        name=body.name.strip()
        if not name: raise HTTPException(422,"course name cannot be blank")
        def rename():
            with _metadata_lock:
                record=read_metadata(cid)
                record["name"]=name
                save_metadata(cid,record)
                return record
        return await run_in_threadpool(rename)

    @router.put("/v1/courses/{course_id}/books/{book_id}/name")
    async def rename_book(course_id: str, book_id: str, body: CourseName) -> dict:
        cid = valid(course_id)
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "book name cannot be blank")
        source = await list_books(cid)
        book = next((b for b in source["books"] if b["book_id"] == book_id), None)
        if book is None:
            raise HTTPException(404, "book not found")
        record = {"book_id": book_id, "file_sha256": book.get("file_sha256", ""), "name": name}
        await run_in_threadpool(store_provider().s3.put_object, Bucket=bucket,
            Key=book_name_key(cid, book), Body=json.dumps(record, ensure_ascii=False).encode(),
            ContentType="application/json")
        return {"course_id": cid, **record}

    @router.put("/v1/courses/{course_id}/memberships/{book_id}")
    async def attach_book(course_id: str, book_id: str, body: Membership) -> dict:
        cid=valid(course_id)
        if body.book_id!=book_id: raise HTTPException(422,"book identity mismatch")
        source=await list_books(body.source_course_id)
        book=next((b for b in source["books"] if b["book_id"]==book_id),None)
        if book is None: raise HTTPException(404,"source book not found")
        native=(await list_books(cid))["books"]
        membership={"book_id":book_id,"source_course_id":body.source_course_id,
            "role":body.role,"title":book.get("display_name") or book.get("title") or book_id,
            "file_sha256":book.get("file_sha256","")}
        def attach():
            with _metadata_lock:
                record=read_metadata(cid)
                members=[m for m in record.get("books",[]) if m["book_id"]!=book_id]
                known={m["book_id"] for m in members}|{book_id}
                members += [{"book_id":b["book_id"],"source_course_id":cid,
                    "role":b.get("role","spine"),"title":b.get("display_name") or b.get("title") or b["book_id"],
                    "file_sha256":b.get("file_sha256","")} for b in native if b["book_id"] not in known]
                if body.role=="spine":
                    for member in members:
                        if member.get("role")=="spine": member["role"]="supplementary"
                record["books"]=members+[membership]
                save_metadata(cid,record)
                return record
        return await run_in_threadpool(attach)

    @router.get("/v1/courses/{course_id}/memberships")
    async def memberships(course_id: str) -> dict:
        cid=valid(course_id)
        data=await run_in_threadpool(read_metadata,cid)
        known={m["book_id"] for m in data.get("books",[])}
        native=(await list_books(cid))["books"]
        data["books"]=data.get("books",[])+[{"book_id":b["book_id"],"source_course_id":cid,
            "title":b.get("display_name") or b.get("title") or b["book_id"],
            "role":b.get("role","spine"),"file_sha256":b.get("file_sha256","")}
            for b in native if b["book_id"] not in known]
        data["books"] = await run_in_threadpool(apply_book_names, store_provider(), bucket, cid, data["books"])
        return data

    @router.patch("/v1/courses/{course_id}/memberships/{book_id}")
    async def rename_membership(course_id: str, book_id: str, body: BookDisplayName) -> dict:
        cid = valid(course_id)
        name = body.display_name.strip()
        if not name:
            raise HTTPException(422, "book display name cannot be blank")
        native = (await list_books(cid))["books"]
        def rename():
            with _metadata_lock:
                record = read_metadata(cid)
                member = next((m for m in record.get("books", []) if m["book_id"] == book_id), None)
                if member is None:
                    source = next((b for b in native if b["book_id"] == book_id), None)
                    if source is None:
                        raise HTTPException(404, "course book membership not found")
                    member = {"book_id": book_id, "source_course_id": cid,
                        "title": source.get("title") or book_id, "role": source.get("role", "spine"),
                        "file_sha256": source.get("file_sha256", "")}
                    record.setdefault("books", []).append(member)
                member["display_name"] = name
                save_metadata(cid, record)
                return member
        return await run_in_threadpool(rename)


    return router
