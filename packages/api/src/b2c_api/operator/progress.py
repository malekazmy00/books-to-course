"""Stages of one book on the new path, computed from already-read data (pure functions).

Sources per stage:

* the stage-output registry (``StageOutputRecord``: status and ``GateResult``s), matched to the book
  by ``config.book_id``, ``config.run_id`` (a translation run of the book) or lineage (an input
  artifact that is the book's, transitively);
* ``translation_runs`` / ``translation_unit_results`` counts for the translation (D-028 part D);
* ``book_termbase_entries`` counts for the termbase;
* Temporal for the workflow state.

A stage with no data of its own is ``waiting`` while one of its prerequisites is not complete, and
``not_started`` otherwise.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from b2c_contracts import StageOutputRecord

from b2c_api.operator.reader import BookTermbase, RunCounts, WorkflowState
from b2c_api.operator.schemas import (
    GateInfo,
    GateSummary,
    OutputInfo,
    Stage,
    StageInfo,
    StageProgress,
    WorkflowInfo,
)


@dataclass(frozen=True)
class StageDef:
    stage_id: str
    title: str
    registry_stages: tuple[str, ...]
    depends_on: tuple[str, ...]

    def info(self, order: int) -> StageInfo:
        return StageInfo(stage_id=self.stage_id, order=order, title=self.title,
                         registry_stages=list(self.registry_stages), depends_on=list(self.depends_on))


STAGES: tuple[StageDef, ...] = (
    StageDef("extraction", "الاستخراج", ("ingest-parse-pdf",), ()),
    StageDef("outline", "المخطط", ("build-outline-v2",), ("extraction",)),
    StageDef("termbase", "قاموس المصطلحات", ("book-termbase", "book-termbase-approved"), ("extraction",)),
    StageDef("translation", "الترجمة", ("translate-book",), ("termbase",)),
    StageDef("translator_notes", "حواشي المترجم", ("translator-notes",), ("translation",)),
    StageDef("knowledge", "المعرفة والمفاهيم", ("knowledge-research", "concept-layer"), ("termbase",)),
    StageDef("episodes", "الحلقات", ("episode-script-en", "episode-script-ar"),
             ("outline", "translator_notes", "knowledge")),
    StageDef("audio", "الصوت", ("speech-text", "speech-track"), ("episodes",)),
    StageDef("mp3", "MP3", ("episode-mp3",), ("audio",)),
)
STAGE_BY_ID = {s.stage_id: s for s in STAGES}
STATUS_TEXT = {"not_started": "لم تبدأ", "running": "جارية", "completed": "مكتملة", "failed": "فشلت",
               "waiting": "بانتظار مرحلة سابقة"}
FAILED_WORKFLOW = frozenset({"FAILED", "TERMINATED", "TIMED_OUT", "CANCELED"})
LEGACY_NOTE = "مخرَج من قبل سجل الترقية: لا توجد له نتيجة بوابة."


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def book_aliases(book: Mapping[str, Any]) -> list[str]:
    return list(dict.fromkeys(str(x) for x in (book.get("book_id"), book.get("artifact_book_id")) if x))


def _ref_key(value: Any) -> str | None:
    return value.get("key") if isinstance(value, Mapping) else None


def book_runs(book: Mapping[str, Any], runs: Iterable[RunCounts]) -> list[RunCounts]:
    aliases = set(book_aliases(book))
    return sorted((r for r in runs if r.book_id in aliases), key=lambda r: r.created_at, reverse=True)


def book_records(book: Mapping[str, Any], records: Sequence[StageOutputRecord], runs: Sequence[RunCounts], *,
                 single_book: bool) -> list[StageOutputRecord]:
    """The registry records that belong to this book (config identity or lineage, transitively)."""
    if single_book:
        return list(records)
    aliases = set(book_aliases(book))
    run_ids = {r.run_id for r in runs}
    seeds = {k for k in (_ref_key(book.get("parsed_artifact_ref")), _ref_key(book.get("source_artifact_ref")))
             if k}
    seeds |= {k for k in (_ref_key(r.units_ref) for r in runs) if k}
    matched: dict[tuple[str, str], StageOutputRecord] = {}
    changed = True
    while changed:
        changed = False
        for record in records:
            ident = (record.artifact.stage, record.artifact.key)
            if ident in matched:
                continue
            config = record.config or {}
            if (config.get("book_id") in aliases or config.get("run_id") in run_ids
                    or any(i.key in seeds for i in record.inputs)):
                matched[ident] = record
                seeds.add(record.artifact.key)
                changed = True
    return [r for r in records if (r.artifact.stage, r.artifact.key) in matched]


def _scalar_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in metrics.items():
        if isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, str) and len(value) <= 200 and "://" not in value:
            out[key] = value
    return out


def gate_infos(record: StageOutputRecord) -> list[GateInfo]:
    latest = record.latest_gate_results() if hasattr(record, "latest_gate_results") else {
        g.gate_id: g for g in record.gate_results}
    return [GateInfo(gate_id=g.gate_id, kind=g.kind, passed=g.passed, decider=g.decider, decided_by=g.decided_by,
                     decided_at=g.decided_at.isoformat(), metrics=_scalar_metrics(g.metrics))
            for g in latest.values()]


def gate_summary(record: StageOutputRecord | None) -> GateSummary:
    if record is None:
        return GateSummary(state="none")
    results = gate_infos(record)
    if record.status == "accepted":
        state = "passed"
    elif record.status == "rejected" or any(not g.passed for g in results):
        state = "failed"
    elif record.status == "candidate":
        state = "pending"
    else:
        state = "none"
    return GateSummary(state=state, record_status=record.status, results=results)


def output_info(record: StageOutputRecord) -> OutputInfo:
    return OutputInfo(stage=record.artifact.stage, key=record.artifact.key[:16], status=record.status,
                      status_reason=record.status_reason, created_at=record.artifact.created_at.isoformat(),
                      status_changed_at=record.status_changed_at.isoformat(), gates=gate_infos(record))


def workflow_info(state: WorkflowState | None) -> WorkflowInfo | None:
    if state is None:
        return None
    name = state.status.upper()
    if name in ("RUNNING", "CONTINUED_AS_NEW"):
        kind = "running"
    elif name == "COMPLETED":
        kind = "completed"
    elif name in FAILED_WORKFLOW:
        kind = "failed"
    else:
        kind = "unknown"
    return WorkflowInfo(workflow_id=state.workflow_id, state=kind, status=name,
                        current_activity=state.current_activity, activity_attempt=state.activity_attempt,
                        started_at=iso(state.started_at), closed_at=iso(state.closed_at))


def _percent(done: int | None, total: int | None) -> float | None:
    if done is None or not total:
        return None
    return round(min(100.0, 100.0 * done / total), 1)


def translation_counts(run: RunCounts) -> dict[str, int]:
    c = run.counts
    counts = {"accepted": c.get("accepted", 0), "note": c.get("needs_review", 0), "dropped": c.get("dropped", 0),
              "rejected": c.get("rejected", 0)}
    if run.unit_count is not None:
        counts["pending"] = max(0, run.unit_count - sum(c.values()))
        counts["total"] = run.unit_count
    return counts


def translation_progress(run: RunCounts) -> StageProgress:
    decided = sum(run.counts.values())
    rate = float(run.decided_last_hour) if run.decided_last_hour else None
    pending = None if run.unit_count is None else max(0, run.unit_count - decided)
    eta = round(pending / rate * 3600) if rate and pending else (0 if pending == 0 else None)
    return StageProgress(done=decided, total=run.unit_count, percent=_percent(decided, run.unit_count),
                         rate_per_hour=rate, eta_s=eta)


@dataclass
class BookInputs:
    book: Mapping[str, Any]
    records: list[StageOutputRecord]  # this book's registry records, newest first
    runs: list[RunCounts]  # this book's translation runs, newest first
    termbase: BookTermbase | None
    workflows: dict[str, WorkflowState | None]
    episode_total: int | None

    @property
    def primary_run(self) -> RunCounts | None:
        return self.runs[0] if self.runs else None

    def stage_records(self, stage_id: str) -> list[StageOutputRecord]:
        names = STAGE_BY_ID[stage_id].registry_stages
        out = [r for r in self.records if r.artifact.stage in names]
        if stage_id in ("translation", "translator_notes"):
            run = self.primary_run
            out = [r for r in out if run is not None and (r.config or {}).get("run_id") == run.run_id]
        return out


def _episode_count(records: Sequence[StageOutputRecord]) -> int:
    numbers = {(r.config or {}).get("episode") for r in records}
    return len(numbers) if None not in numbers else len(records)


def _from_records(stage: StageDef, records: list[StageOutputRecord], total: int | None
                  ) -> tuple[str, StageProgress, StageOutputRecord | None] | None:
    """Status, progress and representative record of a stage made of registry outputs."""
    if not records:
        return None
    final = [r for r in records if r.artifact.stage == stage.registry_stages[-1]] or records
    representative = final[0]
    accepted = [r for r in final if r.status == "accepted"]
    candidates = [r for r in records if r.status == "candidate"]
    done = _episode_count(accepted) if total is not None else (1 if accepted else 0)
    if total is not None:
        complete = done >= total > 0
    else:
        complete = representative.status == "accepted" and not candidates
    if complete:
        status = "completed"
    elif representative.status == "rejected" and not candidates:
        status = "failed"
    else:
        status = "running"
    if total is not None:
        progress = StageProgress(done=done, total=total, percent=_percent(done, total))
    else:
        progress = StageProgress(percent=100.0 if status == "completed" else None)
    return status, progress, representative


def _updated(*values: datetime | None) -> str | None:
    present = [v for v in values if v is not None]
    return max(present).isoformat() if present else None


def compute_stages(inp: BookInputs) -> list[Stage]:
    done: dict[str, str] = {}
    stages: list[Stage] = []
    for order, stage in enumerate(STAGES, 1):
        records = inp.stage_records(stage.stage_id)
        status: str | None = None
        progress = StageProgress()
        counts: dict[str, int] = {}
        representative = records[0] if records else None
        workflow: WorkflowInfo | None = None
        note: str | None = None
        updated = _updated(*(r.status_changed_at for r in records[:1]), *(r.artifact.created_at for r in records[:1]))

        if stage.stage_id == "extraction":
            workflow = workflow_info(inp.workflows.get(str(inp.book.get("workflow_id") or "")))
            found = _from_records(stage, records, None)
            if found:
                status, progress, representative = found
            elif inp.book.get("parsed_artifact_ref"):
                status, note = "completed", LEGACY_NOTE
                progress = StageProgress(percent=100.0)
                parsed = inp.book["parsed_artifact_ref"]
                updated = parsed.get("created_at") if isinstance(parsed, Mapping) else None
            elif workflow is not None and workflow.state in ("running", "failed"):
                status = workflow.state

        elif stage.stage_id == "termbase":
            tb = inp.termbase
            if tb is not None:
                candidates = tb.pending + tb.approved + tb.rejected
                counts = {"candidates": candidates, "approved": tb.approved, "rejected": tb.rejected,
                          "pending": tb.pending}
                reviewed = tb.approved + tb.rejected
                progress = StageProgress(done=reviewed, total=candidates, percent=_percent(reviewed, candidates))
                status = "running" if tb.pending > 0 or candidates == 0 else "completed"
                if tb.pending:
                    note = f"بانتظار مراجعة {tb.pending} مصطلحًا."
                updated = _updated(tb.built_at, tb.last_decided_at)
                approved = [r for r in records if r.artifact.stage == "book-termbase-approved"]
                representative = approved[0] if approved else None
            else:
                found = _from_records(stage, records, None)
                if found:
                    status, progress, representative = found

        elif stage.stage_id == "translation":
            run = inp.primary_run
            if run is not None:
                counts = translation_counts(run)
                progress = translation_progress(run)
                workflow = workflow_info(inp.workflows.get(run.workflow_id))
                pending = counts.get("pending")
                if run.unit_count and pending == 0:
                    status = "completed"
                elif workflow is not None and workflow.state == "failed":
                    status = "failed"
                elif workflow is not None and workflow.state == "completed":
                    status, note = "failed", "انتهى الـ workflow قبل أن تُحسم كل الوحدات."
                else:
                    status = "running"
                updated = _updated(run.updated_at, run.last_decided_at, *(r.status_changed_at for r in records[:1]))
                if len(inp.runs) > 1:
                    note = (note + " " if note else "") + f"آخر تشغيل من {len(inp.runs)} تشغيلات لهذا الكتاب."

        elif stage.stage_id == "translator_notes":
            run = inp.primary_run
            if run is not None:
                counts = {"notes": run.counts.get("needs_review", 0) + run.counts.get("rejected", 0)}
            found = _from_records(stage, records, None)
            if found:
                status, progress, representative = found

        else:
            total = None
            if stage.stage_id in ("episodes", "mp3"):
                total = inp.episode_total
            found = _from_records(stage, records, total)
            if found:
                status, progress, representative = found
                if stage.stage_id in ("episodes", "audio", "mp3"):
                    counts = {"accepted": sum(r.status == "accepted" for r in records
                                              if r.artifact.stage == stage.registry_stages[-1]),
                              "candidates": sum(r.status == "candidate" for r in records),
                              "rejected": sum(r.status == "rejected" for r in records)}

        if status is None:
            blocking = [d for d in stage.depends_on if done.get(d) != "completed"]
            status = "waiting" if blocking else "not_started"
            if blocking:
                note = "بانتظار: " + "، ".join(STAGE_BY_ID[d].title for d in blocking)
        if status == "completed" and progress.percent is None:
            progress = progress.model_copy(update={"percent": 100.0})
        done[stage.stage_id] = status
        stages.append(Stage(stage_id=stage.stage_id, order=order, title=stage.title, status=status,
                            status_text=STATUS_TEXT[status], progress=progress, counts=counts,
                            gate=gate_summary(representative), updated_at=updated, workflow=workflow, note=note))
    return stages


def current_stage(stages: Sequence[Stage]) -> Stage | None:
    """The earliest failed stage, else the latest running one, else the first incomplete one."""
    for stage in stages:
        if stage.status == "failed":
            return stage
    running = [s for s in stages if s.status == "running"]
    if running:
        return running[-1]
    return next((s for s in stages if s.status != "completed"), None)


def overall_percent(stages: Sequence[Stage]) -> float:
    total = 0.0
    for stage in stages:
        if stage.status == "completed":
            total += 100.0
        elif stage.status in ("running", "failed") and stage.progress.percent is not None:
            total += stage.progress.percent
    return round(total / len(stages), 1) if stages else 0.0
