"""Stable PDF identity alongside immutable processing-artifact identity.

The aggregate ArtifactRef SHA hashes named output files; it is never the PDF SHA.
Legacy parsed artifacts without a PDF fingerprint remain explicitly unverified.
"""
from __future__ import annotations
import json
import re
from typing import Any
from b2c_contracts import ArtifactRef, ParsedBook


def parsed_source_identity(parsed: ParsedBook, ref: ArtifactRef) -> dict[str, Any]:
    digest = parsed.parser.get("source_file_sha256")
    if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid source PDF fingerprint")
    return {"book_id": parsed.book_id, "source_file_sha256": digest,
            "source_parsed_sha256": ref.sha256,
            "source_parsed_ref": ref.model_dump(mode="json"),
            "source_fingerprint_verified": digest is not None}


def artifact_source_identity(store: Any, ref: ArtifactRef) -> dict[str, Any]:
    """Resolve the immutable source chain, never infer it from a mutable book ID."""
    seen: set[str] = set()
    course_prefix = "/".join(ref.uri.split("/")[:4]) + "/"
    def resolve(current: ArtifactRef) -> dict[str, Any]:
        if not current.uri.startswith(course_prefix):
            raise ValueError("source lineage leaves the artifact course")
        if current.uri in seen:
            raise ValueError("cyclic source artifact lineage")
        seen.add(current.uri)
        if current.stage == "ingest-parse-pdf":
            parsed = ParsedBook.model_validate_json(store.read(current, "parsed-book.json"))
            identity = parsed_source_identity(parsed, current)
            if identity["source_file_sha256"] is None and getattr(store, "s3", None) and getattr(store, "bucket", None):
                cid = current.uri.split("/")[3]
                key = f"_system/source-identities/{cid}/by-parsed/{current.sha256}.json"
                try:
                    mapped = json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())
                except Exception as exc:
                    code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
                    if not isinstance(exc, KeyError) and code != 404 and type(exc).__name__ != "NoSuchKey":
                        raise
                    mapped = None
                if mapped:
                    if (mapped.get("source_parsed_sha256") != current.sha256 or
                            ArtifactRef.model_validate(mapped.get("source_parsed_ref")) != current or
                            mapped.get("artifact_book_id") != parsed.book_id or
                            not re.fullmatch(r"[0-9a-f]{64}", str(mapped.get("source_file_sha256", "")))):
                        raise ValueError("legacy source mapping differs from exact parsed artifact")
                    upload = ArtifactRef.model_validate(mapped["source_upload_ref"])
                    if not upload.uri.startswith(course_prefix) or upload.stage != "book-upload":
                        raise ValueError("legacy upload mapping scope mismatch")
                    metadata = json.loads(store.read(upload,"book-upload.json"))
                    if metadata.get("file_sha256") != mapped["source_file_sha256"] or metadata.get("book_id") != parsed.book_id:
                        raise ValueError("legacy upload mapping fingerprint mismatch")
                    identity.update({"source_file_sha256":mapped["source_file_sha256"],
                        "external_book_id":mapped["external_book_id"],
                        "source_fingerprint_verified":True,"source_mapping_id":mapped["migration_id"]})
            return identity
        if current.stage == "translate-content":
            payload = json.loads(store.read(current, "translation-source.json"))
            return resolve(ArtifactRef.model_validate(payload["source_artifact_ref"]))
        if current.stage == "approve-outline":
            payload = json.loads(store.read(current, "approved-outline.json"))
            return resolve(ArtifactRef.model_validate(payload["spine_parsed_ref"]))
        if current.stage == "generate-course-qa":
            try:
                payload = json.loads(store.read(current, "source-identity.json"))
            except Exception as exc:
                code = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
                if not isinstance(exc, KeyError) and code != 404 and type(exc).__name__ != "NoSuchKey":
                    raise
                legacy = json.loads(store.read(current, "course-qa.json"))
                return {"book_id": legacy.get("spine_book_id"),
                        "source_file_sha256": None,
                        "source_parsed_sha256": legacy.get("spine_parsed_sha256"),
                        "source_fingerprint_verified": False}
            parsed_ref = ArtifactRef.model_validate(payload["source_parsed_ref"])
            identity = resolve(parsed_ref)
            qa = json.loads(store.read(current, "course-qa.json"))
            if (any(payload.get(name) != identity.get(name) for name in
                    ("book_id", "source_parsed_sha256", "source_parsed_ref")) or
                    payload.get("source_file_sha256") not in (None, identity["source_file_sha256"]) or
                    qa.get("spine_parsed_sha256") != parsed_ref.sha256 or
                    qa.get("spine_book_id") != identity["book_id"]):
                raise ValueError("QA source identity differs from immutable parsed book")
            return identity
        raise ValueError("artifact has no resolvable book source identity")
    return resolve(ref)


def source_identity_file(identity: dict[str, Any]) -> bytes:
    return json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
