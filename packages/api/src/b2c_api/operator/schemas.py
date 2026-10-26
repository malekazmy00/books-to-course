"""Operator API v3 models (the read-only contract of the Android «المراحل والتقدم» section).

Projections only: no run input (engine/QE profiles, fingerprints), no storage URI, no DSN, no
token. Gate metrics are reduced to scalar values.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "1.0.0"
MIN_CLIENT_VERSION = 1

StageId = Literal["extraction", "outline", "termbase", "translation", "translator_notes", "knowledge", "episodes",
                  "audio", "mp3"]
StageStatus = Literal["not_started", "running", "completed", "failed", "waiting"]
GateState = Literal["passed", "failed", "pending", "none"]
WorkflowStateName = Literal["running", "completed", "failed", "unknown"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageInfo(_Model):
    stage_id: StageId
    order: int
    title: str
    registry_stages: list[str] = Field(description="stage-output registry stages this stage reads")
    depends_on: list[StageId]


class OperatorMeta(_Model):
    api_version: str
    min_client_version: int
    cache_ttl_s: int = Field(description="server-side cache of every response, seconds")
    refresh_interval_s: int = Field(description="suggested client auto-refresh while visible, seconds")
    stages: list[StageInfo]


class StageProgress(_Model):
    done: int | None = None
    total: int | None = None
    percent: float | None = None
    rate_per_hour: float | None = Field(default=None, description="units decided in the last hour")
    eta_s: int | None = Field(default=None, description="expected remaining seconds at rate_per_hour")


class GateInfo(_Model):
    gate_id: str
    kind: str
    passed: bool
    decider: str
    decided_by: str
    decided_at: str
    metrics: dict[str, float | int | bool | str | None] = {}


class GateSummary(_Model):
    state: GateState
    record_status: Literal["candidate", "accepted", "rejected", "quarantined"] | None = None
    results: list[GateInfo] = []


class WorkflowInfo(_Model):
    workflow_id: str
    state: WorkflowStateName
    status: str = Field(description="Temporal status name, e.g. RUNNING, COMPLETED, FAILED")
    current_activity: str | None = None
    activity_attempt: int | None = None
    started_at: str | None = None
    closed_at: str | None = None


class Stage(_Model):
    stage_id: StageId
    order: int
    title: str
    status: StageStatus
    status_text: str
    progress: StageProgress
    counts: dict[str, int] = {}
    gate: GateSummary
    updated_at: str | None = None
    workflow: WorkflowInfo | None = None
    note: str | None = None


class BookSummary(_Model):
    course_id: str
    book_id: str
    title: str
    display_name: str
    short_id: str
    file_sha256: str | None = None
    uploaded_at: str | None = None
    role: str | None = None
    current_stage: StageId | None = None
    current_stage_title: str | None = None
    current_status: StageStatus
    overall_percent: float
    completed_stages: int
    stage_count: int
    updated_at: str | None = None


class BookList(_Model):
    books: list[BookSummary]
    generated_at: str


class BookStages(_Model):
    course_id: str
    book_id: str
    title: str
    stages: list[Stage]
    generated_at: str


class UnitSample(_Model):
    unit_id: str
    seq: int
    decision: str
    source_text: str | None = None
    target_text: str | None = None
    reasons: list[str] = []
    updated_at: str


class Rejection(_Model):
    subject: str
    reason: str
    at: str | None = None


class OutputInfo(_Model):
    stage: str
    key: str = Field(description="first 16 characters of the artifact key")
    status: str
    status_reason: str | None = None
    created_at: str
    status_changed_at: str
    gates: list[GateInfo] = []


class RunInfo(_Model):
    run_id: str
    created_at: str
    updated_at: str
    unit_count: int | None = None
    counts: dict[str, int]
    progress: StageProgress
    workflow: WorkflowInfo | None = None


class StageDetail(_Model):
    course_id: str
    book_id: str
    stage: Stage
    outputs: list[OutputInfo] = []
    runs: list[RunInfo] = []
    recent_units: list[UnitSample] = []
    translator_notes: list[UnitSample] = []
    rejections: list[Rejection] = []
    generated_at: str
