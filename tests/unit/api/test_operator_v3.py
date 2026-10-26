"""Operator API v3 (/v3/operator): read-only stages and progress of every book on the new path.

The router reads through an ``OperatorReader``; here a fake reader holds books, stage-output registry
records, translation runs, termbase counts and Temporal workflow states. Every route is a GET; the
responses carry no run input (profiles, fingerprints) and no secret; results are cached 15-30 s.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from b2c_contracts import ArtifactRef, GateResult, StageOutputRecord
from b2c_core import Settings
from fastapi.routing import APIRoute

from b2c_api.app import create_app
from b2c_api.operator.openapi import operator_openapi_text
from b2c_api.operator.reader import (
    BookTermbase,
    OperatorReader,
    PostgresOperatorReader,
    RunCounts,
    TermRejection,
    UnitRow,
    WorkflowState,
)
from b2c_api.operator.router import STAGES, operator_router

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
SHA = "a" * 40
SECRET = "SECRET-INPUT-SENTINEL"
RUN_A = "1" * 64
RUN_B = "2" * 64
RUN_OLD = "3" * 64


def ref(course: str, stage: str, n: int) -> ArtifactRef:
    key = f"{n:064x}"
    return ArtifactRef(stage=stage, key=key, uri=f"s3://artifacts/{course}/{stage}/{key}/", sha256="c" * 64,
                       created_at=NOW - timedelta(hours=5) + timedelta(minutes=n))


def record(course: str, artifact: ArtifactRef, status: str, *, inputs=(), config=None, passed=True,
           reason=None) -> StageOutputRecord:
    gate = GateResult(stage=artifact.stage, artifact=artifact, gate_id="g.check", gate_version="v1",
                      kind="deterministic", passed=passed, metrics={"ratio": 0.5, "nested": {"x": 1}},
                      decider="system", decided_by="policy:g.check", decided_at=NOW - timedelta(hours=1))
    return StageOutputRecord(course_id=course, artifact=artifact, inputs=list(inputs), code_revision=SHA,
                             config=config or {}, required_gates=["g.check"],
                             gate_results=[] if status == "candidate" else [gate],
                             status=status, status_reason=reason, status_changed_at=NOW - timedelta(minutes=30),
                             status_changed_by="system:promotion")


class FakeReader(OperatorReader):
    def __init__(self):
        self.calls: dict[str, int] = {}
        self.fail = False
        self.clock = NOW
        self.parsed_a = ref("c1", "ingest-parse-pdf", 1)
        self.books_by_course = {
            "c1": [
                {"course_id": "c1", "book_id": "book-aaaaaaaaaaaaaaaaaaaaaaaa", "artifact_book_id": "book-main",
                 "title": "Structured Analytic Techniques", "display_name": "Structured Analytic Techniques · aaaaaaaa",
                 "file_sha256": "a" * 64, "uploaded_at": None, "role": "spine",
                 "workflow_id": "b2c-ingest-" + "a" * 64,
                 "parsed_artifact_ref": self.parsed_a.model_dump(mode="json"),
                 "source_artifact_ref": ref("c1", "book-upload", 2).model_dump(mode="json")},
                {"course_id": "c1", "book_id": "book-bbbbbbbbbbbbbbbbbbbbbbbb",
                 "artifact_book_id": "book-bbbbbbbbbbbbbbbbbbbbbbbb", "title": "كتاب التجربة",
                 "display_name": "كتاب التجربة · bbbbbbbb", "file_sha256": "b" * 64,
                 "uploaded_at": "2026-10-09T20:33:37+00:00", "role": "spine", "workflow_id": "",
                 "source_artifact_ref": ref("c1", "book-upload", 3).model_dump(mode="json")},
            ],
            "c2": [],
        }
        ingest_b = ref("c1", "ingest-parse-pdf", 10)
        outline_b = ref("c1", "build-outline-v2", 11)
        script_b = ref("c1", "episode-script-ar", 12)
        notes_old = ref("c1", "translator-notes", 13)
        self.records = {"c1": [
            record("c1", ingest_b, "accepted", config={"book_id": "book-bbbbbbbbbbbbbbbbbbbbbbbb"}),
            record("c1", outline_b, "rejected", inputs=[ingest_b], passed=False,
                   reason="required gate failed: g.check"),
            record("c1", script_b, "candidate", inputs=[outline_b]),
            # notes of an older run of book A (another run id): not the current run, not shown as complete
            record("c1", notes_old, "accepted", config={"run_id": RUN_OLD}),
        ], "c2": []}
        self.runs = {"c1": [
            RunCounts(run_id=RUN_OLD, workflow_id="b2c-translate-book-" + RUN_OLD, book_id="book-main",
                      unit_count=10, created_at=NOW - timedelta(days=3), updated_at=NOW - timedelta(days=2),
                      counts={"accepted": 10}, decided_last_hour=0,
                      units_ref=ref("c1", "translation-units", 20).model_dump(mode="json")),
            RunCounts(run_id=RUN_A, workflow_id="b2c-translate-book-" + RUN_A, book_id="book-main",
                      unit_count=4560, created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(minutes=1),
                      counts={"accepted": 2457, "needs_review": 204, "rejected": 4, "dropped": 98},
                      decided_last_hour=120, units_ref=ref("c1", "translation-units", 21).model_dump(mode="json")),
            RunCounts(run_id=RUN_B, workflow_id="b2c-translate-book-" + RUN_B,
                      book_id="book-bbbbbbbbbbbbbbbbbbbbbbbb", unit_count=100,
                      created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(hours=2),
                      counts={"accepted": 50}, decided_last_hour=0,
                      units_ref=ref("c1", "translation-units", 22).model_dump(mode="json")),
        ], "c2": []}
        self.termbases = {
            ("c1", "book-main"): BookTermbase(pending=0, approved=492, rejected=332, built_at=NOW - timedelta(days=1),
                                              entry_count=824, last_decided_at=NOW - timedelta(hours=20)),
            ("c1", "book-bbbbbbbbbbbbbbbbbbbbbbbb"): BookTermbase(
                pending=5, approved=10, rejected=1, built_at=NOW - timedelta(days=1), entry_count=16,
                last_decided_at=None),
        }
        self.workflows = {
            "b2c-ingest-" + "a" * 64: WorkflowState(workflow_id="b2c-ingest-" + "a" * 64, status="COMPLETED",
                                                    started_at=NOW - timedelta(days=9),
                                                    closed_at=NOW - timedelta(days=9)),
            "b2c-translate-book-" + RUN_A: WorkflowState(
                workflow_id="b2c-translate-book-" + RUN_A, status="RUNNING", started_at=NOW - timedelta(days=1),
                current_activity="translate_unit_batch", activity_attempt=1),
            "b2c-translate-book-" + RUN_B: WorkflowState(
                workflow_id="b2c-translate-book-" + RUN_B, status="FAILED", started_at=NOW - timedelta(days=1),
                closed_at=NOW - timedelta(hours=2)),
        }

    def _hit(self, name):
        self.calls[name] = self.calls.get(name, 0) + 1
        if self.fail:
            raise RuntimeError("database is down: postgresql://b2c:pw@postgres/b2c")

    def now(self):
        return self.clock

    async def course_ids(self):
        self._hit("course_ids")
        return sorted(self.books_by_course)

    async def books(self, course_id):
        self._hit("books")
        return [dict(b) for b in self.books_by_course.get(course_id, [])]

    async def stage_outputs(self, course_id):
        self._hit("stage_outputs")
        return list(self.records.get(course_id, []))

    async def translation_runs(self, course_id):
        self._hit("translation_runs")
        return list(self.runs.get(course_id, []))

    async def termbase(self, course_id, book_ids):
        self._hit("termbase")
        for book_id in book_ids:
            if (course_id, book_id) in self.termbases:
                return self.termbases[(course_id, book_id)]
        return None

    async def termbase_rejections(self, course_id, book_ids, limit):
        self._hit("termbase_rejections")
        return [TermRejection(source="hegemony", reason="generic word, not a term", decided_at=NOW)][:limit]

    async def recent_units(self, run_id, limit):
        self._hit("recent_units")
        return [UnitRow(unit_id="book-main:u00002", seq=2, decision="accepted", target_text="ترجمة الوحدة الثانية",
                        issues=[], updated_at=NOW - timedelta(minutes=1)),
                UnitRow(unit_id="book-main:u00001", seq=1, decision="needs_review", target_text="ترجمة تقريبية",
                        issues=[{"category": "meaning_error", "severity": "major"}],
                        updated_at=NOW - timedelta(minutes=2))][:limit]

    async def units_by_decision(self, run_id, decisions, limit):
        self._hit("units_by_decision")
        rows = [UnitRow(unit_id="book-main:u00001", seq=1, decision="needs_review", target_text="ترجمة تقريبية",
                        issues=[{"category": "meaning_error", "severity": "major"}], updated_at=NOW),
                UnitRow(unit_id="book-main:u00007", seq=7, decision="rejected", target_text="",
                        issues=[{"category": "untranslated_english", "severity": "critical", "check_id": "lang.latin",
                                 "evidence": {"explanation": "English prose left in place"}},
                                {"category": "omission", "severity": "major"}], updated_at=NOW)]
        return [r for r in rows if r.decision in decisions][:limit]

    async def unit_sources(self, course_id, units_ref, unit_ids):
        self._hit("unit_sources")
        return {u: f"SOURCE {u}" for u in unit_ids}

    async def workflow(self, workflow_id):
        self._hit("workflow")
        return self.workflows.get(workflow_id)

    async def episode_total(self, course_id, outline_record):
        self._hit("episode_total")
        return 12


@pytest.fixture
def reader():
    return FakeReader()


def client(reader, **kwargs):
    app = create_app(settings=Settings(), artifact_store=object(), operator_reader=reader, **kwargs)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


A = "/v3/operator/courses/c1/books/book-aaaaaaaaaaaaaaaaaaaaaaaa"
B = "/v3/operator/courses/c1/books/book-bbbbbbbbbbbbbbbbbbbbbbbb"


def by_id(stages):
    return {s["stage_id"]: s for s in stages}


def test_every_operator_route_is_a_get():
    routes = {(m, r.path) for r in operator_router(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))).routes
              if isinstance(r, APIRoute) for m in r.methods}
    assert routes == {("GET", "/v3/operator/meta"), ("GET", "/v3/operator/books"),
                      ("GET", "/v3/operator/courses/{course_id}/books/{book_id}/stages"),
                      ("GET", "/v3/operator/courses/{course_id}/books/{book_id}/stages/{stage_id}")}


async def test_meta_lists_the_nine_new_path_stages_in_order(reader):
    async with client(reader) as c:
        meta = (await c.get("/v3/operator/meta")).json()
    assert [s["stage_id"] for s in meta["stages"]] == [
        "extraction", "outline", "termbase", "translation", "translator_notes", "knowledge", "episodes", "audio",
        "mp3"]
    assert meta["stages"][3]["title"] == "الترجمة"
    assert "translate-book" in meta["stages"][3]["registry_stages"]
    assert 15 <= meta["cache_ttl_s"] <= 30 and meta["refresh_interval_s"] == 15


async def test_library_lists_books_with_title_identity_upload_date_and_progress(reader):
    async with client(reader) as c:
        body = (await c.get("/v3/operator/books")).json()
    books = {b["book_id"]: b for b in body["books"]}
    assert [b["book_id"] for b in body["books"]] == ["book-aaaaaaaaaaaaaaaaaaaaaaaa",
                                                     "book-bbbbbbbbbbbbbbbbbbbbbbbb"]  # most recently active first
    assert set(books) == {"book-aaaaaaaaaaaaaaaaaaaaaaaa", "book-bbbbbbbbbbbbbbbbbbbbbbbb"}
    a = books["book-aaaaaaaaaaaaaaaaaaaaaaaa"]
    assert a["title"] == "Structured Analytic Techniques" and a["course_id"] == "c1"
    assert a["short_id"] == "aaaaaaaa" and a["uploaded_at"] is None
    assert a["current_stage"] == "translation" and a["current_status"] == "running"
    assert 0 < a["overall_percent"] < 100
    b = books["book-bbbbbbbbbbbbbbbbbbbbbbbb"]
    assert b["uploaded_at"] == "2026-10-09T20:33:37+00:00"
    assert b["current_status"] == "failed"
    assert body["generated_at"]


async def test_book_stages_follow_the_new_path_with_counts_rate_and_eta(reader):
    async with client(reader) as c:
        body = (await c.get(A + "/stages")).json()
    stages = body["stages"]
    assert [s["stage_id"] for s in stages] == [s.stage_id for s in STAGES]
    s = by_id(stages)
    # legacy extraction: done, but no gate (made before the promotion registry)
    assert s["extraction"]["status"] == "completed" and s["extraction"]["gate"]["state"] == "none"
    assert s["extraction"]["workflow"]["state"] == "completed"
    assert s["outline"]["status"] == "not_started"
    tb = s["termbase"]
    assert tb["status"] == "completed"
    assert tb["counts"] == {"candidates": 824, "approved": 492, "rejected": 332, "pending": 0}
    tr = s["translation"]
    assert tr["status"] == "running"
    assert tr["counts"] == {"accepted": 2457, "note": 204, "dropped": 98, "rejected": 4, "pending": 1797,
                            "total": 4560}
    assert tr["progress"]["done"] == 2763 and tr["progress"]["total"] == 4560
    assert tr["progress"]["percent"] == pytest.approx(60.6, abs=0.1)
    assert tr["progress"]["rate_per_hour"] == 120
    assert tr["progress"]["eta_s"] == round(1797 / 120 * 3600)
    assert tr["workflow"]["state"] == "running" and tr["workflow"]["current_activity"] == "translate_unit_batch"
    assert tr["updated_at"] == (NOW - timedelta(minutes=1)).isoformat()
    # the notes of an older run do not complete the current run's notes stage
    assert s["translator_notes"]["status"] == "waiting"
    assert s["translator_notes"]["counts"]["notes"] == 208
    assert s["knowledge"]["status"] == "not_started"
    for later in ("episodes", "audio", "mp3"):
        assert s[later]["status"] == "waiting", later


async def test_registry_gates_lineage_and_failures_of_another_book(reader):
    async with client(reader) as c:
        body = (await c.get(B + "/stages")).json()
    s = by_id(body["stages"])
    assert s["extraction"]["status"] == "completed"
    assert s["extraction"]["gate"]["state"] == "passed"
    assert s["extraction"]["gate"]["results"][0]["gate_id"] == "g.check"
    assert s["extraction"]["gate"]["results"][0]["metrics"] == {"ratio": 0.5}  # scalars only
    assert s["outline"]["status"] == "failed" and s["outline"]["gate"]["state"] == "failed"
    # the episode script derives from this book's outline (lineage), so it is this book's
    assert s["episodes"]["status"] == "running" and s["episodes"]["gate"]["state"] == "pending"
    assert s["episodes"]["progress"] == {"done": 0, "total": 12, "percent": 0.0, "rate_per_hour": None,
                                         "eta_s": None}
    assert s["termbase"]["status"] == "running" and s["termbase"]["counts"]["pending"] == 5
    # the translation workflow failed before all units were decided
    assert s["translation"]["status"] == "failed" and s["translation"]["workflow"]["state"] == "failed"
    async with client(reader) as c:
        a = by_id((await c.get(A + "/stages")).json()["stages"])
    assert a["episodes"]["status"] == "waiting"  # book B's script is not book A's


async def test_translation_detail_has_samples_notes_rejections_and_runs(reader):
    async with client(reader) as c:
        body = (await c.get(A + "/stages/translation")).json()
    assert body["stage"]["stage_id"] == "translation"
    recent = body["recent_units"]
    assert recent[0] == {"unit_id": "book-main:u00002", "seq": 2, "decision": "accepted",
                         "source_text": "SOURCE book-main:u00002", "target_text": "ترجمة الوحدة الثانية",
                         "reasons": [], "updated_at": (NOW - timedelta(minutes=1)).isoformat()}
    notes = body["translator_notes"]
    assert [n["unit_id"] for n in notes] == ["book-main:u00001", "book-main:u00007"]
    assert notes[0]["reasons"] == ["meaning_error"]
    rejected = body["rejections"]
    assert rejected[0]["subject"] == "book-main:u00007"
    assert "untranslated_english" in rejected[0]["reason"] and "omission" in rejected[0]["reason"]
    assert "lang.latin — English prose left in place" in rejected[0]["reason"]  # the check's own explanation
    runs = body["runs"]
    assert [r["run_id"] for r in runs] == [RUN_A, RUN_OLD]  # newest first; only this book's runs
    assert runs[0]["counts"]["note"] == 204 and runs[0]["workflow"]["state"] == "running"


async def test_termbase_and_outline_details_explain_rejections(reader):
    async with client(reader) as c:
        tb = (await c.get(A + "/stages/termbase")).json()
        outline = (await c.get(B + "/stages/outline")).json()
    assert tb["rejections"] == [{"subject": "hegemony", "reason": "generic word, not a term",
                                 "at": NOW.isoformat()}]
    assert outline["outputs"][0]["status"] == "rejected"
    assert outline["outputs"][0]["gates"][0]["passed"] is False
    assert outline["rejections"][0]["reason"] == "required gate failed: g.check"


async def test_unknown_book_stage_or_bad_ids(reader):
    async with client(reader) as c:
        assert (await c.get("/v3/operator/courses/c1/books/book-nope/stages")).status_code == 404
        assert (await c.get(A + "/stages/ocr")).status_code == 404
        assert (await c.get("/v3/operator/courses/..bad/books/x/stages")).status_code == 422


async def test_reader_failure_is_503_without_details(reader):
    reader.fail = True
    async with client(reader) as c:
        r = await c.get("/v3/operator/books")
    assert r.status_code == 503 and "postgresql" not in r.text and "pw" not in r.text


async def test_results_are_cached_between_15_and_30_seconds(reader):
    ticks = [1000.0]
    async with client(reader, operator_monotonic=lambda: ticks[0]) as c:
        await c.get(A + "/stages")
        first = dict(reader.calls)
        ticks[0] += 10
        await c.get(A + "/stages")
        assert reader.calls == first  # served from cache
        ticks[0] += 25
        await c.get(A + "/stages")
        assert reader.calls["translation_runs"] > first["translation_runs"]


async def test_etag_revalidation(reader):
    async with client(reader) as c:
        r1 = await c.get("/v3/operator/books")
        r2 = await c.get("/v3/operator/books", headers={"If-None-Match": r1.headers["etag"]})
    assert r2.status_code == 304 and r2.content == b""


async def test_no_run_input_or_secret_is_exposed(reader):
    reader.runs["c1"][1] = RunCounts(**{**reader.runs["c1"][1].__dict__,
                                        "units_ref": {**reader.runs["c1"][1].units_ref, "key": "k" * 64}})
    async with client(reader) as c:
        texts = [(await c.get(p)).text for p in (
            "/v3/operator/meta", "/v3/operator/books", A + "/stages", A + "/stages/translation",
            A + "/stages/termbase", B + "/stages/outline")]
    for text in texts:
        for forbidden in ("engine_profile", "profile_fingerprint", "qe_profile", "input_json", SECRET,
                          "s3://", "postgresql://"):
            assert forbidden not in text


def test_committed_openapi_matches_the_code():
    path = Path(__file__).resolve().parents[3] / "docs" / "api" / "operator-v3.openapi.json"
    assert path.read_text(encoding="utf-8") == operator_openapi_text(), (
        "docs/api/operator-v3.openapi.json is stale: run `uv run python scripts/export_operator_openapi.py`")
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert all(set(item) == {"get"} for item in doc["paths"].values())


# ----------------------------------------------------------------- the Postgres reader stays light
class RecordingConn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        flat = " ".join(sql.split())
        self.log.append(flat)
        rows = []
        if "FROM translation_runs" in flat:
            rows = [(RUN_A, "wf", "book-main", 10, NOW, NOW, None)]
        return SimpleNamespace(fetchall=lambda: list(rows))


async def test_postgres_reader_is_read_only_indexed_and_bounded():
    log, opened = [], []

    def connect(**kwargs):
        opened.append(kwargs)
        return RecordingConn(log)

    reader = PostgresOperatorReader(SimpleNamespace(settings=SimpleNamespace(pg_dsn="postgresql://x")),
                                    connect=connect)
    await reader.stage_outputs("c1")
    await reader.translation_runs("c1")
    await reader.termbase("c1", ["book-main"])
    await reader.termbase_rejections("c1", ["book-main"], 20)
    await reader.recent_units(RUN_A, 10)
    await reader.units_by_decision(RUN_A, ["needs_review", "rejected"], 50)
    assert opened and all("default_transaction_read_only=on" in o["options"] and "statement_timeout" in o["options"]
                          for o in opened)
    assert log, "the reader issued no SQL"
    for sql in log:
        assert sql.startswith(("SELECT", "WITH")), sql
        assert " WHERE " in sql, sql  # never a full-table read
        assert not any(w in sql.upper() for w in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ")), sql
    unit_sql = [s for s in log if "translation_unit_results" in s]
    assert any("GROUP BY run_id, decision" in s for s in unit_sql)  # aggregated on the (run_id, decision) index
    assert all("LIMIT" in s or "GROUP BY" in s for s in unit_sql)
