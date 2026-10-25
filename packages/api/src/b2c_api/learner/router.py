"""Learner API v3 routes: ``/v3/learner/...``.

Read-only. Every artifact is looked up in the stage-output registry and read only after
``deps.require_accepted_artifact`` has cleared exactly that artifact (stage, key, uri, sha256);
candidates, rejected and quarantined outputs are invisible. Where an episode script or audio is
not accepted yet, the routes answer with a clean empty state (``status="script_pending"``,
``audio.available=false``), never with a candidate.

Answers: no GET response carries an answer (``schemas``). ``POST .../reveal`` returns one answer
when the learner asks for it; it changes no server state (a POST so answers never sit in GET
caches or logs of GET URLs). Auth: the app-wide bearer token (D-030).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from b2c_api.deps import is_not_found, log, require_accepted_artifact, require_course_id
from b2c_api.learner import mapping
from b2c_api.learner.schemas import (
    ConceptList,
    CourseDetail,
    CourseList,
    CourseSummary,
    EpisodeAudio,
    EpisodeDetail,
    EpisodeSummary,
    LearnerMeta,
    RevealRequest,
    RevealResponse,
)
from b2c_api.serving import parse_single_byte_range

PREFIX = "/v3/learner"
QUESTION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,119}")
MAX_RECORDS = 1000


@dataclass
class _Course:
    course_id: str
    title: str
    language: str
    updated_at: str
    episodes: list[mapping.EpisodeRef]


def _json_response(request: Request, model: Any) -> Response:
    """JSON with a strong ETag; a matching If-None-Match gets 304 (cheap revalidation for the app)."""
    body = json.dumps(model.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def learner_router(ctx: Any) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["learner"])

    async def registry_call(fn, *args, **kwargs):
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except Exception as exc:
            log.exception("stage-output registry is unavailable")
            raise HTTPException(status_code=503, detail="content status is unavailable") from exc

    async def accepted_records(course_id: str | None, stage: str) -> list[Any]:
        return await registry_call(ctx.get_stage_registry().list, course_id=course_id, stage=stage,
                                   status="accepted", limit=MAX_RECORDS)

    async def read_json(course_id: str, record: Any, name: str, *, optional: bool = False) -> Any:
        await require_accepted_artifact(ctx.get_stage_registry(), course_id, record.artifact)
        try:
            return await run_in_threadpool(ctx.get_store().read_json, record.artifact, name)
        except Exception as exc:
            if optional and is_not_found(exc):
                return None
            log.exception("accepted learner artifact could not be read")
            raise HTTPException(status_code=503, detail="content is unavailable") from exc

    async def load_course(course_id: str) -> _Course:
        require_course_id(course_id)
        records = await accepted_records(course_id, mapping.OUTLINE_STAGE)
        if not records:
            raise HTTPException(status_code=404, detail="course not found")
        record = records[0]  # newest accepted outline
        raw = await read_json(course_id, record, mapping.OUTLINE_FILE)
        try:
            episodes = mapping.episodes_from_outline(raw)
        except ValueError as exc:
            log.exception("accepted outline does not match its contract")
            raise HTTPException(status_code=503, detail="course content is unavailable") from exc
        return _Course(course_id, mapping.course_title(course_id, record.config), str(raw.get("language") or "en"),
                       record.artifact.created_at.isoformat(), episodes)

    def find_episode(course: _Course, number: int) -> mapping.EpisodeRef:
        for episode in course.episodes:
            if episode.number == number:
                return episode
        raise HTTPException(status_code=404, detail="episode not found")

    async def by_episode(course_id: str, stage: str, file_name: str) -> dict[int, Any]:
        """Newest accepted artifact of ``stage`` per episode number."""
        found: dict[int, Any] = {}
        for record in await accepted_records(course_id, stage):
            number = mapping.episode_number_of(record.config)
            if number is None and file_name:
                number = mapping.episode_number_of(None, await read_json(course_id, record, file_name))
            if number is not None and number not in found:
                found[number] = record
        return found

    @router.get("/meta", response_model=LearnerMeta)
    async def learner_meta(request: Request) -> Response:
        return _json_response(request, LearnerMeta(mapping_version=mapping.MAPPING_VERSION, sources={
            "episodes": mapping.OUTLINE_STAGE, "episode_script": mapping.SCRIPT_STAGE,
            "episode_audio": mapping.AUDIO_STAGE, "concepts": mapping.CONCEPT_STAGE}))

    @router.get("/courses", response_model=CourseList)
    async def learner_courses(request: Request) -> Response:
        newest: dict[str, Any] = {}
        for record in await accepted_records(None, mapping.OUTLINE_STAGE):
            newest.setdefault(record.course_id, record)
        courses = []
        for course_id in sorted(newest):
            course = await load_course(course_id)
            courses.append(CourseSummary(course_id=course.course_id, title=course.title, language=course.language,
                                         episode_count=len(course.episodes), updated_at=course.updated_at))
        return _json_response(request, CourseList(courses=courses))

    @router.get("/courses/{course_id}", response_model=CourseDetail)
    async def learner_course(course_id: str, request: Request) -> Response:
        course = await load_course(course_id)
        scripts = await by_episode(course_id, mapping.SCRIPT_STAGE, mapping.SCRIPT_FILE)
        audio = await by_episode(course_id, mapping.AUDIO_STAGE, "")
        concepts = await accepted_records(course_id, mapping.CONCEPT_STAGE)
        episodes = [EpisodeSummary(
            number=e.number, label=e.label, title=e.title, kind=e.kind, lesson_id=e.lesson_id,
            script_available=e.number in scripts, audio_available=e.number in audio,
            duration_s=mapping.number_or_none((audio[e.number].config or {}).get("duration_s")) if e.number in audio else None)
            for e in course.episodes]
        return _json_response(request, CourseDetail(
            course_id=course_id, title=course.title, language=course.language, episodes=episodes,
            concepts_available=bool(concepts), updated_at=course.updated_at))

    async def episode_content(course_id: str, number: int):
        course = await load_course(course_id)
        episode = find_episode(course, number)
        scripts = await by_episode(course_id, mapping.SCRIPT_STAGE, mapping.SCRIPT_FILE)
        audio_records = await by_episode(course_id, mapping.AUDIO_STAGE, "")
        script = None
        if number in scripts:
            raw = await read_json(course_id, scripts[number], mapping.SCRIPT_FILE)
            script = mapping.parse_script(raw if isinstance(raw, dict) else {})
        return course, episode, script, audio_records.get(number)

    @router.get("/courses/{course_id}/episodes/{number}", response_model=EpisodeDetail)
    async def learner_episode(course_id: str, number: int, request: Request) -> Response:
        course, episode, script, audio_record = await episode_content(course_id, number)
        duration = script.duration_s if script else None
        audio = EpisodeAudio(available=False)
        if audio_record is not None:
            timings = await read_json(course_id, audio_record, mapping.TIMINGS_FILE, optional=True)
            if script is not None:
                duration = mapping.apply_timings(script, timings if isinstance(timings, dict) else None)
            config = audio_record.config or {}
            duration = mapping.number_or_none(config.get("duration_s")) or duration
            audio = EpisodeAudio(available=True, path=f"{PREFIX}/courses/{course_id}/episodes/{number}/audio",
                                 duration_s=duration, size_bytes=config.get("size_bytes")
                                 if isinstance(config.get("size_bytes"), int) else None)
        return _json_response(request, EpisodeDetail(
            course_id=course_id, number=episode.number, label=episode.label,
            title=(script.title if script and script.title else episode.title), kind=episode.kind,
            lesson_id=episode.lesson_id, status="ready" if script else "script_pending",
            language=script.language if script else "ar", duration_s=duration, audio=audio,
            sections=script.sections if script else [], key_terms=script.key_terms if script else [],
            questions=script.questions if script else []))

    @router.post("/courses/{course_id}/episodes/{number}/questions/{question_id}/reveal",
                 response_model=RevealResponse)
    async def learner_reveal(course_id: str, number: int, question_id: str, body: RevealRequest) -> RevealResponse:
        if not QUESTION_ID.fullmatch(question_id):
            raise HTTPException(status_code=404, detail="question not found")
        _course, _episode, script, _audio = await episode_content(course_id, number)
        hidden = script.answers.get(question_id) if script else None
        if hidden is None:
            raise HTTPException(status_code=404, detail="question not found")
        return RevealResponse(question_id=question_id, answer=hidden.answer,
                              correct=mapping.check_answer(hidden, body.selected_option),
                              answer_segment_id=hidden.answer_segment_id, answer_section_id=hidden.answer_section_id)

    @router.get("/courses/{course_id}/episodes/{number}/audio",
                responses={200: {"content": {"audio/mpeg": {}}}, 206: {"content": {"audio/mpeg": {}}},
                           404: {"description": "no accepted audio for this episode yet"}})
    async def learner_episode_audio(course_id: str, number: int, request: Request):
        course = await load_course(course_id)
        find_episode(course, number)
        record = (await by_episode(course_id, mapping.AUDIO_STAGE, "")).get(number)
        if record is None:
            raise HTTPException(status_code=404, detail="episode audio is not available yet")
        await require_accepted_artifact(ctx.get_stage_registry(), course_id, record.artifact)
        store = ctx.get_store()
        bucket = store.bucket
        key = record.artifact.uri.removeprefix(f"s3://{bucket}/") + mapping.AUDIO_FILE
        try:
            head = await run_in_threadpool(lambda: store.s3.head_object(Bucket=bucket, Key=key))
        except Exception as exc:
            log.exception("accepted episode audio could not be inspected")
            raise HTTPException(status_code=503, detail="episode audio is unavailable") from exc
        size = int(head["ContentLength"])
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(size), "Cache-Control": "private, max-age=86400"}
        if head.get("ETag"):
            headers["ETag"] = head["ETag"]
        args: dict[str, Any] = {"Bucket": bucket, "Key": key}
        status = 200
        if range_header := request.headers.get("range"):
            try:
                start, end = parse_single_byte_range(range_header, size)
            except ValueError as exc:
                raise HTTPException(status_code=416, detail="requested byte range is not satisfiable",
                                    headers={"Content-Range": f"bytes */{size}"}) from exc
            args["Range"] = f"bytes={start}-{end}"
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
        try:
            result = await run_in_threadpool(lambda: store.s3.get_object(**args))
        except Exception as exc:
            log.exception("accepted episode audio could not be read")
            raise HTTPException(status_code=503, detail="episode audio is unavailable") from exc
        body = result["Body"]

        def chunks():
            try:
                while data := body.read(64 * 1024):
                    yield data
            finally:
                body.close()
        return StreamingResponse(chunks(), status_code=status, media_type="audio/mpeg", headers=headers,
                                 background=BackgroundTask(body.close))

    @router.get("/courses/{course_id}/concepts", response_model=ConceptList)
    async def learner_concepts(course_id: str, request: Request) -> Response:
        course = await load_course(course_id)
        records = await accepted_records(course_id, mapping.CONCEPT_STAGE)
        if not records:
            return _json_response(request, ConceptList(course_id=course_id, language="en", status="pending",
                                                       concepts=[]))
        raw = await read_json(course_id, records[0], mapping.CONCEPT_FILE)
        try:
            language, cards = mapping.concept_cards(raw, course.episodes)
        except ValueError as exc:
            log.exception("accepted concept layer does not match its contract")
            raise HTTPException(status_code=503, detail="concepts are unavailable") from exc
        return _json_response(request, ConceptList(course_id=course_id, language=language, status="ready",
                                                   concepts=cards))

    return router
