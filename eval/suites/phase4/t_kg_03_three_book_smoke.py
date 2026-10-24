"""Live disposable-scope proof that adding a third book preserves prior graph entities."""
from __future__ import annotations

import os
import uuid

import psycopg
from b2c_contracts import ConceptEdge
from b2c_kg import ConceptEvidence, PostgresConceptRelationStore


def connect():
    return psycopg.connect(
        host=os.environ["B2C_PG_HOST"],
        port=int(os.getenv("B2C_PG_PORT", "5432")),
        dbname=os.environ["B2C_PG_DB"],
        user=os.environ["B2C_PG_USER"],
        password=os.environ["B2C_PG_PASSWORD"],
    )


suffix = uuid.uuid4().hex
scope = f"phase4-kg03-three-book-{suffix}"
books = [f"kg03-book-{n}-{suffix}" for n in (1, 2, 3)]
store = PostgresConceptRelationStore(connect)
store.ensure_schema()


def insert(book: str, role: str, entity_a: str, entity_b: str, chunk_id: str, page: int) -> None:
    store.put(
        scope,
        ConceptEdge(
            src_entity=entity_a,
            dst_entity=entity_b,
            type="part_of",
            evidence_chunk_ids=[chunk_id],
            confidence=0.5,
            classifier={"model": "deterministic-t-kg-03-fixture", "prompt_version": "1"},
        ),
        [ConceptEvidence(chunk_id=chunk_id, book_id=book, role=role, pages=(page,))],
    )


try:
    insert(books[0], "spine", "Alpha", "Beta", f"c1-{suffix}", 1)
    insert(books[1], "supplementary", "Beta", "Gamma", f"c2-{suffix}", 2)
    before = store.list_relations(scope)
    before_entities = {value for relation in before for value in (relation.edge.src_entity, relation.edge.dst_entity)}
    insert(books[2], "supplementary", "Delta", "Epsilon", f"c3-{suffix}", 3)
    after = store.list_relations(scope)
    after_entities = {value for relation in after for value in (relation.edge.src_entity, relation.edge.dst_entity)}
    assert before_entities == {"Alpha", "Beta", "Gamma"}
    assert before_entities <= after_entities, "adding book three removed an earlier entity"
    assert after_entities == {"Alpha", "Beta", "Gamma", "Delta", "Epsilon"}
    assert len(after) == 3
    assert {e.book_id for relation in after for e in relation.evidence} == set(books)
    print("PASS T-KG-03: third-book insertion retained all 3/3 prior entities and added the third-book entities")
finally:
    for book in books:
        store.delete_book(scope, book)
    assert store.list_relations(scope) == [], "temporary T-KG-03 scope was not fully cleaned"
