"""Durable MP3 generation for books and per-lesson courses."""
from __future__ import annotations
import hashlib
import json
import os
import threading
import urllib.request
from urllib.error import HTTPError
from datetime import timedelta
from typing import Any, Literal
from pydantic import BaseModel, Field
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

AUDIO_RENDER_POLICY = "sentence-aware-boundaries-v4-short-arabic-terminal"

def renderer_status_retryable(status: int) -> bool:
    return status in {408, 425, 429} or status >= 500
with workflow.unsafe.imports_passed_through():
    from .controls import wait_for_activity_checkpoint, execute_checkpointed
    from .heartbeat import LONG_ACTIVITY_HEARTBEAT_TIMEOUT, activity_heartbeats
    from b2c_contracts import ArtifactRef, ParsedBook, CourseOutline, stage_key
    from b2c_core.processing_plan import detect_source_language
    from b2c_core.source_identity import artifact_source_identity, source_identity_file
class GenerateMP3Input(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    source_artifact_ref: dict[str, Any]
    source_kind: Literal["book", "course"]
    language: Literal["en", "ar"]
    approved_outline_ref: dict[str, Any] | None = None
    source_qa_artifact_ref: dict[str, Any] | None = None
    source_book_ref: dict[str, Any] | None = None
    source_book_translation_ref: dict[str, Any] | None = None
    book_audio_layout: Literal["full_book", "lessons"] = "full_book"
    lesson_ids: list[str] | None = None
    allow_partial_qa: bool = False
    include_summaries: bool = True
    include_extra_scopes: bool = True
    activity_task_queue: str = "heavy"
    # R0 (audits 07 C3, 09 C2): a legacy translate-content translation is used only on explicit
    # request, and the produced artifacts then carry unverified_legacy=true.
    allow_unverified_legacy: bool = False


LEGACY_TRANSLATION_STAGE = "translate-content"     # TranslateContentWorkflow: no per-segment verification
ACCEPTED_TRANSLATION_STAGE = "translate-book"      # translate-book v2 assembly: accepted units only
TRANSLATION_UNITS_STAGE = "translation-units"
VERIFICATION_FILE = "verification.json"            # written into every new MP3 / track artifact


class UnverifiedLegacySource(ValueError):
    """A legacy (unverified) translation was requested without the explicit opt-in."""


def require_legacy_opt_in(reference: ArtifactRef, inp: GenerateMP3Input, label: str) -> None:
    if reference.stage == LEGACY_TRANSLATION_STAGE and not inp.allow_unverified_legacy:
        raise UnverifiedLegacySource(
            f"{label} is a legacy translation without per-segment verification ({LEGACY_TRANSLATION_STAGE}); "
            "use an accepted translate-book assembly, or pass allow_unverified_legacy=true to use it explicitly")


def accepted_translation_blocks(store: Any, reference: ArtifactRef,
                                course_id: str) -> tuple[ArtifactRef, dict[str, str | None]]:
    """Read a translate-book assembly: (its parsed source ref, block_id -> accepted text).

    Only blocks with ``complete=true`` carry text. A block joined into another one
    (``merged_into``) maps to "" because its text is spoken with its owner; a block that is not
    accepted yet maps to ``None``. Blocks the unit builder never translated are absent.
    """
    if reference.stage != ACCEPTED_TRANSLATION_STAGE:
        raise ValueError("not a translate-book assembly")
    document = json.loads(store.read(reference, "translation.json"))
    units_ref = ArtifactRef.model_validate(document.get("units_ref") or {})
    if units_ref.stage != TRANSLATION_UNITS_STAGE or not units_ref.uri.startswith(f"s3://{store.bucket}/{course_id}/"):
        raise ValueError("translation assembly units are outside the course")
    build = json.loads(store.read(units_ref, "build.json"))
    source_ref = ArtifactRef.model_validate(build.get("source_artifact_ref") or {})
    blocks = document.get("blocks")
    if not isinstance(blocks, dict) or not blocks:
        raise ValueError("translation assembly has no blocks")
    texts: dict[str, str | None] = {}
    for block_id, block in blocks.items():
        owner = block.get("merged_into")
        complete = bool(block.get("complete")) and (owner is None or bool((blocks.get(owner) or {}).get("complete")))
        if not complete:
            texts[block_id] = None
        elif owner is not None:
            texts[block_id] = ""
        else:
            texts[block_id] = str(block.get("text") or "")
    return source_ref, texts


def verification_record(translation_ref: ArtifactRef | None) -> dict[str, Any]:
    """What a produced audio artifact was spoken from; legacy translations stay marked forever."""
    return {"unverified_legacy": translation_ref is not None and translation_ref.stage == LEGACY_TRANSLATION_STAGE,
            "translation_source": translation_ref.stage if translation_ref else None,
            "translation_sha256": translation_ref.sha256 if translation_ref else None}


def is_front_matter_lesson(lesson: Any) -> bool:
    """Exclude publisher/front-matter headings from lesson audio, including OCR-spaced titles."""
    import re
    import unicodedata
    markers = ("preface", "foreword", "afterword", "publishernote", "publisherintroduction",
               "copyright", "dedication", "acknowledgement", "acknowledgment", "tableofcontents")
    for value in (getattr(lesson, "title", ""), getattr(lesson, "lesson_id", "")):
        compact = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", str(value)).casefold())
        if any(marker in compact for marker in markers):
            return True
    title = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", str(getattr(lesson, "title", ""))).casefold())
    return "publisher" in title and any(word in title for word in ("intro", "note", "preface"))


def dominant_script_language(text: str) -> str | None:
    """Detect clear Arabic/Latin script dominance, including short lesson text."""
    import unicodedata
    arabic = latin = 0
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "ARABIC" in name:
            arabic += 1
        elif "LATIN" in name:
            latin += 1
    total = arabic + latin
    if latin >= 20 and latin / max(total, 1) >= 0.90:
        return "en"
    if arabic >= 20 and arabic / max(total, 1) >= 0.50:
        return "ar"
    return None


def qa_source_matches(store: Any, qa: dict[str, Any], qa_ref: ArtifactRef,
                      outline_ref: ArtifactRef, source_ref: ArtifactRef, course_id: str) -> bool:
    if qa.get("approved_outline_sha256") or qa.get("spine_parsed_sha256"):
        return qa.get("approved_outline_sha256") == outline_ref.sha256 and qa.get("spine_parsed_sha256") == source_ref.sha256
    # Older QA has no inline pins. Accept only the exact source-bound, verified
    # migration registry, including complete immutable refs; never use book-main.
    import hashlib
    identity = artifact_source_identity(store, source_ref)
    alias = identity.get("external_book_id")
    if not alias or not identity.get("source_file_sha256"):
        return False
    try:
        record = json.loads(store.s3.get_object(Bucket=store.bucket,
            Key=f"{course_id}/book-progress/{hashlib.sha256(alias.encode()).hexdigest()}.json")["Body"].read())
        stage = record["stages"]["qa"]
        return (record.get("migration_id") == identity.get("source_mapping_id") and bool(record.get("migration_id"))
            and stage.get("source_parsed_sha256") == source_ref.sha256
            and stage.get("source_file_sha256") == identity["source_file_sha256"]
            and ArtifactRef.model_validate(stage["artifact_ref"]) == qa_ref
            and ArtifactRef.model_validate(stage["approved_outline_ref"]) == outline_ref)
    except (KeyError, ValueError, TypeError):
        return False


def validate_mp3_input_lineage(store: Any, inp: GenerateMP3Input) -> None:
    """Reject audio inputs whose immutable artifacts do not share one book lineage."""
    def ref(value: dict[str, Any] | None, label: str) -> ArtifactRef:
        if not value:
            raise ValueError(f"{label} is required")
        result = ArtifactRef.model_validate(value)
        if not result.uri.startswith(f"s3://{store.bucket}/{inp.course_id}/"):
            raise ValueError(f"{label} is outside the course")
        return result

    def json_asset(reference: ArtifactRef, name: str) -> dict[str, Any]:
        return json.loads(store.read(reference, name))

    def translated(reference: ArtifactRef, expected_source: ArtifactRef, language: str,
                   label: str) -> tuple[dict[str, Any], ArtifactRef]:
        if reference.stage != "translate-content":
            raise ValueError(f"{label} must be a translation artifact")
        pack = json_asset(reference, "translation.json")
        segments = pack.get("segments", [])
        missing = pack.get("missing_segment_ids", [])
        failed = pack.get("failed_segment_ids", [])
        total = pack.get("total_segments")
        if (pack.get("status", "completed") != "completed" or missing or failed or
                not isinstance(segments, list) or not segments or
                (total is not None and int(total) != len(segments)) or
                any(not str(item.get("text", "")).strip() for item in segments)):
            raise ValueError(f"{label} is incomplete; audio requires a complete translation")
        source_info = json_asset(reference, "translation-source.json")
        source_ref = ArtifactRef.model_validate(source_info.get("source_artifact_ref", {}))
        if source_ref.sha256 != expected_source.sha256 or source_ref.stage != expected_source.stage:
            raise ValueError(f"{label} was translated from a different source")
        if pack.get("target_language") != language:
            raise ValueError(f"{label} target language does not match audio")
        return pack, source_ref

    def detected_language(text: str) -> str | None:
        script_language = dominant_script_language(text)
        if script_language:
            return script_language
        language, confidence, _ = detect_source_language(text)
        return language if language in {"en", "ar"} and confidence >= 0.85 else None

    def accepted(reference: ArtifactRef, expected_source: ArtifactRef, label: str) -> dict[str, str | None]:
        source_ref, texts = accepted_translation_blocks(store, reference, inp.course_id)
        if source_ref.sha256 != expected_source.sha256 or source_ref.stage != expected_source.stage:
            raise ValueError(f"{label} was translated from a different source")
        pending = sorted(block_id for block_id, text in texts.items() if text is None)
        if pending:
            raise ValueError(f"{label} has {len(pending)} block(s) not accepted yet (first: {pending[0]}); "
                             "audio requires every translated block to be accepted")
        joined = "\n".join(text for text in texts.values() if text)
        if not joined.strip():
            raise ValueError(f"{label} has no accepted text")
        found = detected_language(joined)
        if found is not None and found != inp.language:
            raise ValueError(f"{label} language does not match audio")
        return texts

    source = ref(inp.source_artifact_ref, "audio source")
    # Refuse an unverified legacy translation before reading anything else.
    require_legacy_opt_in(source, inp, "audio source translation")
    if inp.source_book_translation_ref:
        require_legacy_opt_in(ref(inp.source_book_translation_ref, "book translation"), inp, "book translation")
    outline_ref = ref(inp.approved_outline_ref, "approved outline") if inp.approved_outline_ref else None
    source_book_ref = ref(inp.source_book_ref, "source book") if inp.source_book_ref else None

    if inp.source_kind == "book":
        original_ref = source
        pack = None
        if source.stage == ACCEPTED_TRANSLATION_STAGE:
            original_ref, _ = accepted_translation_blocks(store, source, inp.course_id)
            pack = accepted(source, source_book_ref or original_ref, "book translation")
        elif source.stage == "translate-content":
            require_legacy_opt_in(source, inp, "book translation")
            info_ref = json_asset(source, "translation-source.json")
            original_ref = ArtifactRef.model_validate(info_ref.get("source_artifact_ref", {}))
            if original_ref.stage != "ingest-parse-pdf":
                raise ValueError("book translation does not point to parsed-book source")
            pack, _ = translated(source, original_ref, inp.language, "book translation")
        if original_ref.stage != "ingest-parse-pdf":
            raise ValueError("book audio requires parsed text or its translation")
        parsed = ParsedBook.model_validate_json(store.read(original_ref, "parsed-book.json"))
        source_language = detected_language("\n".join(block.text for block in parsed.blocks))
        if source_language and source_language != inp.language and pack is None:
            raise ValueError("book source language differs from audio; a matching translation is required")
        if inp.book_audio_layout == "lessons":
            if outline_ref is None or outline_ref.stage != "approve-outline":
                raise ValueError("lesson audio requires an approved outline")
            if source_book_ref is None or source_book_ref.sha256 != original_ref.sha256:
                raise ValueError("lesson audio book ref differs from the selected source")
            envelope = json_asset(outline_ref, "approved-outline.json")
            spine_ref = ArtifactRef.model_validate(envelope.get("spine_parsed_ref", {}))
            outline = CourseOutline.model_validate(envelope["outline"])
            if spine_ref.sha256 != original_ref.sha256 or outline.spine_book_id != parsed.book_id:
                raise ValueError("approved outline belongs to a different parsed book")
            if inp.lesson_ids is not None:
                all_lesson_ids = {lesson.lesson_id for module in outline.modules for lesson in module.lessons}
                if len(set(inp.lesson_ids)) != len(inp.lesson_ids) or not set(inp.lesson_ids) or not set(inp.lesson_ids) <= all_lesson_ids:
                    raise ValueError("audio lesson selection is duplicated or outside the approved outline")
        return

    if source_book_ref is None or source_book_ref.stage != "ingest-parse-pdf":
        raise ValueError("course audio requires its original parsed book")
    source_book = ParsedBook.model_validate_json(store.read(source_book_ref, "parsed-book.json"))
    if source.stage == "translate-content":
        require_legacy_opt_in(source, inp, "course translation")
        qa_pack, qa_ref = translated(source, ref(inp.source_qa_artifact_ref, "source QA artifact"),
                                     inp.language, "course translation")
        if qa_ref.sha256 != ArtifactRef.model_validate(inp.source_qa_artifact_ref).sha256:
            raise ValueError("course translation does not match the selected QA artifact")
    else:
        qa_ref = ref(inp.source_qa_artifact_ref, "source QA artifact")
        if source.stage != "generate-course-qa" or source.sha256 != qa_ref.sha256:
            raise ValueError("course audio source must be the selected QA artifact or its translation")
        qa_pack = None

    if outline_ref is None or outline_ref.stage != "approve-outline":
        raise ValueError("course audio requires an approved outline")
    envelope = json_asset(outline_ref, "approved-outline.json")
    spine_ref = ArtifactRef.model_validate(envelope.get("spine_parsed_ref", {}))
    outline = CourseOutline.model_validate(envelope["outline"])
    if (spine_ref.sha256 != source_book_ref.sha256 or
            outline.spine_book_id != source_book.book_id):
        raise ValueError("approved outline does not belong to the selected parsed book")

    qa = json_asset(qa_ref, "course-qa.json")
    if ((qa.get("status") != "final" and not inp.allow_partial_qa) or
            not qa_source_matches(store, qa, qa_ref, outline_ref, source_book_ref, inp.course_id)):
        raise ValueError("QA artifact is incomplete or belongs to a different outline/book")
    if inp.lesson_ids is not None:
        allowed = {lesson.lesson_id for module in outline.modules for lesson in module.lessons}
        if not inp.lesson_ids or len(set(inp.lesson_ids)) != len(inp.lesson_ids) or not set(inp.lesson_ids) <= allowed:
            raise ValueError("audio lesson selection is duplicated or outside the approved outline")
    qa_text = "\n".join(
        str(value) for scope in qa.get("scopes", []) for item in scope.get("items", [])
        for value in (item.get("question", ""), item.get("answer", ""))
    )
    qa_language = detected_language(qa_text)
    if qa_language and qa_language != inp.language and qa_pack is None:
        raise ValueError("course QA language differs from audio; a matching QA translation is required")

    book_language = detected_language("\n".join(block.text for block in source_book.blocks))
    book_pack = None
    if inp.source_book_translation_ref:
        book_translation_ref = ref(inp.source_book_translation_ref, "book translation")
        if book_translation_ref.stage == ACCEPTED_TRANSLATION_STAGE:
            book_pack = accepted(book_translation_ref, source_book_ref, "book translation")
        else:
            require_legacy_opt_in(book_translation_ref, inp, "book translation")
            book_pack, _ = translated(book_translation_ref, source_book_ref, inp.language, "book translation")
    if book_language and book_language != inp.language and book_pack is None:
        raise ValueError("lesson source language differs from audio; a matching book translation is required")


def persist_audio_progress_snapshot(store: Any, record: dict[str, Any]) -> None:
    """Retain a previous populated manifest before an activity retry resets it."""
    import hashlib
    key = f"_system/audio-progress/{record['workflow_id']}.json"
    try:
        previous = json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())
    except Exception as exc:
        if getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
            raise
        previous = None
    if previous and len(previous.get("tracks", [])) > len(record.get("tracks", [])):
        payload = json.dumps(previous, ensure_ascii=False, sort_keys=True).encode("utf-8")
        archived_key = f"_system/audio-progress-history/{record['workflow_id']}/{hashlib.sha256(payload).hexdigest()}.json"
        store.s3.put_object(Bucket=store.bucket, Key=archived_key, Body=payload,
                            ContentType="application/json")
        record["previous_snapshot_key"] = archived_key
        record["previous_saved_tracks"] = len(previous["tracks"])
    elif previous and previous.get("previous_snapshot_key"):
        record["previous_snapshot_key"] = previous["previous_snapshot_key"]
        record["previous_saved_tracks"] = previous.get("previous_saved_tracks", 0)
    store.s3.put_object(Bucket=store.bucket, Key=key,
        Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json")


class MP3Activities:
    def __init__(self, artifact_store: Any) -> None: self.store = artifact_store
    def _render(self, text: str, language: str, progress_callback=None) -> bytes:
        body = text.strip()
        if not body:
            raise ValueError("audio track contains no readable text")
        base_url = os.getenv("B2C_AUDIO_RENDERER_URL", "http://audio-renderer:3008").rstrip("/")
        url = base_url + "/v1/render"
        punctuation_route = os.getenv("B2C_SPEECH_PUNCTUATION", "off")
        request_id = hashlib.sha256(json.dumps(
            {"text": body, "language": language, "policy": AUDIO_RENDER_POLICY,
             "punctuation_route": punctuation_route}, ensure_ascii=False, sort_keys=True
        ).encode()).hexdigest()
        payload = {"texts": [body], "language": language,
                   "pipeline_version": AUDIO_RENDER_POLICY, "request_id": request_id}
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                         headers={"Content-Type": "application/json"})
        stop = threading.Event()

        def monitor() -> None:
            if progress_callback is None:
                return
            progress_url = f"{base_url}/v1/render/{request_id}/progress"
            while not stop.wait(10):
                try:
                    with urllib.request.urlopen(progress_url, timeout=5) as response:
                        state = json.loads(response.read())
                    visible = {key: state.get(key) for key in (
                        "status", "segment_count", "completed_segments", "remaining_segments",
                        "failed_segments", "current_segment", "updated_at", "error")}
                    progress_callback(visible)
                except Exception:
                    # Status polling is advisory; it must not cancel a valid render.
                    continue

        watcher = threading.Thread(target=monitor, name="b2c-audio-progress", daemon=True)
        watcher.start()
        try:
            with urllib.request.urlopen(request, timeout=21600) as response:
                mp3 = response.read()
                if response.headers.get_content_type() != "audio/mpeg":
                    raise ValueError("renderer did not return an MP3")
        finally:
            stop.set()
            watcher.join(timeout=2)
        if len(mp3) < 256 or not (mp3.startswith(b"ID3") or mp3[0] == 0xff):
            raise ValueError("renderer returned invalid MP3 bytes")
        return mp3
    @activity.defn(name="generate_mp3")
    def generate(self, inp: GenerateMP3Input) -> dict[str, Any]:
        # One renderer request can block for hours; heartbeat independently.
        with activity_heartbeats("mp3_liveness"):
            return self._generate(inp)

    def _generate(self, inp: GenerateMP3Input) -> dict[str, Any]:
        try:
            workflow_id = activity.info().workflow_id
        except RuntimeError:  # Direct activity tests do not have Temporal context.
            workflow_id = ""
        coverage = {"ready_lesson_ids": [], "pending_lesson_ids": []}
        progress_lock = threading.RLock()
        def save_audio_progress(kind: str, tracks: list[dict[str, Any]], track_count: int | None = None,
                                *, current_track: dict[str, Any] | None = None,
                                renderer_progress: dict[str, Any] | None = None) -> None:
            record = {"workflow_id": workflow_id, "course_id": inp.course_id,
                      "status": "RUNNING", "kind": kind, "language": inp.language,
                      "source_identity": source_identity, "attachment_coverage": coverage,
                      "track_count": track_count if track_count is not None else len(tracks),
                      "tracks": list(tracks), "current_track": current_track,
                      "renderer_progress": renderer_progress, "unverified_legacy": legacy_used,
                      "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
            if not workflow_id or not getattr(self.store, "s3", None):
                return
            try:
                with progress_lock:
                    persist_audio_progress_snapshot(self.store, record)
            except Exception:
                activity.logger.warning("could not persist incremental audio manifest")
        validate_mp3_input_lineage(self.store, inp)
        ref=ArtifactRef.model_validate(inp.source_artifact_ref)
        if inp.source_book_ref:
            identity_ref=ArtifactRef.model_validate(inp.source_book_ref)
        elif ref.stage==ACCEPTED_TRANSLATION_STAGE:
            identity_ref,_=accepted_translation_blocks(self.store,ref,inp.course_id)
        else:
            identity_ref=ref
        source_identity = artifact_source_identity(self.store, identity_ref)
        translation_refs=[r for r in (ref, ArtifactRef.model_validate(inp.source_book_translation_ref) if inp.source_book_translation_ref else None)
                          if r is not None and r.stage in {LEGACY_TRANSLATION_STAGE, ACCEPTED_TRANSLATION_STAGE}]
        # Lineage validation refused a legacy translation unless allow_unverified_legacy was set.
        legacy_used=any(r.stage==LEGACY_TRANSLATION_STAGE for r in translation_refs)
        legacy_key={"unverified_legacy":True} if legacy_used else {}
        def persist_audio(course_id, stage, key, compute, translation_ref=None):
            def with_identity():
                return {**compute(), "source-identity.json": source_identity_file(source_identity),
                        VERIFICATION_FILE: json.dumps(verification_record(translation_ref)).encode()}
            return self.store.get_or_compute(course_id, stage, key, with_identity)
        def mark(manifest):
            return {**manifest, "unverified_legacy": True} if legacy_used else manifest
        if ref.stage not in {"ingest-parse-pdf","generate-course-qa",LEGACY_TRANSLATION_STAGE,ACCEPTED_TRANSLATION_STAGE} or not ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
            raise ApplicationError("source artifact stage or course scope is invalid",type="InvalidMP3Source",non_retryable=True)
        try:
            if inp.source_kind=="book":
                accepted_blocks=None
                book_translation_source=ref if ref.stage in {LEGACY_TRANSLATION_STAGE,ACCEPTED_TRANSLATION_STAGE} else None
                if ref.stage=="ingest-parse-pdf":
                    parsed=ParsedBook.model_validate_json(self.store.read(ref,"parsed-book.json"))
                    pack=None
                elif ref.stage==ACCEPTED_TRANSLATION_STAGE:
                    original_ref,accepted_blocks=accepted_translation_blocks(self.store,ref,inp.course_id)
                    pack=None
                    parsed=None
                    if inp.book_audio_layout=="full_book":
                        parsed=ParsedBook.model_validate_json(self.store.read(original_ref,"parsed-book.json"))
                        text="\n".join(accepted_blocks[x.block_id] for x in parsed.blocks if x.text.strip() and accepted_blocks.get(x.block_id))
                elif ref.stage=="translate-content":
                    pack=json.loads(self.store.read(ref,"translation.json"))
                    if pack.get("target_language")!=inp.language:
                        raise ValueError("translation language mismatch")
                    if inp.book_audio_layout=="full_book":
                        text="\n".join(x["text"] for x in pack.get("segments",[]) if x.get("text","").strip())
                        key=stage_key("generate-mp3","3",[ref.sha256],{"language":inp.language,"kind":"book","render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off"),**legacy_key})
                        manifest=mark({"kind":"book","language":inp.language,"tracks":[{"track_id":"book","kind":"book","title":"Book","filename":"course.mp3"}]})
                        out,hit=persist_audio(inp.course_id,"generate-mp3",key,lambda:{"course.mp3":self._render(text,inp.language),"audio-manifest.json":json.dumps(manifest).encode()},book_translation_source)
                        return {"course_id":inp.course_id,"status":"completed","kind":"book","language":inp.language,"artifact":out.model_dump(mode="json"),"cache_hit":hit,"unverified_legacy":legacy_used}
                    parsed=None
                else:
                    raise ValueError("book audio requires parsed or translated content")
                if inp.book_audio_layout=="full_book":
                    if ref.stage=="ingest-parse-pdf":
                        text="\n".join(x.text for x in parsed.blocks if x.text.strip())
                    key=stage_key("generate-mp3","3",[ref.sha256],{"language":inp.language,"kind":"book","render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off")})
                    manifest={"kind":"book","language":inp.language,"tracks":[{"track_id":"book","kind":"book","title":"Book","filename":"course.mp3"}]}
                    out,hit=persist_audio(inp.course_id,"generate-mp3",key,lambda:{"course.mp3":self._render(text,inp.language),"audio-manifest.json":json.dumps(manifest).encode()},book_translation_source)
                    return {"course_id":inp.course_id,"status":"completed","kind":"book","language":inp.language,"artifact":out.model_dump(mode="json"),"cache_hit":hit,"unverified_legacy":legacy_used}
                source_book_ref=ArtifactRef.model_validate(inp.source_book_ref or {})
                outline_ref=ArtifactRef.model_validate(inp.approved_outline_ref or {})
                if source_book_ref.stage!="ingest-parse-pdf" or not source_book_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                    raise ValueError("lesson-split book audio requires the original parsed book")
                if outline_ref.stage!="approve-outline" or not outline_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                    raise ValueError("lesson-split book audio requires the approved outline")
                parsed=ParsedBook.model_validate_json(self.store.read(source_book_ref,"parsed-book.json"))
                outline=CourseOutline.model_validate(json.loads(self.store.read(outline_ref,"approved-outline.json"))["outline"])
                approved_payload=json.loads(self.store.read(outline_ref,"approved-outline.json"))
                approved_spine=ArtifactRef.model_validate(approved_payload.get("spine_parsed_ref",{}))
                if parsed.book_id != outline.spine_book_id or approved_spine.sha256 != source_book_ref.sha256:
                    raise ValueError("parsed book does not match the approved outline source")
                translated={str(x.get("segment_id")):str(x.get("text","")) for x in (pack or {}).get("segments",[])}
                blocks=parsed.blocks
                positions={block.block_id:i for i,block in enumerate(blocks)}
                tracks=[]
                selected_lesson_ids = set(inp.lesson_ids or [])
                all_lessons = [lesson for module in outline.modules for lesson in module.lessons if not is_front_matter_lesson(lesson)]
                planned_track_count = len(selected_lesson_ids) if selected_lesson_ids else len(all_lessons)
                save_audio_progress("book_lessons", tracks, planned_track_count)
                for module in outline.modules:
                    for lesson in module.lessons:
                        if selected_lesson_ids and lesson.lesson_id not in selected_lesson_ids:
                            continue
                        span=lesson.spine_span
                        if span.start_block not in positions or span.end_block not in positions or positions[span.start_block]>positions[span.end_block]:
                            raise ValueError("lesson span is incompatible with parsed book")
                        lesson_blocks=blocks[positions[span.start_block]:positions[span.end_block]+1]
                        pieces=[]
                        for block in lesson_blocks:
                            if not block.text.strip():
                                continue
                            if accepted_blocks is not None:
                                # translate-book: only accepted (complete) blocks are spoken.
                                value=accepted_blocks.get(block.block_id)
                                if value is None and block.block_id in accepted_blocks:
                                    raise ValueError("translated lesson block is not accepted: "+block.block_id)
                                if not value:
                                    continue
                            else:
                                value=translated.get(block.block_id) if pack else block.text
                                if pack and not value:
                                    raise ValueError("translated lesson segment missing: "+block.block_id)
                            pieces.append(value)
                        if not pieces:
                            continue
                        track_id=lesson.lesson_id+"-book"
                        config={"language":inp.language,"kind":"book_lesson","track_id":track_id,"model":"fasih-tts-v1" if inp.language=="ar" else "supertonic3","render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off")}
                        track_key=stage_key("generate-mp3-track","2",[source_book_ref.sha256,outline_ref.sha256,ref.sha256],config)
                        track_ref,hit=persist_audio(inp.course_id,"generate-mp3-track",track_key,lambda body="\n".join(pieces),cfg=config:{"track.mp3":self._render(body,inp.language),"track.json":json.dumps(cfg).encode(),"track.txt":body.encode("utf-8")},book_translation_source)
                        tracks.append({"track_id":track_id,"kind":"book_lesson","lesson_id":lesson.lesson_id,"title":lesson.title,"language":inp.language,"filename":track_id+".mp3","artifact_ref":track_ref.model_dump(mode="json"),"cache_hit":hit,
                                       "unverified_legacy":verification_record(book_translation_source)["unverified_legacy"]})
                        save_audio_progress("book_lessons", tracks, planned_track_count)
                        wait_for_activity_checkpoint(self.store, "audio-track:" + track_id)
                if not tracks:
                    raise ValueError("approved outline produced no readable lesson tracks")
                manifest=mark({"course_id":inp.course_id,"kind":"book_lessons","language":inp.language,"track_count":len(tracks),"tracks":tracks})
                key=stage_key("generate-mp3","4",[source_book_ref.sha256,outline_ref.sha256,ref.sha256],{"language":inp.language,"kind":"book_lessons","lesson_ids":sorted(selected_lesson_ids),"render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off"),**legacy_key})
                out,hit=persist_audio(inp.course_id,"generate-mp3",key,lambda:{"audio-manifest.json":json.dumps(manifest,ensure_ascii=False,indent=2).encode()},book_translation_source)
                return {"course_id":inp.course_id,"status":"completed","kind":"book_lessons","language":inp.language,"artifact":out.model_dump(mode="json"),"manifest":manifest,"track_count":len(tracks),"cache_hit":hit,"unverified_legacy":legacy_used}
            if ref.stage not in {"generate-course-qa","translate-content"} or inp.approved_outline_ref is None:
                raise ValueError("course audio requires QA and approved outline")
            outline_ref=ArtifactRef.model_validate(inp.approved_outline_ref)
            if outline_ref.stage!="approve-outline" or not outline_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                raise ValueError("approved outline scope invalid")
            outline=CourseOutline.model_validate(json.loads(self.store.read(outline_ref,"approved-outline.json"))["outline"])
            source_book_ref=ArtifactRef.model_validate(inp.source_book_ref or {})
            if source_book_ref.stage!="ingest-parse-pdf" or not source_book_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                raise ValueError("course lesson audio requires the original parsed spine book")
            source_book=ParsedBook.model_validate_json(self.store.read(source_book_ref,"parsed-book.json"))
            approved_payload=json.loads(self.store.read(outline_ref,"approved-outline.json"))
            approved_spine=ArtifactRef.model_validate(approved_payload.get("spine_parsed_ref",{}))
            if source_book.book_id != outline.spine_book_id or approved_spine.sha256 != source_book_ref.sha256:
                raise ValueError("audio source book does not match the approved outline source")
            book_pack=None
            book_translation={}
            book_translation_ref=None
            accepted_book=None
            if inp.source_book_translation_ref:
                book_translation_ref=ArtifactRef.model_validate(inp.source_book_translation_ref)
                if book_translation_ref.stage not in {LEGACY_TRANSLATION_STAGE,ACCEPTED_TRANSLATION_STAGE} or not book_translation_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                    raise ValueError("translated lesson source is outside the course")
            if book_translation_ref is not None and book_translation_ref.stage==ACCEPTED_TRANSLATION_STAGE:
                translated_source,accepted_book=accepted_translation_blocks(self.store,book_translation_ref,inp.course_id)
                if translated_source.sha256!=source_book_ref.sha256:
                    raise ValueError("translated lesson source does not match the original book")
            elif book_translation_ref is not None:
                book_pack=json.loads(self.store.read(book_translation_ref,"translation.json"))
                source_info=json.loads(self.store.read(book_translation_ref,"translation-source.json"))
                translated_source=ArtifactRef.model_validate(source_info["source_artifact_ref"])
                if translated_source.sha256!=source_book_ref.sha256 or book_pack.get("target_language")!=inp.language:
                    raise ValueError("translated lesson source does not match the original book or requested language")
                book_translation={str(x.get("segment_id")):str(x.get("text","")) for x in book_pack.get("segments",[])}
            qa_ref,pack=ref,None
            if ref.stage=="translate-content":
                pack=json.loads(self.store.read(ref,"translation.json"))
                if pack.get("target_language")!=inp.language:
                    raise ValueError("translation language mismatch")
                source_info=json.loads(self.store.read(ref,"translation-source.json"))
                qa_ref=ArtifactRef.model_validate(source_info["source_artifact_ref"])
                if inp.source_qa_artifact_ref and ArtifactRef.model_validate(inp.source_qa_artifact_ref).sha256 != qa_ref.sha256:
                    raise ValueError("translation QA source mismatch")
                if qa_ref.stage!="generate-course-qa" or not qa_ref.uri.startswith(f"s3://{self.store.bucket}/{inp.course_id}/"):
                    raise ValueError("translation source scope invalid")
            qa=json.loads(self.store.read(qa_ref,"course-qa.json"))
            if ((qa.get("status") != "final" and not inp.allow_partial_qa) or
                    not qa_source_matches(self.store, qa, qa_ref, outline_ref, source_book_ref, inp.course_id)):
                raise ValueError("QA artifact is incomplete or belongs to another source")
            tr={str(x.get("segment_id")):str(x.get("text","")) for x in (pack or {}).get("segments",[])}
            def val(sid,original):
                value=tr.get(sid,"") if pack else original
                if pack and not value:
                    raise ValueError("translation missing: "+sid)
                return value
            assets={str(x.get("lesson_id")):x for x in qa.get("lesson_assets",[])}
            scopes={str(x.get("scope_id")):x for x in qa.get("scopes",[])}
            tracks=[]
            selected = set(inp.lesson_ids or [])
            course_lessons = [lesson for module in outline.modules for lesson in module.lessons if not is_front_matter_lesson(lesson) and (not selected or lesson.lesson_id in selected)]
            for lesson in course_lessons:
                scope = scopes.get(lesson.lesson_id, {})
                ready = bool(scope.get("items")) and (scope.get("status") == "final" or (qa.get("status") == "final" and not inp.allow_partial_qa)) and all(item.get("status", "final") == "final" and all(item.get("automated_checks", {}).values()) for item in scope.get("items", []))
                coverage["ready_lesson_ids" if ready else "pending_lesson_ids"].append(lesson.lesson_id)
            planned = len(course_lessons) + len(coverage["ready_lesson_ids"])

            save_audio_progress("course", tracks, planned)
            def add(tid,kind,title,body,lesson_id=None,source_hashes=None,translation_ref=None):
                if dominant_script_language(body) not in {None, inp.language}:
                    raise ValueError("audio track language mismatch: " + tid)
                cfg={"language":inp.language,"kind":kind,"track_id":tid,"model":"fasih-tts-v1" if inp.language=="ar" else "supertonic3","render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off")}
                if kind == "lesson_attachment":
                    cfg["include_summaries"] = inp.include_summaries
                hashes=list(source_hashes or ([qa_ref.sha256,outline_ref.sha256]+([ref.sha256] if pack else [])))
                key=stage_key("generate-mp3-track","2",hashes,cfg)
                current_track={"track_id":tid,"kind":kind,"lesson_id":lesson_id,"title":title,
                               "status":"rendering","completed_segments":0,"remaining_segments":None,"failed_segments":0}
                renderer_state={}
                save_audio_progress("course",tracks,planned,current_track=current_track,renderer_progress=None)
                def report_renderer_progress(state):
                    renderer_state.update(state)
                    current_track.update({"completed_segments":int(state.get("completed_segments") or 0),
                                          "remaining_segments":state.get("remaining_segments"),
                                          "failed_segments":int(state.get("failed_segments") or 0)})
                    save_audio_progress("course",tracks,planned,current_track=current_track.copy(),renderer_progress=renderer_state.copy())
                try:
                    track_ref,hit=persist_audio(inp.course_id,"generate-mp3-track",key,lambda:{"track.mp3":self._render(body,inp.language,report_renderer_progress),"track.json":json.dumps(cfg).encode(),"track.txt":body.encode("utf-8")},translation_ref)
                except Exception as exc:
                    current_track.update(status="failed",error_type=type(exc).__name__)
                    save_audio_progress("course",tracks,planned,current_track=current_track.copy(),renderer_progress=renderer_state.copy() or None)
                    raise
                current_track["status"]="completed"
                tracks.append({"track_id":tid,"kind":kind,"lesson_id":lesson_id,"title":title,"language":inp.language,"filename":tid+".mp3","artifact_ref":track_ref.model_dump(mode="json"),"bytes":len(self.store.read(track_ref,"track.mp3")),"cache_hit":hit,
                               "unverified_legacy":verification_record(translation_ref)["unverified_legacy"]})
                save_audio_progress("course",tracks,planned,current_track=None,renderer_progress=None)
                wait_for_activity_checkpoint(self.store, "audio-track:" + tid)
            source_blocks=source_book.blocks
            source_positions={block.block_id:i for i,block in enumerate(source_blocks)}
            for module in outline.modules:
                for lesson in module.lessons:
                    if is_front_matter_lesson(lesson):
                        continue
                    if selected and lesson.lesson_id not in selected:
                        continue
                    span=lesson.spine_span
                    if span.start_block not in source_positions or span.end_block not in source_positions or source_positions[span.start_block]>source_positions[span.end_block]:
                        raise ValueError("lesson span is incompatible with the original OCR book")
                    lesson_blocks=source_blocks[source_positions[span.start_block]:source_positions[span.end_block]+1]
                    source_parts=[]
                    for block in lesson_blocks:
                        if not block.text.strip():
                            continue
                        if accepted_book is not None:
                            # translate-book: only accepted (complete) blocks are spoken.
                            text=accepted_book.get(block.block_id)
                            if text is None and block.block_id in accepted_book:
                                raise ValueError("translated source block is not accepted: "+block.block_id)
                            if not text:
                                continue
                        else:
                            text=book_translation.get(block.block_id,"") if book_pack else block.text
                            if book_pack and not text.strip():
                                raise ValueError("translated source text missing: "+block.block_id)
                        source_parts.append(text)
                    if source_parts:
                        source_track_id=lesson.lesson_id+"-source-text"
                        source_hashes=[source_book_ref.sha256,outline_ref.sha256]
                        if book_translation_ref:
                            source_hashes.append(book_translation_ref.sha256)
                        add(source_track_id,"lesson_source_text",lesson.title,"\n".join(source_parts),lesson.lesson_id,source_hashes,book_translation_ref)
                    if inp.allow_partial_qa and lesson.lesson_id not in coverage["ready_lesson_ids"]:
                        continue
                    asset=assets.get(lesson.lesson_id,{}) if inp.include_summaries else {}
                    lines=[val(lesson.lesson_id+":summary:"+str(i),str(point))
                           for i,point in enumerate(asset.get("summary",{}).get("points",[]) if inp.include_summaries else [],1)]
                    for i,q in enumerate(scopes.get(lesson.lesson_id,{}).get("items",[]),1):
                        lines += ["سؤال: "+val(lesson.lesson_id+":lesson-qa:"+str(i)+":question",str(q.get("question",""))),
                                  "الإجابة: "+val(lesson.lesson_id+":lesson-qa:"+str(i)+":answer",str(q.get("answer","")))]
                    for i,q in enumerate(asset.get("questions",[]),1):
                        lines.append("سؤال: "+val(lesson.lesson_id+":question:"+str(i)+":stem",str(q.get("stem",""))))
                        options=q.get("options",[])
                        lines += [val(lesson.lesson_id+":question:"+str(i)+":option:"+str(n),str(option))
                                  for n,option in enumerate(options,1)]
                        answer_index=int(q.get("answer_index",0))
                        if options and 0<=answer_index<len(options):
                            lines.append("الإجابة: "+val(lesson.lesson_id+":question:"+str(i)+":answer",str(options[answer_index])))
                        if q.get("rationale"):
                            lines.append("Explanation: "+val(lesson.lesson_id+":question:"+str(i)+":rationale",str(q["rationale"])))
                    if lines:
                        add(lesson.lesson_id+"-attachment","lesson_attachment",lesson.title+(" — الملخص والأسئلة والإجابات" if inp.include_summaries else " — الأسئلة والإجابات"),
                            "\n".join(lines),lesson.lesson_id,None,ref if pack else None)
            for scope in qa.get("scopes",[]) if inp.include_extra_scopes else []:
                if scope.get("kind")=="lesson":
                    continue
                if inp.allow_partial_qa and scope.get("status") != "final":
                    continue
                sid=str(scope.get("scope_id","scope"))
                lines=[]
                for i,q in enumerate(scope.get("items",[]),1):
                    lines += ["سؤال: "+val("scope:"+sid+":qa:"+str(i)+":question",str(q.get("question",""))),"الإجابة: "+val("scope:"+sid+":qa:"+str(i)+":answer",str(q.get("answer","")))]
                if lines:
                    add(str(scope.get("kind"))+"-"+sid+"-questions",str(scope.get("kind"))+"_questions",str(scope.get("scope_title",sid)),"\n".join(lines),None,None,ref if pack else None)
            if not tracks:
                raise ValueError("course artifact produced no audio tracks")
            manifest=mark({"course_id":inp.course_id,"kind":"course","language":inp.language,"track_count":len(tracks),"tracks":tracks,"attachment_coverage":coverage,"qa_status":qa.get("status"),"include_summaries":inp.include_summaries})
            hashes=[qa_ref.sha256,outline_ref.sha256,source_book_ref.sha256]+([ref.sha256] if pack else [])+([book_translation_ref.sha256] if book_translation_ref else [])
            key=stage_key("generate-mp3","3",hashes,{"language":inp.language,"kind":"course","lesson_ids":inp.lesson_ids,"allow_partial_qa":inp.allow_partial_qa,"include_summaries":inp.include_summaries,"include_extra_scopes":inp.include_extra_scopes,"render_policy":AUDIO_RENDER_POLICY,"punctuation_route":os.getenv("B2C_SPEECH_PUNCTUATION","off"),**legacy_key})
            manifest_translation=next((r for r in translation_refs if r.stage==LEGACY_TRANSLATION_STAGE),translation_refs[0] if translation_refs else None)
            out,hit=persist_audio(inp.course_id,"generate-mp3",key,lambda:{"audio-manifest.json":json.dumps(manifest,ensure_ascii=False,indent=2).encode()},manifest_translation)
            return {"course_id":inp.course_id,"status":"completed","kind":"course","language":inp.language,"artifact":out.model_dump(mode="json"),"manifest":manifest,"track_count":len(tracks),"cache_hit":hit,"unverified_legacy":legacy_used}
        except ApplicationError:
            raise
        except HTTPError as exc:
            retryable = renderer_status_retryable(exc.code)
            raise ApplicationError(f"audio renderer returned HTTP {exc.code}",
                                   type="AudioRendererHTTPError",
                                   non_retryable=not retryable) from None
        except Exception as exc:
            # Keep Temporal failure payloads small; giant HTTP/runtime causes made
            # retries fail with "Failure exceeds size limit" and hid the real issue.
            detail = " ".join(str(exc).split())[:240]
            permanent = isinstance(exc, (ValueError, KeyError, TypeError))
            message = f"MP3 generation failed ({type(exc).__name__})"
            if detail:
                message += f": {detail}"
            raise ApplicationError(message, type="MP3GenerationFailed",
                                   non_retryable=permanent) from None
MP3_RETRY = RetryPolicy(initial_interval=timedelta(seconds=30), backoff_coefficient=2.0,
                        maximum_interval=timedelta(minutes=10), maximum_attempts=3)


@workflow.defn
class GenerateMP3Workflow:
    @workflow.signal(name="resume_stage")
    def resume_stage(self) -> None:
        self._stage_resume_requested = True

    @workflow.run
    async def run(self,inp:GenerateMP3Input)->dict[str,Any]:
        options: dict[str, Any] = {}
        # Pre-existing histories scheduled MP3 generation without a heartbeat timeout.
        if workflow.patched("mp3-heartbeat-timeout-v1"):
            options["heartbeat_timeout"] = LONG_ACTIVITY_HEARTBEAT_TIMEOUT
        # Pre-existing histories used the server default (unlimited attempts).
        # Each attempt can render for hours; the renderer reuses completed WAVs.
        if workflow.patched("mp3-bounded-retry-v1"):
            options["retry_policy"] = MP3_RETRY
        return await execute_checkpointed(self, "generate_mp3", inp, task_queue=inp.activity_task_queue,
                                          start_to_close_timeout=timedelta(hours=24), **options)

