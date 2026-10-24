"""Disposable live Postgres smoke for concept-graph retrieval provenance and isolation."""
from __future__ import annotations
import os
import uuid
import psycopg
from b2c_contracts import ConceptEdge, StructuredChunk
from b2c_kg import ConceptEvidence, ConceptGraphRetriever, PostgresConceptRelationStore

suffix = uuid.uuid4().hex
scope = f"phase4-graph-query-{suffix}"
other_scope = f"{scope}-other"
book_main, book_supp = f"phase4-main-{suffix}", f"phase4-supp-{suffix}"

def connect():
    return psycopg.connect(host=os.environ["B2C_PG_HOST"], port=int(os.getenv("B2C_PG_PORT", "5432")),
        dbname=os.environ["B2C_PG_DB"], user=os.environ["B2C_PG_USER"],
        password=os.environ["B2C_PG_PASSWORD"])

def make_chunk(chunk_id: str, book_id: str, role: str, page: int, text: str) -> StructuredChunk:
    return StructuredChunk(chunk_id=chunk_id, book_id=book_id, role=role,
        section_path=["Graph smoke"], block_ids=[f"{chunk_id}:block-1"], pages=[page],
        text=text, token_count=len(text.split()), embedding_model="unassigned")

direct = make_chunk("direct-evidence", book_main, "spine", 7,
    "AlphaConcept connects directly with BetaConcept in this source passage.")
one_hop = make_chunk("one-hop-evidence", book_supp, "supplementary", 22,
    "BetaConcept is related to GammaConcept in supplementary evidence.")
chunks = [direct, one_hop]
store = PostgresConceptRelationStore(connect)
store.ensure_schema()
retriever = ConceptGraphRetriever(store, scope_id=scope)
other_retriever = ConceptGraphRetriever(store, scope_id=other_scope)

def edge(src: str, dst: str, chunk_id: str) -> ConceptEdge:
    return ConceptEdge(src_entity=src, dst_entity=dst, type="prerequisite_of",
        evidence_chunk_ids=[chunk_id], confidence=0.91,
        classifier={"model": "smoke-fixture", "prompt_version": "1"})

try:
    store.put(scope, edge("AlphaConcept", "BetaConcept", direct.chunk_id), [
        ConceptEvidence(chunk_id=direct.chunk_id, book_id=book_main, role="spine", pages=(7,))])
    store.put(scope, edge("BetaConcept", "GammaConcept", one_hop.chunk_id), [
        ConceptEvidence(chunk_id=one_hop.chunk_id, book_id=book_supp, role="supplementary", pages=(22,))])

    persisted = store.list_relations(scope)
    assert len(persisted) == 2
    provenance = {(relation.edge.src_entity, relation.edge.dst_entity): relation.evidence[0]
                  for relation in persisted}
    assert provenance[("AlphaConcept", "BetaConcept")] == ConceptEvidence(
        chunk_id=direct.chunk_id, book_id=book_main, role="spine", pages=(7,))
    assert provenance[("BetaConcept", "GammaConcept")] == ConceptEvidence(
        chunk_id=one_hop.chunk_id, book_id=book_supp, role="supplementary", pages=(22,))

    all_hits = retriever.search("AlphaConcept", chunks, top_k=5)
    assert [(item.chunk_id, score) for item, score in all_hits] == [
        (direct.chunk_id, 1.0), (one_hop.chunk_id, 0.5)]
    assert all_hits[0][0] == direct and all_hits[1][0] == one_hop
    assert all_hits[0][0].pages == [7] and all_hits[0][0].role == "spine"
    assert all_hits[1][0].pages == [22] and all_hits[1][0].role == "supplementary"

    main_only = retriever.search("AlphaConcept", chunks, allowed_roles={"spine"})
    supp_only = retriever.search("AlphaConcept", chunks, allowed_book_ids={book_supp})
    assert [(item.chunk_id, score) for item, score in main_only] == [(direct.chunk_id, 1.0)]
    assert [(item.chunk_id, score) for item, score in supp_only] == [(one_hop.chunk_id, 0.5)]
    assert other_retriever.search("AlphaConcept", chunks) == []
    print("PASS: persisted direct and one-hop evidence, exact chunk/page/role attribution, role/book filters, scope isolation")
finally:
    store.delete_book(scope, book_main)
    store.delete_book(scope, book_supp)
    assert store.list_relations(scope) == [], "test scope cleanup left relation or evidence rows"
    assert store.list_relations(other_scope) == [], "unexpected rows found in the isolated comparison scope"
