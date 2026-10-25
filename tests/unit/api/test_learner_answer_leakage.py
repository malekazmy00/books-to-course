"""R0 containment (audits 06 C4, 09 C4): no learner-facing response reveals an answer.

Walks every learner/serving route with a fixture item bank whose answers, rationales and
evidence anchors carry unique sentinels, then asserts that no answer field name and no
sentinel value appears anywhere in the JSON. A new learner route must be added to
``LEARNER_REQUESTS`` or ``test_every_learner_route_is_walked`` fails.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from b2c_api.app import create_app
from b2c_api.routers.serving import serving_router
from b2c_contracts import CourseOutline, CourseRelease, Question, ReviewLogEntry
from b2c_core import Settings
from b2c_pedagogy.qa_generation import CourseQAResult

FORBIDDEN_KEYS = {
    "answer", "answer_index", "answer_text", "rationale", "validation", "evidence_sids",
    "option_evidence_sids", "evidence_quote", "evidence_chunk_ids", "automated_checks",
    "automated_evaluation", "correct_option", "correct_answer",
}
SENTINELS = ("RATIONALE-SENTINEL", "GROUNDED-ANSWER-SENTINEL", "EVIDENCE-QUOTE-SENTINEL",
             "EVIDENCE-SID-SENTINEL", "OPTION-EVIDENCE-SENTINEL", "SHORT-ANSWER-SENTINEL",
             "VALIDATION-SENTINEL", "CHUNK-SENTINEL")


def _question(qid: str, lesson_id: str, **extra) -> dict:
    base = dict(qid=qid, lesson_id=lesson_id, purpose="end_of_lesson", type="mcq", bloom="apply",
                stem=f"Stem {qid}?", options=["Alpha", "Beta", "Gamma"], answer_index=1,
                rationale=f"RATIONALE-SENTINEL {qid}", evidence_sids=[f"EVIDENCE-SID-SENTINEL-{qid}"],
                option_evidence_sids=[["OPTION-EVIDENCE-SENTINEL-a"], ["OPTION-EVIDENCE-SENTINEL-b"],
                                      ["OPTION-EVIDENCE-SENTINEL-c"]],
                validation={"solver": "VALIDATION-SENTINEL"})
    base.update(extra)
    return Question(**base).model_dump(mode="json")


def _course():
    outline = CourseOutline(course_id="demo", spine_book_id="main", supplementary_book_ids=[], requires_review=False,
        modules=[{"module_id": "m1", "title": "One", "order": 1, "lessons": [
            {"lesson_id": "l1", "title": "First", "order": 1,
             "spine_span": {"start_block": "b1", "end_block": "b2", "pages": [1, 2]}, "provenance": "headings"},
            {"lesson_id": "l2", "title": "Next", "order": 2,
             "spine_span": {"start_block": "b3", "end_block": "b4", "pages": [3, 4]}, "provenance": "headings",
             "prior_lessons": ["l1"]}]}])
    grounded = {"qa_id": "g1", "question": "What is grounded?", "answer": "GROUNDED-ANSWER-SENTINEL",
                "evidence_chunk_ids": ["CHUNK-SENTINEL"], "book_id": "main", "role": "spine", "pages": [1],
                "evidence_quote": "EVIDENCE-QUOTE-SENTINEL", "model_id": "fixture", "prompt_version": "1",
                "automated_checks": {"grounded": True}}
    asset = {
        "lesson_id": "l1", "status": "final",
        "draft": {"lesson_id": "l1", "version": 1, "faithfulness": {},
                  "sentences": [{"sid": "l1:s001", "text_en": "Lesson text.", "citations": ["c1"], "kind": "primary"}]},
        "summary": {"lesson_id": "l1", "points": ["p1", "p2", "p3"], "point_sids": [["l1:s001"]] * 3},
        "glossary": [{"term_en": "term", "definition_en": "definition", "first_sid": "l1:s001", "lesson_id": "l1"}],
        "questions": [
            _question("l1:q001", "l1"),
            _question("l1:q002", "l1", type="short_answer", options=None, answer_index=None,
                      option_evidence_sids=None, answer_text="SHORT-ANSWER-SENTINEL"),
        ],
        "automated_evaluation": {"VALIDATION-SENTINEL": 1},
    }
    qa = CourseQAResult(status="final", expected_lesson_ids=["l1", "l2"], covered_lesson_ids=["l1", "l2"],
                        scopes=[{"scope_id": "l1", "kind": "lesson", "lesson_ids": ["l1"], "status": "final",
                                 "items": [grounded]},
                                {"scope_id": "course", "kind": "course", "lesson_ids": ["l1", "l2"],
                                 "status": "final", "items": [{**grounded, "qa_id": "g2"}]}],
                        model_id="fixture", prompt_version="1", lesson_assets=[asset])
    release = CourseRelease(release_id="r1", course_id="demo", lesson_versions={"l1": 1, "l2": 1},
                            model_versions={}, voice={"provider": "fasih_v1", "voice_id": "v", "cloned": False},
                            human_reviewed_ratio=0, book_rights={"main": "cleared"}, created_at=datetime.now(UTC))
    audio = SimpleNamespace(segments=[{"sid": "l1:s001", "start_s": 0.0, "end_s": 1.0,
                                       "text": "الإجابة: GROUNDED-ANSWER-SENTINEL"}])
    return SimpleNamespace(release=release, outline=outline, qa=qa, audio_assets={"l1": audio})


class _Catalog:
    s3 = None

    def released_course_ids(self):
        return ["demo"]

    def load(self, course_id):
        if course_id != "demo":
            raise KeyError(course_id)
        return _course()


class _SRS:
    def due(self, **_):
        now = datetime.now(UTC)
        return [{"question_id": "l1:q001", "due_at": now}, {"question_id": "l1:q002", "due_at": now}]

    def history(self, **_):
        return [ReviewLogEntry(card_id="l1:q001", user_id="learner", rating=3, reviewed_at=datetime.now(UTC))]

    def review(self, **kwargs):
        entry = ReviewLogEntry(card_id=kwargs["question_id"], user_id=kwargs["user_id"], rating=kwargs["rating"],
                               reviewed_at=kwargs["reviewed_at"])
        return entry, datetime.now(UTC)


# (method, route template, concrete path, json body)
LEARNER_REQUESTS = [
    ("GET", "/v1/released-courses", "/v1/released-courses", None),
    ("GET", "/v1/courses/{course_id}", "/v1/courses/demo", None),
    ("GET", "/v1/lessons/{lesson_id}", "/v1/lessons/l1?course_id=demo", None),
    ("GET", "/v1/lessons/{lesson_id}/review-quiz", "/v1/lessons/l2/review-quiz?course_id=demo", None),
    ("GET", "/v1/lessons/{lesson_id}/segments", "/v1/lessons/l1/segments?course_id=demo", None),
    ("GET", "/v1/srs/due", "/v1/srs/due?course_id=demo", None),
    ("GET", "/v1/reviews", "/v1/reviews?course_id=demo", None),
    ("POST", "/v1/reviews", "/v1/reviews",
     {"course_id": "demo", "question_id": "l1:q001", "rating": 3, "reviewed_at": datetime.now(UTC).isoformat()}),
]
# Binary audio stream: it carries no JSON and is covered by test_serving.py.
NON_JSON_LEARNER_ROUTES = {("GET", "/v1/lessons/{lesson_id}/audio")}


def _leaks(value, path="$"):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_KEYS or key.endswith("_evidence_sids"):
                found.append(f"{path}.{key}")
            found += _leaks(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += _leaks(item, f"{path}[{index}]")
    elif isinstance(value, str):
        found += [f"{path}={sentinel}" for sentinel in SENTINELS if sentinel in value]
    return found


def test_every_learner_route_is_walked():
    routes = {(method, route.path) for route in serving_router(SimpleNamespace(
        app=None, settings=None, get_store=None)).routes for method in route.methods}
    walked = {(method, template) for method, template, _, _ in LEARNER_REQUESTS}
    assert routes == walked | NON_JSON_LEARNER_ROUTES


async def test_no_learner_route_reveals_answers():
    app = create_app(settings=Settings(), serving_catalog=_Catalog(), srs_store=_SRS(),
                     learner_authenticator=lambda request: "learner")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, _template, path, body in LEARNER_REQUESTS:
            response = await client.request(method, path, json=body)
            assert response.status_code in (200, 201), (path, response.status_code, response.text)
            payload = response.json()
            assert _leaks(payload) == [], (path, _leaks(payload))
            # Defence in depth: the raw body must not carry a sentinel either.
            assert not [s for s in SENTINELS if s in json.dumps(payload, ensure_ascii=False)], path


async def test_learner_payloads_still_carry_the_questions():
    """Hiding the answer must not hide the question itself."""
    app = create_app(settings=Settings(), serving_catalog=_Catalog(), srs_store=_SRS(),
                     learner_authenticator=lambda request: "learner")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        lesson = (await client.get("/v1/lessons/l1?course_id=demo")).json()
        assert [q["qid"] for q in lesson["lesson_assets"]["questions"]] == ["l1:q001", "l1:q002"]
        assert lesson["lesson_assets"]["questions"][0]["options"] == ["Alpha", "Beta", "Gamma"]
        assert lesson["grounded_questions"] == [{"qa_id": "g1", "question": "What is grounded?", "pages": [1]}]
        course = (await client.get("/v1/courses/demo")).json()
        assert {s["scope_id"] for s in course["course_qa"]["scopes"]} == {"l1", "course"}
        due = (await client.get("/v1/srs/due?course_id=demo")).json()
        assert [item["question"]["stem"] for item in due["items"]] == ["Stem l1:q001?", "Stem l1:q002?"]
