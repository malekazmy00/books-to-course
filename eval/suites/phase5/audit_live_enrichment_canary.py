from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import psycopg
import b2c_pedagogy
sys.path.insert(0, "/tmp/phase5-live-src/synthesis")
b2c_pedagogy.__path__.insert(0, "/tmp/phase5-live-src")

from b2c_contracts import ArtifactRef, CourseOutline, ParsedBook
from b2c_core import Settings
from b2c_core.artifacts import ArtifactStore, content_sha256
from b2c_indexing import PostgresChunkStore
from b2c_pedagogy.lesson_enrichment import LessonEnrichment, _derive_scopes
from b2c_pedagogy.validation import validate_lesson_assets
from b2c_synthesis.validation import validate_citations


def latest_approved_outline(store: ArtifactStore, course_id: str) -> ArtifactRef:
    response = store.s3.list_objects_v2(
        Bucket=store.bucket, Prefix=f"{course_id}/approve-outline/", MaxKeys=1000
    )
    refs = []
    for item in response.get("Contents", []):
        if not item["Key"].endswith("/_manifest.json"):
            continue
        content = store.s3.get_object(Bucket=store.bucket, Key=item["Key"])["Body"].read()
        ref = ArtifactRef.model_validate_json(content)
        if ref.stage == "approve-outline":
            refs.append(ref)
    if not refs:
        raise RuntimeError("approved outline artifact not found")
    return max(refs, key=lambda ref: ref.created_at)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--artifact-key", required=True)
    parser.add_argument("--repair-envelope", action="store_true")
    parser.add_argument("--export-path")
    args = parser.parse_args()

    settings = Settings()
    store = ArtifactStore.from_settings(settings)
    source_ref = store.exists(args.course_id, "phase5-enrichment-canary", args.artifact_key)
    if source_ref is None:
        raise RuntimeError("requested source canary artifact not found")
    source_bytes = store.read(source_ref, "lesson-enrichment.json")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    if content_sha256({"lesson-enrichment.json": source_bytes}) != source_ref.sha256:
        raise RuntimeError("source artifact manifest digest mismatch")
    payload = json.loads(source_bytes)

    if args.repair_envelope:
        # Repair only the wrapper metadata. Generated lesson/question content is unchanged.
        payload["artifact_status"] = "evaluation_only"
        payload["status"] = "final"
        automated = payload.get("automated_evaluation", {})
        automated.pop("solver_gate", None)
        payload["automated_evaluation"] = automated
        metadata = payload.setdefault("evaluation_metadata", {})
        metadata["solver_status"] = "not_run"
        metadata["envelope_repair"] = "status_and_typed_evaluation_metadata_v1"
        metadata["parent_artifact_ref"] = {
            "key": source_ref.key, "sha256": source_ref.sha256, "uri": source_ref.uri
        }
        repaired_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        repair_key = hashlib.sha256(
            f"{source_ref.sha256}:{args.lesson_id}:phase5-envelope-contract-repair-v1".encode()
        ).hexdigest()
        ref = store.put(args.course_id, "phase5-enrichment-canary", repair_key,
                        {"lesson-enrichment.json": repaired_bytes})
        raw = store.read(ref, "lesson-enrichment.json")
        if raw != repaired_bytes:
            raise RuntimeError("repaired artifact readback differs from the written bytes")
    else:
        ref, raw = source_ref, source_bytes

    data = json.loads(raw)
    lesson = LessonEnrichment.model_validate(data)
    if lesson.lesson_id != args.lesson_id or lesson.status != "final":
        raise RuntimeError("LessonEnrichment contract or requested lesson mismatch")

    approved_ref = latest_approved_outline(store, args.course_id)
    approved = json.loads(store.read(approved_ref, "approved-outline.json"))
    outline = CourseOutline.model_validate(approved["outline"])
    parsed_ref = ArtifactRef.model_validate(approved["spine_parsed_ref"])
    spine = ParsedBook.model_validate_json(store.read(parsed_ref, "parsed-book.json"))
    chunks = PostgresChunkStore(lambda: psycopg.connect(settings.pg_dsn)).list_chunks(
        args.course_id, book_ids={outline.spine_book_id}
    )
    scopes, _ = _derive_scopes(
        outline, spine, chunks, lesson_question_count=10, group_question_count=1,
        course_question_count=1, outline_approved=True,
    )
    matches = [scope for scope in scopes
               if scope.kind == "lesson" and scope.scope_id == args.lesson_id]
    if len(matches) != 1:
        raise RuntimeError("lesson scope is not uniquely present in the approved outline")
    scope = matches[0]

    citation_report = validate_citations(lesson.draft, scope.chunks)
    asset_report = validate_lesson_assets(
        lesson.draft, lesson.summary, lesson.glossary, lesson.questions
    )
    sentence_source_ids = {
        chunk_id for sentence in lesson.draft.sentences for chunk_id in sentence.citations
    }
    allowed_ids = {chunk.chunk_id for chunk in scope.chunks}
    higher_bloom = sum(q.bloom != "remember" for q in lesson.questions)
    bloom_pass = bool(lesson.questions) and higher_bloom / len(lesson.questions) >= 0.8
    metadata_source_ids = set(data.get("evaluation_metadata", {}).get("source_chunk_ids", []))
    gates = {
        "contract_valid": True,
        "citation_resolution_and_role": citation_report.valid,
        "lesson_summary_glossary_question_links": asset_report.valid,
        "citations_within_approved_spine_scope": sentence_source_ids <= allowed_ids,
        "bloom_at_least_80_percent_above_remember": bloom_pass,
        "source_id_metadata_matches_content": metadata_source_ids == sentence_source_ids,
    }
    if not all(gates.values()):
        raise RuntimeError("one or more readback source/lesson quality gates failed")

    final_sha = hashlib.sha256(raw).hexdigest()
    if content_sha256({"lesson-enrichment.json": raw}) != ref.sha256:
        raise RuntimeError("final artifact manifest digest mismatch")
    if args.export_path:
        export_path = Path(args.export_path)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_bytes(raw)
        if hashlib.sha256(export_path.read_bytes()).hexdigest() != final_sha:
            raise RuntimeError("data-disk export hash mismatch")
    else:
        export_path = None

    print(json.dumps({
        "status": "readback_audit_pass",
        "artifact_ref": ref.model_dump(mode="json"),
        "raw_sha256": final_sha,
        "source_artifact_raw_sha256": source_sha,
        "source_artifact_key": source_ref.key,
        "lesson_id": lesson.lesson_id,
        "lesson_sentences": len(lesson.draft.sentences),
        "summary_points": len(lesson.summary.points),
        "glossary_terms": len(lesson.glossary),
        "mcqs": len(lesson.questions),
        "higher_bloom_count": higher_bloom,
        "provider_usage": data.get("evaluation_metadata", {}).get("provider_usage", {}),
        "gates": gates,
        "export_path": str(export_path) if export_path else None,
        "gold_eligible": data.get("evaluation_metadata", {}).get("gold_eligible", False),
        "solver_status": data.get("evaluation_metadata", {}).get("solver_status", "not_run"),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

