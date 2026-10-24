"""PostgreSQL adjacency adapter for evidence-backed concept relations.

This is a small candidate backend for Phase 4 comparison. It stores typed
relations and their source evidence as relational rows; it does not create or
select a graph engine, retrieval strategy, or embedding model.
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from b2c_contracts import ConceptEdge

from .concepts import ConceptEvidence, ConceptRelationStore, EvidenceBackedRelation

ConnectionFactory = Callable[[], Any]

_DDL_RELATIONS = """
CREATE TABLE IF NOT EXISTS b2c_concept_relations (
    scope_id TEXT NOT NULL,
    src_entity TEXT NOT NULL,
    dst_entity TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    classifier JSONB NOT NULL,
    PRIMARY KEY (scope_id, src_entity, dst_entity, relation_type)
)
"""

_DDL_EVIDENCE = """
CREATE TABLE IF NOT EXISTS b2c_concept_evidence (
    scope_id TEXT NOT NULL,
    src_entity TEXT NOT NULL,
    dst_entity TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('spine', 'supplementary')),
    pages JSONB NOT NULL,
    PRIMARY KEY (scope_id, src_entity, dst_entity, relation_type, chunk_id),
    FOREIGN KEY (scope_id, src_entity, dst_entity, relation_type)
        REFERENCES b2c_concept_relations (scope_id, src_entity, dst_entity, relation_type)
        ON DELETE CASCADE
)
"""

_DDL_BOOK_INDEX = """
CREATE INDEX IF NOT EXISTS b2c_concept_evidence_scope_book_idx
ON b2c_concept_evidence (scope_id, book_id)
"""


class PostgresConceptRelationStore(ConceptRelationStore):
    """Durable adjacency-list implementation of ConceptRelationStore.

    Inject a connection factory so callers own PostgreSQL credentials, pooling,
    and lifecycle. ensure_schema is explicit and safe to repeat. Writes merge
    distinct evidence chunks idempotently; changed relation metadata or changed
    source metadata for an existing chunk is rejected.
    """

    def __init__(self, connect: ConnectionFactory):
        self._connect = connect

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL_RELATIONS)
                cur.execute(_DDL_EVIDENCE)
                cur.execute(_DDL_BOOK_INDEX)

    def put(self, scope_id: str, edge: ConceptEdge,
            evidence: list[ConceptEvidence]) -> EvidenceBackedRelation:
        return self.put_many(scope_id, [(edge, evidence)])[0]

    def put_many(self, scope_id: str,
                 writes: list[tuple[ConceptEdge, list[ConceptEvidence]]]
                 ) -> list[EvidenceBackedRelation]:
        """Write and merge a batch in one PostgreSQL transaction."""
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        validated = [(edge, _validated_evidence(scope_id, edge, evidence))
                     for edge, evidence in writes]
        if not validated:
            return []

        relation_keys = [
            (scope_id, edge.src_entity, edge.dst_entity, edge.type)
            for edge, _ in validated
        ]
        with self._connect() as conn:
            with conn.cursor() as cur:
                for (edge, by_chunk), relation_key in zip(validated, relation_keys):
                    cur.execute(
                        """INSERT INTO b2c_concept_relations
                           (scope_id, src_entity, dst_entity, relation_type, confidence, classifier)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (scope_id, src_entity, dst_entity, relation_type) DO NOTHING""",
                        (*relation_key, edge.confidence,
                         json.dumps(edge.classifier, sort_keys=True, ensure_ascii=False)),
                    )
                    cur.execute(
                        """SELECT confidence, classifier FROM b2c_concept_relations
                           WHERE scope_id = %s AND src_entity = %s AND dst_entity = %s
                             AND relation_type = %s FOR UPDATE""",
                        relation_key,
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise RuntimeError("relation insert was not visible in its transaction")
                    confidence, classifier = existing
                    if confidence != edge.confidence or _json_value(classifier) != edge.classifier:
                        raise ValueError("relation metadata conflicts with the existing edge")

                    for item in by_chunk.values():
                        cur.execute(
                            """INSERT INTO b2c_concept_evidence
                               (scope_id, src_entity, dst_entity, relation_type, chunk_id,
                                book_id, role, pages)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                               ON CONFLICT (scope_id, src_entity, dst_entity, relation_type, chunk_id)
                               DO NOTHING""",
                            (*relation_key, item.chunk_id, item.book_id, item.role,
                             json.dumps(list(item.pages))),
                        )
                        cur.execute(
                            """SELECT book_id, role, pages FROM b2c_concept_evidence
                               WHERE scope_id = %s AND src_entity = %s AND dst_entity = %s
                                 AND relation_type = %s AND chunk_id = %s""",
                            (*relation_key, item.chunk_id),
                        )
                        stored = cur.fetchone()
                        if stored is None or (
                            stored[0], stored[1], tuple(_json_value(stored[2]))
                        ) != (item.book_id, item.role, item.pages):
                            raise ValueError(
                                f"evidence metadata conflicts with existing chunk {item.chunk_id!r}"
                            )
                return [self._read_relation(cur, key) for key in relation_keys]

    def list_relations(self, scope_id: str) -> list[EvidenceBackedRelation]:
        if not scope_id.strip():
            raise ValueError("scope_id must be non-empty")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT r.src_entity, r.dst_entity, r.relation_type,
                              r.confidence, r.classifier, e.chunk_id, e.book_id,
                              e.role, e.pages
                       FROM b2c_concept_relations AS r
                       JOIN b2c_concept_evidence AS e
                         USING (scope_id, src_entity, dst_entity, relation_type)
                       WHERE r.scope_id = %s
                       ORDER BY r.src_entity, r.dst_entity, r.relation_type, e.chunk_id""",
                    (scope_id,),
                )
                rows = cur.fetchall()
        return _relations_from_rows(scope_id, rows)

    def delete_book(self, scope_id: str, book_id: str) -> int:
        """Delete one book's evidence in one scope, retaining other support.

        Returns the number of relations that lost at least one evidence row,
        matching the in-memory adapter's behavior.
        """
        if not scope_id.strip() or not book_id.strip():
            raise ValueError("scope_id and book_id must be non-empty")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """DELETE FROM b2c_concept_evidence
                       WHERE scope_id = %s AND book_id = %s
                       RETURNING src_entity, dst_entity, relation_type""",
                    (scope_id, book_id),
                )
                affected = len(set(cur.fetchall()))
                cur.execute(
                    """DELETE FROM b2c_concept_relations AS r
                       WHERE r.scope_id = %s AND NOT EXISTS (
                           SELECT 1 FROM b2c_concept_evidence AS e
                           WHERE e.scope_id = r.scope_id
                             AND e.src_entity = r.src_entity
                             AND e.dst_entity = r.dst_entity
                             AND e.relation_type = r.relation_type
                       )""",
                    (scope_id,),
                )
        return affected

    @staticmethod
    def _read_relation(cur: Any, relation_key: tuple[str, str, str, str]) -> EvidenceBackedRelation:
        scope_id, src, dst, relation_type = relation_key
        cur.execute(
            """SELECT r.confidence, r.classifier, e.chunk_id, e.book_id, e.role, e.pages
               FROM b2c_concept_relations AS r
               JOIN b2c_concept_evidence AS e
                 USING (scope_id, src_entity, dst_entity, relation_type)
               WHERE r.scope_id = %s AND r.src_entity = %s AND r.dst_entity = %s
                 AND r.relation_type = %s
               ORDER BY e.chunk_id""",
            relation_key,
        )
        rows = cur.fetchall()
        if not rows:
            raise RuntimeError("relation has no persisted evidence")
        confidence, classifier = rows[0][:2]
        evidence = tuple(
            ConceptEvidence(chunk_id=row[2], book_id=row[3], role=row[4],
                            pages=tuple(_json_value(row[5])))
            for row in rows
        )
        edge = ConceptEdge(
            src_entity=src, dst_entity=dst, type=relation_type,
            evidence_chunk_ids=[item.chunk_id for item in evidence],
            confidence=confidence, classifier=_json_value(classifier),
        )
        return EvidenceBackedRelation(scope_id=scope_id, edge=edge, evidence=evidence)


