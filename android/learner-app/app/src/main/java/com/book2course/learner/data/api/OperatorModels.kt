package com.book2course.learner.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/*
 * Operator API v3 models (read-only stages and progress). They mirror
 * docs/api/operator-v3.openapi.json field for field; OperatorOpenApiConformanceTest fails when a
 * schema and its model drift apart.
 */

object StageStatuses {
    const val NOT_STARTED = "not_started"
    const val RUNNING = "running"
    const val COMPLETED = "completed"
    const val FAILED = "failed"
    const val WAITING = "waiting"
}

@Serializable
data class StageInfo(
    @SerialName("stage_id") val stageId: String,
    val order: Int,
    val title: String,
    @SerialName("registry_stages") val registryStages: List<String>,
    @SerialName("depends_on") val dependsOn: List<String>,
)

@Serializable
data class OperatorMeta(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("min_client_version") val minClientVersion: Int,
    @SerialName("cache_ttl_s") val cacheTtlS: Int,
    @SerialName("refresh_interval_s") val refreshIntervalS: Int,
    val stages: List<StageInfo>,
)

@Serializable
data class StageProgress(
    val done: Int? = null,
    val total: Int? = null,
    val percent: Double? = null,
    @SerialName("rate_per_hour") val ratePerHour: Double? = null,
    @SerialName("eta_s") val etaS: Long? = null,
)

@Serializable
data class GateInfo(
    @SerialName("gate_id") val gateId: String,
    val kind: String,
    val passed: Boolean,
    val decider: String,
    @SerialName("decided_by") val decidedBy: String,
    @SerialName("decided_at") val decidedAt: String,
    val metrics: Map<String, JsonElement> = emptyMap(),
)

@Serializable
data class GateSummary(
    val state: String,
    @SerialName("record_status") val recordStatus: String? = null,
    val results: List<GateInfo> = emptyList(),
)

@Serializable
data class WorkflowInfo(
    @SerialName("workflow_id") val workflowId: String,
    val state: String,
    val status: String,
    @SerialName("current_activity") val currentActivity: String? = null,
    @SerialName("activity_attempt") val activityAttempt: Int? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("closed_at") val closedAt: String? = null,
)

@Serializable
data class Stage(
    @SerialName("stage_id") val stageId: String,
    val order: Int,
    val title: String,
    val status: String,
    @SerialName("status_text") val statusText: String,
    val progress: StageProgress,
    val counts: Map<String, Int> = emptyMap(),
    val gate: GateSummary,
    @SerialName("updated_at") val updatedAt: String? = null,
    val workflow: WorkflowInfo? = null,
    val note: String? = null,
)

@Serializable
data class BookSummary(
    @SerialName("course_id") val courseId: String,
    @SerialName("book_id") val bookId: String,
    val title: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("short_id") val shortId: String,
    @SerialName("file_sha256") val fileSha256: String? = null,
    @SerialName("uploaded_at") val uploadedAt: String? = null,
    val role: String? = null,
    @SerialName("current_stage") val currentStage: String? = null,
    @SerialName("current_stage_title") val currentStageTitle: String? = null,
    @SerialName("current_status") val currentStatus: String,
    @SerialName("overall_percent") val overallPercent: Double,
    @SerialName("completed_stages") val completedStages: Int,
    @SerialName("stage_count") val stageCount: Int,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class BookList(
    val books: List<BookSummary>,
    @SerialName("generated_at") val generatedAt: String,
)

@Serializable
data class BookStages(
    @SerialName("course_id") val courseId: String,
    @SerialName("book_id") val bookId: String,
    val title: String,
    val stages: List<Stage>,
    @SerialName("generated_at") val generatedAt: String,
)

@Serializable
data class UnitSample(
    @SerialName("unit_id") val unitId: String,
    val seq: Int,
    val decision: String,
    @SerialName("source_text") val sourceText: String? = null,
    @SerialName("target_text") val targetText: String? = null,
    val reasons: List<String> = emptyList(),
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class Rejection(
    val subject: String,
    val reason: String,
    val at: String? = null,
)

@Serializable
data class OutputInfo(
    val stage: String,
    val key: String,
    val status: String,
    @SerialName("status_reason") val statusReason: String? = null,
    @SerialName("created_at") val createdAt: String,
    @SerialName("status_changed_at") val statusChangedAt: String,
    val gates: List<GateInfo> = emptyList(),
)

@Serializable
data class RunInfo(
    @SerialName("run_id") val runId: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
    @SerialName("unit_count") val unitCount: Int? = null,
    val counts: Map<String, Int>,
    val progress: StageProgress,
    val workflow: WorkflowInfo? = null,
)

@Serializable
data class StageDetail(
    @SerialName("course_id") val courseId: String,
    @SerialName("book_id") val bookId: String,
    val stage: Stage,
    val outputs: List<OutputInfo> = emptyList(),
    val runs: List<RunInfo> = emptyList(),
    @SerialName("recent_units") val recentUnits: List<UnitSample> = emptyList(),
    @SerialName("translator_notes") val translatorNotes: List<UnitSample> = emptyList(),
    val rejections: List<Rejection> = emptyList(),
    @SerialName("generated_at") val generatedAt: String,
)
