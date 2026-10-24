"""Disposable live Postgres isolation smoke for QueryRetrievalService."""
from __future__ import annotations
import os
import uuid
import psycopg
from b2c_contracts.models import StructuredChunk
from b2c_indexing import PostgresChunkStore, QueryRetrievalService

suffix = uuid.uuid4().hex
course_a, course_b = f"phase3-query-a-{suffix}", f"phase3-query-b-{suffix}"
book_main, book_extra = f"phase3-main-{suffix}", f"phase3-extra-{suffix}"
def connect():
    return psycopg.connect(host=os.environ["B2C_PG_HOST"], port=int(os.getenv("B2C_PG_PORT", "5432")),
        dbname=os.environ["B2C_PG_DB"], user=os.environ["B2C_PG_USER"],
        password=os.environ["B2C_PG_PASSWORD"])
def make(book, role, cid, text):
    return StructuredChunk(chunk_id=cid, book_id=book, role=role, section_path=["smoke"],
        block_ids=[cid], pages=[1], text=text, token_count=len(text.split()), embedding_model="unassigned")
store = PostgresChunkStore(connect)
store.ensure_schema()
try:
    store.upsert_batch(course_a, [
        make(book_main, "spine", "main:1", "Bayes theorem supports conditional probability."),
        make(book_extra, "supplementary", "extra:1", "Bayes supplementary discussion of probability.")])
    store.upsert_batch(course_b, [make(book_main, "spine", "other-course:1",
        "Bayes probability belongs only to the other course.")])
    service = QueryRetrievalService(store)
    all_a = service.search(course_a, "Bayes probability", top_k=10)
    scoped = service.search(course_a, "Bayes probability", allowed_book_ids={book_main},
                            allowed_roles={"spine"})
    only_b = service.search(course_b, "Bayes probability", allowed_book_ids={book_main},
                             allowed_roles={"spine"})
    assert {hit.chunk.chunk_id for hit in all_a} == {"main:1", "extra:1"}
    assert [(hit.chunk.chunk_id, hit.chunk.role, hit.chunk.book_id) for hit in scoped] == [
        ("main:1", "spine", book_main)]
    assert [(hit.chunk.chunk_id, hit.chunk.book_id) for hit in only_b] == [
        ("other-course:1", book_main)]
    assert not any(hit.chunk.chunk_id == "other-course:1" for hit in all_a)
    print("PASS: course isolation, book filter, role filter, and lexical query-time retrieval")
finally:
    store.delete_book(course_a, book_main)
    store.delete_book(course_a, book_extra)
    store.delete_book(course_b, book_main)
