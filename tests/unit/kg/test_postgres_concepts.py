from __future__ import annotations
import json

import pytest
from b2c_contracts import ConceptEdge
from b2c_kg import ConceptEvidence, PostgresConceptRelationStore


def edge(*chunks: str, confidence: float = .91) -> ConceptEdge:
    return ConceptEdge(
        src_entity="concept:probability",
        dst_entity="concept:random-variable",
        type="prerequisite_of",
        evidence_chunk_ids=list(chunks),
        confidence=confidence,
        classifier={"model": "fixture", "prompt_version": "unit-v1"},
    )


def evidence(chunk: str, book: str, page: int, role: str = "spine") -> ConceptEvidence:
    return ConceptEvidence(chunk, book, role, (page,))  # type: ignore[arg-type]


class MemoryCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = 0

    def __enter__(self): return self
    def __exit__(self, *_): return False

    def execute(self, sql, params=None):
        q = " ".join(sql.split()).lower()
        p = params or ()
        self.rows = []
        self.rowcount = 0
        if q.startswith("create "):
            return
        if q.startswith("insert into b2c_concept_relations"):
            key = tuple(p[:4])
            self.conn.relations.setdefault(key, (p[4], json.loads(p[5])))
        elif q.startswith("select confidence, classifier"):
            self.rows = [self.conn.relations[tuple(p)]] if tuple(p) in self.conn.relations else []
        elif q.startswith("insert into b2c_concept_evidence"):
            relation_key = tuple(p[:4])
            key = (*relation_key, p[4])
            self.conn.evidence.setdefault(key, (p[5], p[6], tuple(json.loads(p[7]))))
        elif q.startswith("select book_id, role, pages"):
            key = (*tuple(p[:4]), p[4])
            self.rows = [self.conn.evidence[key]] if key in self.conn.evidence else []
        elif q.startswith("select r.src_entity, r.dst_entity, r.relation_type, r.confidence"):
            scope = p[0]
            for key, (confidence, classifier) in sorted(self.conn.relations.items()):
                if key[0] != scope: continue
                for evidence_key, (book, role, pages) in sorted(self.conn.evidence.items()):
                    if evidence_key[:4] == key:
                        self.rows.append((*key[1:], confidence, classifier,
                                          evidence_key[4], book, role, list(pages)))
        elif q.startswith("select r.confidence, r.classifier"):
            relation_key = tuple(p)
            for key, (confidence, classifier) in sorted(self.conn.relations.items()):
                if key == relation_key:
                    for evidence_key, (book, role, pages) in sorted(self.conn.evidence.items()):
                        if evidence_key[:4] == relation_key:
                            self.rows.append((confidence, classifier, evidence_key[4], book, role, list(pages)))
        elif q.startswith("delete from b2c_concept_evidence"):
            scope, book = p
            for key, value in list(self.conn.evidence.items()):
                if key[0] == scope and value[0] == book:
                    self.rows.append(key[1:])
                    del self.conn.evidence[key]
        elif q.startswith("delete from b2c_concept_relations"):
            scope = p[0]
            for key in list(self.conn.relations):
                if key[0] == scope and not any(e[:4] == key for e in self.conn.evidence):
                    del self.conn.relations[key]
        else:
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return list(self.rows)


class MemoryConnection:
    def __init__(self):
        self.relations = {}
        self.evidence = {}
        self.context_entries = 0
    def __enter__(self):
        self.context_entries += 1
        return self
    def __exit__(self, *_): return False
    def cursor(self): return MemoryCursor(self)


def new_store():
    conn = MemoryConnection()
    return PostgresConceptRelationStore(lambda: conn), conn


def test_postgres_candidate_merges_evidence_idempotently_and_lists_pages():
    store, _ = new_store()
    store.ensure_schema()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    repeated = store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    [found] = store.list_relations("course-a")
    assert found == repeated
    assert found.edge.evidence_chunk_ids == ["chunk-a", "chunk-b"]
    assert [(e.chunk_id, e.book_id, e.role, e.pages) for e in found.evidence] == [
        ("chunk-a", "book-a", "spine", (12,)),
        ("chunk-b", "book-b", "supplementary", (31,)),
    ]


def test_scope_isolation_and_book_deletion_retains_other_book_evidence():
    store, _ = new_store()
    store.put("course-a", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    store.put("course-a", edge("chunk-b"), [evidence("chunk-b", "book-b", 31, "supplementary")])
    store.put("course-b", edge("chunk-a"), [evidence("chunk-a", "book-a", 12)])
    assert len(store.list_relations("course-a")) == len(store.list_relations("course-b")) == 1
    assert store.delete_book("course-a", "book-a") == 1
    [remaining] = store.list_relations("course-a")
    assert remaining.edge.evidence_chunk_ids == ["chunk-b"]
    assert [e.book_id for e in remaining.evidence] == ["book-b"]
    assert len(store.list_relations("course-b")) == 1
    assert store.delete_book("course-a", "book-b") == 1
    assert store.list_relations("course-a") == []
    assert len(store.list_relations("course-b")) == 1


def test_conflicting_relation_or_chunk_metadata_is_rejected():
    store, _ = new_store()
    store.put("scope", edge("chunk-a"), [evidence("chunk-a", "book-a", 2)])
    with pytest.raises(ValueError, match="relation metadata conflicts"):
        store.put("scope", edge("chunk-b", confidence=.3), [evidence("chunk-b", "book-b", 3)])
    changed = evidence("chunk-a", "book-other", 2)
    with pytest.raises(ValueError, match="evidence metadata conflicts"):
        store.put("scope", edge("chunk-a"), [changed])


@pytest.mark.parametrize(
    "scope, chunks, citations, message",
    [
        ("", ("c",), [evidence("c", "b", 1)], "scope_id"),
        ("s", ("c",), [], "at least one"),
        ("s", ("c", "d"), [evidence("c", "b", 1)], "every ConceptEdge"),
        ("s", ("c",), [evidence("other", "b", 1)], "absent from ConceptEdge"),
    ],
)
def test_invalid_scope_and_incomplete_or_mismatched_evidence_rejected(scope, chunks, citations, message):
    store, _ = new_store()
    with pytest.raises(ValueError, match=message):
        store.put(scope, edge(*chunks), citations)


def test_empty_scope_or_book_cannot_be_listed_or_deleted():
    store, _ = new_store()
    with pytest.raises(ValueError, match="scope_id"):
        store.list_relations(" ")
    with pytest.raises(ValueError, match="scope_id and book_id"):
        store.delete_book("scope", "")
def test_put_many_executes_in_one_connection_transaction():
    store, conn = new_store()
    before = conn.context_entries
    second = ConceptEdge(
        src_entity="concept:definition",
        dst_entity="concept:probability",
        type="defines",
        evidence_chunk_ids=["chunk-b"],
        confidence=.8,
        classifier={"model": "fixture", "prompt_version": "unit-v1"},
    )
    first = edge("chunk-a")
    result = store.put_many("course-a", [
        (first, [evidence("chunk-a", "book-a", 12)]),
        (second, [evidence("chunk-b", "book-b", 18, "supplementary")]),
    ])
    assert conn.context_entries - before == 1
    assert len(result) == 2
    assert len(store.list_relations("course-a")) == 2
