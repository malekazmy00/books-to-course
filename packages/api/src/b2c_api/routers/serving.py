"""Learner-facing released courses, lessons, audio streaming and SRS reviews."""
from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from b2c_contracts import (
    Question,
)
from b2c_core import ArtifactStore
from b2c_pedagogy.srs import build_review_quiz

from b2c_api.serving import S3CourseServingCatalog, learner_question, parse_single_byte_range
from b2c_api.srs import PostgresSRSStore
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from fastapi import APIRouter
from b2c_api.deps import ApiContext, log
from b2c_api.schemas import ReviewSubmission


def serving_router(ctx: ApiContext, router: Any = None) -> Any:
    """Register the routes on ``router`` (the app itself in ``create_app``,
    keeping the route table flat and in its original order)."""
    router = APIRouter() if router is None else router
    app = ctx.app
    settings = ctx.settings

    def get_serving_catalog() -> Any:
        if app.state.serving_catalog is None:
            store = app.state.artifact_store
            if store is None:
                store = ArtifactStore.from_settings(settings)
                app.state.artifact_store = store
            app.state.serving_catalog = S3CourseServingCatalog(store, settings.s3_bucket)
        return app.state.serving_catalog

    def get_srs_store() -> Any:
        if app.state.srs_store is None:
            app.state.srs_store = PostgresSRSStore(settings.pg_dsn)
        return app.state.srs_store

    async def require_learner_id(request: Request) -> str:
        authenticator = app.state.learner_authenticator
        if authenticator is None:
            raise HTTPException(status_code=503, detail="learner authentication is not configured")
        principal = authenticator(request)
        if inspect.isawaitable(principal):
            principal = await principal
        if not isinstance(principal, str) or not principal or len(principal) > 120:
            raise HTTPException(status_code=401, detail="authenticated learner identity is missing")
        return principal

    def load_released_course(course_id: str):
        try:
            catalog = get_serving_catalog()
            return catalog.load(course_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="released course not found") from exc
        except HTTPException:
            raise
        except Exception as exc:
            log.exception("failed to load released course")
            raise HTTPException(status_code=503, detail="released course artifacts are unavailable") from exc

    def lesson_in_course(course: Any, lesson_id: str):
        for module in course.outline.modules:
            for lesson in module.lessons:
                if lesson.lesson_id == lesson_id:
                    return lesson
        raise HTTPException(status_code=404, detail="released lesson not found")

    def lesson_questions(course: Any) -> dict[str, Question]:
        questions: dict[str, Question] = {}
        for asset in course.qa.lesson_assets:
            for item in asset.get("questions", []):
                question = Question.model_validate(item)
                if question.qid in questions:
                    raise HTTPException(status_code=503, detail="released lesson has duplicate question IDs")
                questions[question.qid] = question
        return questions

    # GET /v1/courses belongs to the operator course library (Android client);
    # the learner release catalog has its own path so neither shadows the other.
    @router.get("/v1/released-courses")
    async def list_released_courses() -> dict[str, Any]:
        try:
            catalog = get_serving_catalog()
            course_ids = await run_in_threadpool(catalog.released_course_ids)
            courses = []
            for course_id in course_ids:
                course = await run_in_threadpool(catalog.load, course_id)
                courses.append({
                    "course_id": course_id,
                    "release_id": course.release.release_id,
                    "lesson_count": len(course.release.lesson_versions),
                    "created_at": course.release.created_at.isoformat(),
                })
            return {"courses": courses}
        except Exception as exc:
            log.exception("failed to list released courses")
            raise HTTPException(status_code=503, detail="released course catalog is unavailable") from exc

    @router.get("/v1/courses/{course_id}")
    async def read_released_course(course_id: str) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        return {
            "course_id": course.release.course_id,
            "release": course.release.model_dump(mode="json"),
            "outline": course.outline.model_dump(mode="json"),
            "course_qa": course.qa.model_dump(mode="json"),
            "audio_lesson_ids": sorted(course.audio_assets),
        }

    @router.get("/v1/lessons/{lesson_id}")
    async def read_released_lesson(lesson_id: str, course_id: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson = lesson_in_course(course, lesson_id)
        asset = next((item for item in course.qa.lesson_assets if item.get("lesson_id") == lesson_id), None)
        scope = next((item for item in course.qa.scopes if item.scope_id == lesson_id), None)
        return {
            "course_id": course_id,
            "lesson": lesson.model_dump(mode="json"),
            "lesson_assets": asset,
            "grounded_questions": [item.model_dump(mode="json") for item in scope.items] if scope else [],
        }

    @router.get("/v1/lessons/{lesson_id}/review-quiz")
    async def read_review_quiz(
        lesson_id: str,
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=10, ge=1, le=50),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        bank = list(lesson_questions(course).values())
        try:
            questions = build_review_quiz(course.outline, lesson_id, bank, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="released lesson not found") from exc
        return {"course_id": course_id, "lesson_id": lesson_id,
                "questions": [learner_question(question) for question in questions]}

    @router.get("/v1/lessons/{lesson_id}/segments")
    async def read_lesson_segments(lesson_id: str, course_id: str = Query(min_length=1, max_length=80)) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        asset = course.audio_assets.get(lesson_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="released lesson audio is unavailable")
        return {"course_id": course_id, "lesson_id": lesson_id, "segments": asset.segments}

    @router.get("/v1/lessons/{lesson_id}/audio")
    async def stream_lesson_audio(
        lesson_id: str,
        request: Request,
        course_id: str = Query(min_length=1, max_length=80),
    ):
        course = await run_in_threadpool(load_released_course, course_id)
        lesson_in_course(course, lesson_id)
        try:
            bucket, key = get_serving_catalog().mp3_location(course, lesson_id)
            head = await run_in_threadpool(
                lambda: get_serving_catalog().s3.head_object(Bucket=bucket, Key=key)
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="released lesson audio is unavailable") from exc
        except Exception as exc:
            log.exception("failed to inspect released audio")
            raise HTTPException(status_code=503, detail="released lesson audio is unavailable") from exc
        size = int(head["ContentLength"])
        start, end = 0, size - 1
        status_code = 200
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(size)}
        if head.get("ETag"):
            headers["ETag"] = head["ETag"]
        range_header = request.headers.get("range")
        request_args: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if range_header:
            try:
                start, end = parse_single_byte_range(range_header, size)
            except ValueError as exc:
                raise HTTPException(status_code=416, detail="requested byte range is not satisfiable",
                                    headers={"Content-Range": f"bytes */{size}"}) from exc
            request_args["Range"] = f"bytes={start}-{end}"
            status_code = 206
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            headers["Content-Length"] = str(end - start + 1)
        try:
            result = await run_in_threadpool(
                lambda: get_serving_catalog().s3.get_object(**request_args)
            )
        except Exception as exc:
            log.exception("failed to read released audio")
            raise HTTPException(status_code=503, detail="released lesson audio is unavailable") from exc
        body = result["Body"]
        def chunks():
            try:
                while data := body.read(64 * 1024):
                    yield data
            finally:
                body.close()
        return StreamingResponse(chunks(), status_code=status_code, media_type="audio/mpeg",
                                 headers=headers, background=BackgroundTask(body.close))

    @router.get("/v1/srs/due")
    async def due_srs_cards(
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=50, ge=1, le=200),
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, course_id)
        questions = lesson_questions(course)
        now = datetime.now(UTC)
        try:
            due = await run_in_threadpool(
                get_srs_store().due, user_id=user_id, course_id=course_id, now=now, limit=limit
            )
        except Exception as exc:
            log.exception("failed to read due SRS cards")
            raise HTTPException(status_code=503, detail="SRS storage is unavailable") from exc
        items = [{"question_id": row["question_id"], "due_at": row["due_at"].isoformat(),
                  "question": learner_question(questions[row["question_id"]])}
                 for row in due if row["question_id"] in questions]
        return {"course_id": course_id, "due_at": now.isoformat(), "items": items}

    @router.get("/v1/reviews")
    async def read_reviews(
        course_id: str = Query(min_length=1, max_length=80),
        limit: int = Query(default=100, ge=1, le=500),
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        await run_in_threadpool(load_released_course, course_id)
        try:
            rows = await run_in_threadpool(
                get_srs_store().history, user_id=user_id, course_id=course_id, limit=limit
            )
        except Exception as exc:
            log.exception("failed to read learner review history")
            raise HTTPException(status_code=503, detail="review history is unavailable") from exc
        return {"course_id": course_id, "reviews": [row.model_dump(mode="json") for row in rows]}

    @router.post("/v1/reviews", status_code=201)
    async def submit_review(
        submission: ReviewSubmission,
        user_id: str = Depends(require_learner_id),
    ) -> dict[str, Any]:
        course = await run_in_threadpool(load_released_course, submission.course_id)
        if submission.question_id not in lesson_questions(course):
            raise HTTPException(status_code=404, detail="released review question not found")
        try:
            entry, due_at = await run_in_threadpool(
                get_srs_store().review,
                user_id=user_id,
                course_id=submission.course_id,
                question_id=submission.question_id,
                rating=submission.rating,
                reviewed_at=submission.reviewed_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("failed to persist learner review")
            raise HTTPException(status_code=503, detail="review could not be saved") from exc
        return {"review": entry.model_dump(mode="json"), "due_at": due_at.isoformat()}

    return router