def _validated_evidence(scope_id: str, edge: ConceptEdge,
                        evidence: list[ConceptEvidence]) -> dict[str, ConceptEvidence]:
    if not scope_id.strip():
        raise ValueError("scope_id must be non-empty")
    if not evidence:
        raise ValueError("at least one evidence citation is required")
    by_chunk: dict[str, ConceptEvidence] = {}
    for item in evidence:
        if item.chunk_id not in edge.evidence_chunk_ids:
            raise ValueError(f"evidence chunk {item.chunk_id!r} is absent from ConceptEdge")
        prior = by_chunk.get(item.chunk_id)
        if prior is not None and prior != item:
            raise ValueError(f"conflicting evidence metadata for chunk {item.chunk_id!r}")
        by_chunk[item.chunk_id] = item
    if set(by_chunk) != set(edge.evidence_chunk_ids):
        raise ValueError("every ConceptEdge evidence_chunk_id needs page-aware evidence")
    return by_chunk


def _relations_from_rows(scope_id: str, rows: list[tuple[Any, ...]]) -> list[EvidenceBackedRelation]:
    grouped: dict[tuple[str, str, str], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        grouped[(row[0], row[1], row[2])].append(row)
    result = []
    for (src, dst, relation_type), values in sorted(grouped.items()):
        confidence, classifier = values[0][3:5]
        evidence = tuple(
            ConceptEvidence(chunk_id=row[5], book_id=row[6], role=row[7],
                            pages=tuple(_json_value(row[8])))
            for row in values
        )
        edge = ConceptEdge(
            src_entity=src, dst_entity=dst, type=relation_type,
            evidence_chunk_ids=[item.chunk_id for item in evidence],
            confidence=confidence, classifier=_json_value(classifier),
        )
        result.append(EvidenceBackedRelation(scope_id=scope_id, edge=edge, evidence=evidence))
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value
