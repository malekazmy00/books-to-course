package com.book2course.learner.ui.operator

import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.TextButton
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.RadioButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import android.net.Uri
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.CompositionLocalProvider
import com.book2course.learner.R
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.BookList
import com.book2course.learner.data.api.BookStages
import com.book2course.learner.data.api.BookSummary
import com.book2course.learner.data.api.GateInfo
import com.book2course.learner.data.api.OutputInfo
import com.book2course.learner.data.api.Rejection
import com.book2course.learner.data.api.RunInfo
import com.book2course.learner.data.api.Stage
import com.book2course.learner.data.api.StageDetail
import com.book2course.learner.data.api.StageStatuses
import com.book2course.learner.data.api.UnitSample
import com.book2course.learner.data.api.WorkflowInfo
import com.book2course.learner.ui.operator.StageAction
import com.book2course.learner.ui.operator.StageActionUi
import com.book2course.learner.domain.agoText
import com.book2course.learner.domain.etaSpoken
import com.book2course.learner.domain.etaText
import com.book2course.learner.domain.grouped
import com.book2course.learner.domain.parseIsoMillis
import com.book2course.learner.domain.percentText
import com.book2course.learner.ui.common.EmptyState
import com.book2course.learner.ui.common.ErrorBanner
import com.book2course.learner.ui.common.Heading
import com.book2course.learner.ui.common.LearnerScaffold
import com.book2course.learner.ui.common.RefreshBar

/* ---------------------------------------------------------------- labels */

@Composable
fun statusLabel(status: String): String = stringResource(
    when (status) {
        StageStatuses.RUNNING -> R.string.stage_status_running
        StageStatuses.COMPLETED -> R.string.stage_status_completed
        StageStatuses.FAILED -> R.string.stage_status_failed
        StageStatuses.WAITING -> R.string.stage_status_waiting
        else -> R.string.stage_status_not_started
    },
)

@Composable
fun gateLabel(state: String): String = stringResource(
    when (state) {
        "passed" -> R.string.gate_passed
        "failed" -> R.string.gate_failed
        "pending" -> R.string.gate_pending
        else -> R.string.gate_none
    },
)

@Composable
fun workflowLabel(state: String): String = stringResource(
    when (state) {
        "running" -> R.string.workflow_running
        "completed" -> R.string.workflow_completed
        "failed" -> R.string.workflow_failed
        else -> R.string.workflow_unknown
    },
)

@Composable
fun countLabel(key: String): String = when (key) {
    "accepted" -> stringResource(R.string.count_accepted)
    "note" -> stringResource(R.string.count_note)
    "dropped" -> stringResource(R.string.count_dropped)
    "rejected" -> stringResource(R.string.count_rejected)
    "pending" -> stringResource(R.string.count_pending)
    "total" -> stringResource(R.string.count_total)
    "candidates" -> stringResource(R.string.count_candidates)
    "approved" -> stringResource(R.string.count_approved)
    "notes" -> stringResource(R.string.count_notes)
    else -> key
}

/** Unit decisions as the owner names them: مقبول، حاشية (needs_review → translator note, D-040)، مُسقط، مرفوض. */
@Composable
fun decisionLabel(decision: String): String = when (decision) {
    "accepted" -> stringResource(R.string.count_accepted)
    "needs_review" -> stringResource(R.string.count_note)
    "dropped" -> stringResource(R.string.count_dropped)
    "rejected" -> stringResource(R.string.count_rejected)
    else -> decision
}

/** QA issue categories (b2c_contracts.QACategory) in Arabic. */
fun reasonLabel(category: String): String = when (category) {
    "omission" -> "حذف"
    "addition" -> "إضافة"
    "prompt_leakage" -> "تسرّب التعليمات"
    "meaning_error" -> "خطأ في المعنى"
    "polarity_error" -> "عكس المعنى"
    "number_error" -> "خطأ في الأرقام"
    "date_error" -> "خطأ في التاريخ"
    "footnote_marker" -> "علامة حاشية"
    "named_entity" -> "اسم علم"
    "terminology" -> "مصطلح"
    "untranslated_english" -> "إنجليزية غير مترجمة"
    "foreign_script" -> "حروف أجنبية"
    "quotation" -> "اقتباس"
    "bibliographic" -> "مرجع"
    "structure_formatting" -> "تنسيق"
    "grammar" -> "نحو"
    "fluency_style" -> "أسلوب"
    "repetition" -> "تكرار"
    "segmentation" -> "تقسيم"
    "other" -> "أخرى"
    else -> category
}

private val ISSUE = Regex("""^([a-z_]+) \(([a-z]+)\)(.*)$""", RegexOption.DOT_MATCHES_ALL)

fun severityLabel(severity: String): String = when (severity) {
    "critical" -> "حرِج"
    "major" -> "كبير"
    "minor" -> "طفيف"
    else -> severity
}

/** A server rejection reason ("omission (critical): check — explanation") split for display:
 *  an Arabic title ("حذف · حرِج") and the check's own (English) words, shown left-to-right. */
data class RejectionPart(val title: String?, val detail: String?)

/** Issue-shaped reasons (translation units) are split per issue; any other reason (a reviewer's note on a
 *  term, a gate's status reason) is kept whole as its own words. */
fun rejectionParts(reason: String): List<RejectionPart> {
    val parts = reason.split("; ").map { it.trim() }
    if (parts.none { ISSUE.matches(it) }) return listOf(RejectionPart(null, reason.trim()))
    return parts.map { part ->
        val m = ISSUE.matchEntire(part)
        if (m == null) {
            RejectionPart(null, part)
        } else {
            val (category, severity, rest) = m.destructured
            RejectionPart("${reasonLabel(category)} · ${severityLabel(severity)}", rest.removePrefix(":").trim().ifEmpty { null })
        }
    }
}

/** Ordered counts of a stage (translation: accepted, note, dropped, rejected, pending, total). */
private val COUNT_ORDER = listOf("accepted", "note", "dropped", "candidates", "approved", "rejected", "pending", "total", "notes")

fun orderedCounts(counts: Map<String, Int>): List<Pair<String, Int>> =
    counts.entries.sortedBy { COUNT_ORDER.indexOf(it.key).let { i -> if (i < 0) 99 else i } }.map { it.key to it.value }

@Composable
private fun statusColor(status: String): Color = when (status) {
    StageStatuses.COMPLETED -> MaterialTheme.colorScheme.primary
    StageStatuses.RUNNING -> MaterialTheme.colorScheme.tertiary
    StageStatuses.FAILED -> MaterialTheme.colorScheme.error
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

/* ---------------------------------------------------------------- shared pieces */

/** First-strong isolate (U+2068 … U+2069): a Latin book title keeps its own order inside an RTL line. */
fun isolate(text: String): String = "\u2068$text\u2069"

/** Status shown as colour + text (never colour alone). */
@Composable
fun StatusChip(status: String, modifier: Modifier = Modifier) {
    val color = statusColor(status)
    Surface(color = color.copy(alpha = 0.14f), contentColor = color, shape = RoundedCornerShape(50), modifier = modifier) {
        Text(statusLabel(status), Modifier.padding(horizontal = 10.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
    }
}

/** Determinate bar that eases to new values (no indeterminate animation, no blocking spinner). */
@Composable
fun SmoothProgress(fraction: Float, color: Color, modifier: Modifier = Modifier) {
    val animated by animateFloatAsState(fraction.coerceIn(0f, 1f), tween(600), label = "progress")
    LinearProgressIndicator(progress = { animated }, color = color, modifier = modifier.fillMaxWidth().height(8.dp))
}

/** Latin identifiers and source text keep their own direction inside the RTL layout. */
@Composable
fun Ltr(content: @Composable () -> Unit) = CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr, content = content)

@Composable
private fun operatorError(error: AppError?, hasData: Boolean, onRetry: () -> Unit) {
    when {
        error == null -> Unit
        error == AppError.NotFound && !hasData -> EmptyState(stringResource(R.string.operator_not_deployed))
        else -> ErrorBanner(error, hasData, onRetry)
    }
}

private fun now(): Long = System.currentTimeMillis()

/* ---------------------------------------------------------------- library */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OperatorLibraryScreen(
    ui: OperatorUi<BookList>,
    onOpenBook: (BookSummary) -> Unit,
    onRefresh: () -> Unit,
    onSettings: () -> Unit,
    nowMs: Long = now(),
) {
    LearnerScaffold(
        title = stringResource(R.string.operator_title), onBack = null,
        actions = { IconButton(onClick = onSettings) { Icon(Icons.Filled.Settings, contentDescription = stringResource(R.string.settings)) } },
    ) {
        val data = ui.data
        RefreshBar(ui.userRefreshing && data != null)
        operatorError(ui.loadable.error, data != null, onRefresh)
        PullToRefreshBox(isRefreshing = ui.showRefreshing, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) {
            LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxSize()) {
                if (data != null) {
                    item(key = "generated") { GeneratedLine(data.generatedAt, nowMs) }
                    if (data.books.isEmpty()) item(key = "empty") { EmptyState(stringResource(R.string.operator_empty)) }
                }
                items(data?.books.orEmpty(), key = { it.courseId + "/" + it.bookId }) { book ->
                    BookCard(book, nowMs, Modifier.animateItem()) { onOpenBook(book) }
                }
            }
        }
    }
}

@Composable
private fun GeneratedLine(generatedAt: String, nowMs: Long) {
    val ago = agoText(nowMs, parseIsoMillis(generatedAt)) ?: generatedAt
    Column {
        Text(stringResource(R.string.operator_generated, ago), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(stringResource(R.string.operator_auto_refresh), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
fun BookCard(book: BookSummary, nowMs: Long, modifier: Modifier = Modifier, onOpen: () -> Unit) {
    val percent = percentText(book.overallPercent) ?: "0٪"
    val stageTitle = book.currentStageTitle ?: stringResource(R.string.operator_all_done)
    val status = statusLabel(book.currentStatus)
    val uploaded = parseIsoMillis(book.uploadedAt)?.let { stringResource(R.string.operator_uploaded, agoText(nowMs, it) ?: "") }
        ?: stringResource(R.string.operator_upload_unknown)
    val description = stringResource(R.string.operator_book_desc, book.title.trimEnd('.'), stageTitle, status, book.completedStages,
        book.stageCount, percent, uploaded)
    val openLabel = stringResource(R.string.operator_open_stages)
    Card(
        modifier = modifier.fillMaxWidth().heightIn(min = 88.dp).clickable(onClick = onOpen)
            .clearAndSetSemantics { contentDescription = description; onClick(label = openLabel, action = { onOpen(); true }) },
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                // Content direction: a Latin title keeps its punctuation in place inside the RTL layout.
                Text(book.title, style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Content), fontWeight = FontWeight.Bold,
                    maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = null)
            }
            Ltr {
                Text("${book.courseId} · ${book.shortId}", style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Text(uploaded, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.operator_current, stageTitle), style = MaterialTheme.typography.bodyMedium)
                StatusChip(book.currentStatus)
            }
            SmoothProgress((book.overallPercent / 100.0).toFloat(), statusColor(book.currentStatus))
            Text(stringResource(R.string.operator_completed_of, book.completedStages, book.stageCount) + " · " + percent,
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

/* ---------------------------------------------------------------- stages of one book */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BookStagesScreen(
    ui: OperatorUi<BookStages>,
    titleFallback: String,
    onBack: () -> Unit,
    onOpenStage: (Stage) -> Unit,
    onRefresh: () -> Unit,
    nowMs: Long = now(),
) {
    val data = ui.data
    LearnerScaffold(title = isolate(data?.title ?: titleFallback), onBack = onBack) {
        RefreshBar(ui.userRefreshing && data != null)
        operatorError(ui.loadable.error, data != null, onRefresh)
        PullToRefreshBox(isRefreshing = ui.showRefreshing, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) {
            LazyColumn(contentPadding = PaddingValues(16.dp), modifier = Modifier.fillMaxSize()) {
                if (data != null) {
                    item(key = "summary") {
                        val done = data.stages.count { it.status == StageStatuses.COMPLETED }
                        val overall = data.stages.sumOf { s ->
                            if (s.status == StageStatuses.COMPLETED) 100.0 else s.progress.percent ?: 0.0
                        } / data.stages.size.coerceAtLeast(1)
                        Column(Modifier.padding(bottom = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(stringResource(R.string.operator_stages_summary, done, data.stages.size, percentText(overall) ?: ""),
                                style = MaterialTheme.typography.bodyMedium)
                            GeneratedLine(data.generatedAt, nowMs)
                        }
                    }
                }
                val stages = data?.stages.orEmpty()
                items(stages, key = { it.stageId }) { stage ->
                    StageTimelineRow(stage, isLast = stage == stages.last(), nowMs = nowMs, modifier = Modifier.animateItem()) { onOpenStage(stage) }
                }
            }
        }
    }
}

/** The sentence TalkBack reads for a stage (one node per stage). */
@Composable
fun stageDescription(stage: Stage, nowMs: Long): String {
    val parts = mutableListOf("${stage.order}. ${stage.title}: ${statusLabel(stage.status)}")
    val p = stage.progress
    if (p.done != null && p.total != null) parts += stringResource(R.string.stage_numbers, grouped(p.done), grouped(p.total))
    percentText(p.percent)?.let { parts += it }
    orderedCounts(stage.counts).filter { it.first != "total" }.takeIf { it.isNotEmpty() }?.let { counts ->
        parts += counts.map { (k, v) -> "${countLabel(k)} ${grouped(v)}" }.joinToString("، ")
    }
    if (stage.status == StageStatuses.RUNNING) {
        etaSpoken(p.etaS)?.let { parts += stringResource(R.string.stage_eta, it) }
    }
    parts += stringResource(R.string.gate_label, gateLabel(stage.gate.state))
    stage.workflow?.let { w ->
        parts += stringResource(R.string.workflow_label, workflowLabel(w.state)) +
            (w.currentActivity?.let { "، " + stringResource(R.string.workflow_activity, it) } ?: "")
    }
    agoText(nowMs, parseIsoMillis(stage.updatedAt))?.let { parts += stringResource(R.string.stage_updated, it) }
    stage.note?.let { parts += it }
    return parts.joinToString(". ")
}

@Composable
fun StageTimelineRow(stage: Stage, isLast: Boolean, nowMs: Long, modifier: Modifier = Modifier, onOpen: (() -> Unit)?) {
    val color = statusColor(stage.status)
    val description = stageDescription(stage, nowMs)
    val openLabel = stringResource(R.string.operator_open_detail)
    Row(
        modifier.fillMaxWidth().height(IntrinsicSize.Min)
            .then(if (onOpen != null) Modifier.clickable(onClick = onOpen) else Modifier)
            .clearAndSetSemantics {
                contentDescription = description
                if (onOpen != null) onClick(label = openLabel, action = { onOpen(); true })
            },
    ) {
        // Timeline rail: numbered dot, then a line down to the next stage.
        Column(Modifier.width(36.dp).fillMaxHeight(), horizontalAlignment = Alignment.CenterHorizontally) {
            Box(Modifier.size(28.dp).background(color, CircleShape), contentAlignment = Alignment.Center) {
                when (stage.status) {
                    StageStatuses.COMPLETED -> Icon(Icons.Filled.Check, null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.size(18.dp))
                    StageStatuses.FAILED -> Icon(Icons.Filled.Close, null, tint = MaterialTheme.colorScheme.onError, modifier = Modifier.size(18.dp))
                    else -> Text("${stage.order}", color = MaterialTheme.colorScheme.surface, style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.Bold)
                }
            }
            if (!isLast) Box(Modifier.width(2.dp).weight(1f).background(color.copy(alpha = 0.4f)))
        }
        Spacer(Modifier.width(8.dp))
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
            modifier = Modifier.weight(1f).padding(bottom = 12.dp).animateContentSize(),
        ) {
            StageBody(stage, nowMs, showChevron = onOpen != null)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StageBody(stage: Stage, nowMs: Long, showChevron: Boolean) {
    val p = stage.progress
    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stage.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
            StatusChip(stage.status)
            if (showChevron) Icon(Icons.AutoMirrored.Filled.KeyboardArrowLeft, contentDescription = null)
        }
        val fraction = when {
            stage.status == StageStatuses.COMPLETED -> 1f
            p.percent != null -> (p.percent / 100.0).toFloat()
            else -> null
        }
        if (fraction != null) SmoothProgress(fraction, statusColor(stage.status))
        val numbers = buildList {
            if (p.done != null && p.total != null) add(stringResource(R.string.stage_numbers, grouped(p.done), grouped(p.total)))
            percentText(p.percent)?.let { add(it) }
            p.ratePerHour?.let { add(stringResource(R.string.stage_rate, grouped(it.toInt()))) }
        }
        if (numbers.isNotEmpty()) Text(numbers.joinToString(" · "), style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
        if (stage.status == StageStatuses.RUNNING && (p.etaS != null || p.ratePerHour == null && p.total != null)) {
            Text(etaText(p.etaS)?.let { stringResource(R.string.stage_eta, it) } ?: stringResource(R.string.stage_eta_unknown),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.tertiary)
        }
        val counts = orderedCounts(stage.counts)
        if (counts.isNotEmpty()) {
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                counts.forEach { (k, v) -> CountChip(countLabel(k), v) }
            }
        }
        Text(stringResource(R.string.gate_label, gateLabel(stage.gate.state)), style = MaterialTheme.typography.bodySmall)
        stage.workflow?.let { WorkflowLine(it) }
        agoText(nowMs, parseIsoMillis(stage.updatedAt))?.let {
            Text(stringResource(R.string.stage_updated, it), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        stage.note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

@Composable
private fun CountChip(label: String, value: Int) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = RoundedCornerShape(8.dp)) {
        Text("$label ${grouped(value)}", Modifier.padding(horizontal = 8.dp, vertical = 3.dp), style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun WorkflowLine(w: WorkflowInfo) {
    Text(stringResource(R.string.workflow_label, workflowLabel(w.state)) +
        (w.currentActivity?.let { " · " + stringResource(R.string.workflow_activity, it) } ?: ""),
        style = MaterialTheme.typography.bodySmall)
}

/* ---------------------------------------------------------------- one stage in detail */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StageDetailScreen(
    ui: OperatorUi<StageDetail>,
    titleFallback: String,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    actionUi: StageActionUi = StageActionUi(),
    onControl: (StageAction) -> Unit = {},
    onExport: (Uri, Boolean) -> Unit = { _, _ -> },
    onStartTranslation: (String, String) -> Unit = { _, _ -> },
    nowMs: Long = now(),
) {
    val data = ui.data
    var confirmAction by remember { mutableStateOf<StageAction?>(null) }
    var exportIsAudio by remember { mutableStateOf(false) }
    var showTranslationStart by remember { mutableStateOf(false) }
    var sourceLanguage by remember { mutableStateOf("en") }
    val createZip = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) onExport(uri, exportIsAudio)
    }
    if (confirmAction != null && data != null) {
        val action = confirmAction!!
        val title = if (action == StageAction.PAUSE) "إيقاف مؤقت آمن" else "استئناف من الحفظ"
        val message = if (action == StageAction.PAUSE)
            "سيُطلب من العامل التوقف بعد تسجيل نقطة الحفظ الحالية. لن يُقطع الجزء الجاري."
        else "سيفحص التطبيق حالة المسار ونقاط الحفظ أولًا، ثم يطلب استكمال غير المحفوظ فقط. لن يبدأ عند غياب بيانات استئناف آمنة."
        AlertDialog(onDismissRequest = { confirmAction = null }, title = { Text(title) }, text = { Text(message) },
            confirmButton = { TextButton(onClick = { confirmAction = null; onControl(action) }) { Text("تأكيد") } },
            dismissButton = { TextButton(onClick = { confirmAction = null }) { Text("إلغاء") } })
    }
    if (showTranslationStart && data != null) {
        val targetLanguage = if (sourceLanguage == "en") "ar" else "en"
        AlertDialog(
            onDismissRequest = { showTranslationStart = false },
            title = { Text("بدء ترجمة متن الكتاب") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("اختر لغة النص المستخرج؛ سيُرسل طلب واحد إلى العامل بعد التحقق من اكتمال OCR وعدم وجود ترجمة محفوظة.")
                    listOf("en" to "النص الأصلي إنجليزي", "ar" to "النص الأصلي عربي").forEach { (code, label) ->
                        Row(Modifier.fillMaxWidth().clickable { sourceLanguage = code }, verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = sourceLanguage == code, onClick = { sourceLanguage = code })
                            Text(label)
                        }
                    }
                    Text("اللغة الناتجة: ${if (targetLanguage == "ar") "العربية" else "الإنجليزية"}")
                }
            },
            confirmButton = { TextButton(onClick = { showTranslationStart = false; onStartTranslation(sourceLanguage, targetLanguage) }) { Text("بدء الترجمة") } },
            dismissButton = { TextButton(onClick = { showTranslationStart = false }) { Text("إلغاء") } },
        )
    }
    LearnerScaffold(title = data?.stage?.title ?: titleFallback, onBack = onBack) {
        RefreshBar(ui.userRefreshing && data != null)
        operatorError(ui.loadable.error, data != null, onRefresh)
        PullToRefreshBox(isRefreshing = ui.showRefreshing, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) {
            LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.fillMaxSize()) {
                if (data != null) {
                    item(key = "controls") {
                        StageControlsCard(
                            detail = data, actionUi = actionUi,
                            onPause = { confirmAction = StageAction.PAUSE },
                            onResume = { confirmAction = StageAction.RESUME_OR_RETRY },
                            onStartTranslation = { showTranslationStart = true },
                            onExportResults = {
                                exportIsAudio = false
                                createZip.launch("${data.courseId}-results.zip")
                            },
                            onExportAudio = {
                                exportIsAudio = true
                                createZip.launch("${data.courseId}-audio.zip")
                            },
                        )
                    }
                    detailItems(data, nowMs)
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StageControlsCard(
    detail: StageDetail,
    actionUi: StageActionUi,
    onPause: () -> Unit,
    onResume: () -> Unit,
    onStartTranslation: () -> Unit,
    onExportResults: () -> Unit,
    onExportAudio: () -> Unit,
) {
    val stage = detail.stage
    val workflowId = stage.workflow?.workflowId
    val isAudio = stage.stageId.contains("audio", true) || workflowId?.startsWith("b2c-mp3-") == true
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("التحكم في المرحلة", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            if (workflowId == null) {
                if (stage.stageId == "translation" && stage.status in setOf(StageStatuses.NOT_STARTED, StageStatuses.FAILED)) {
                    Text("يمكن بدء ترجمة المتن من النص المستخرج. سيفحص التطبيق اكتمال OCR ويمنع تكرار تشغيل محفوظ.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Button(onClick = onStartTranslation, enabled = !actionUi.busy) { Text("بدء ترجمة الكتاب") }
                } else {
                    Text("لا يوجد تشغيل محفوظ لهذه المرحلة. البدء يحتاج مدخلات أو اعتمادًا سابقًا غير متاح هنا؛ لن يُرسل طلب غير مكتمل.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                if (stage.status == StageStatuses.RUNNING) {
                    Button(onClick = onPause, enabled = !actionUi.busy) { Text("إيقاف مؤقت بعد الحفظ") }
                }
                val retryable = stage.status == StageStatuses.FAILED || stage.status == StageStatuses.RUNNING ||
                    (stage.counts["rejected"] ?: 0) > 0 || (stage.counts["failed"] ?: 0) > 0
                if (retryable) {
                    Button(onClick = onResume, enabled = !actionUi.busy) {
                        val failedOnly = stage.status == StageStatuses.FAILED ||
                            (stage.counts["rejected"] ?: 0) > 0 || (stage.counts["failed"] ?: 0) > 0
                        Text(if (failedOnly) "إعادة معالجة غير المحفوظ/الفاشل" else "استئناف من آخر حفظ")
                    }
                }
            }
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Button(onClick = onExportResults, enabled = !actionUi.busy) { Text("تصدير نتائج المقرر ZIP") }
                if (isAudio && workflowId != null) {
                    Button(onClick = onExportAudio, enabled = !actionUi.busy) { Text("تصدير صوت هذه المرحلة ZIP") }
                }
            }
            if (actionUi.busy) Text("جارٍ تنفيذ الطلب…", color = MaterialTheme.colorScheme.tertiary)
            actionUi.message?.let {
                Text(it, style = MaterialTheme.typography.bodySmall,
                    color = if (actionUi.error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
            }
        }
    }
}

private fun LazyListScope.detailItems(d: StageDetail, nowMs: Long) {
    item(key = "summary") {
        val description = stageDescription(d.stage, nowMs)
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
            modifier = Modifier.clearAndSetSemantics { contentDescription = description }) {
            StageBody(d.stage, nowMs, showChevron = false)
        }
    }
    item(key = "readonly") {
        Text(stringResource(R.string.detail_read_only), style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    val empty = d.runs.isEmpty() && d.recentUnits.isEmpty() && d.translatorNotes.isEmpty() && d.rejections.isEmpty() &&
        d.outputs.isEmpty() && d.stage.workflow == null
    if (empty) item(key = "empty") { EmptyState(stringResource(R.string.detail_empty)) }
    if (d.runs.isNotEmpty()) {
        item(key = "h-runs") { Heading(stringResource(R.string.detail_runs)) }
        items(d.runs, key = { "run-" + it.runId }) { RunCard(it, nowMs) }
    }
    if (d.recentUnits.isNotEmpty()) {
        item(key = "h-recent") { Heading(stringResource(R.string.detail_recent)) }
        items(d.recentUnits, key = { "recent-" + it.unitId }) { UnitCard(it) }
    }
    if (d.translatorNotes.isNotEmpty()) {
        val shown = d.translatorNotes.size
        val total = d.stage.counts["notes"] ?: ((d.stage.counts["note"] ?: 0) + (d.stage.counts["rejected"] ?: 0))
        item(key = "h-notes") {
            Heading(stringResource(R.string.detail_notes) + if (total > shown) " " + stringResource(R.string.detail_first_of, shown, total) else " ($shown)")
        }
        items(d.translatorNotes, key = { "note-" + it.unitId }) { UnitCard(it) }
    }
    if (d.rejections.isNotEmpty()) {
        item(key = "h-rejections") { Heading(stringResource(R.string.detail_rejections)) }
        items(d.rejections.withIndex().toList(), key = { "rej-" + it.index }) { RejectionRow(it.value, nowMs) }
    }
    if (d.outputs.isNotEmpty()) {
        item(key = "h-outputs") { Heading(stringResource(R.string.detail_outputs)) }
        items(d.outputs, key = { "out-" + it.stage + it.key }) { OutputCard(it, nowMs) }
    }
}

@Composable
private fun RunCard(run: RunInfo, nowMs: Long) {
    val title = stringResource(R.string.detail_run, run.runId.take(8), agoText(nowMs, parseIsoMillis(run.createdAt)) ?: run.createdAt)
    Card(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            run.progress.percent?.let { SmoothProgress((it / 100.0).toFloat(), MaterialTheme.colorScheme.tertiary) }
            val p = run.progress
            if (p.done != null && p.total != null) {
                Text(stringResource(R.string.stage_numbers, grouped(p.done), grouped(p.total)) + (percentText(p.percent)?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.bodyMedium)
            }
            Text(orderedCounts(run.counts).map { (k, v) -> "${countLabel(k)} ${grouped(v)}" }.joinToString(" · "),
                style = MaterialTheme.typography.bodySmall)
            run.workflow?.let { WorkflowLine(it) }
        }
    }
}

@Composable
private fun UnitCard(unit: UnitSample) {
    val reasons = unit.reasons.joinToString("، ") { reasonLabel(it) }
    val noTarget = stringResource(R.string.detail_no_target)
    val description = stringResource(R.string.detail_unit_desc, unit.seq, decisionLabel(unit.decision), unit.sourceText ?: "—",
        unit.targetText ?: noTarget, if (reasons.isNotEmpty()) stringResource(R.string.detail_reasons, reasons) else "")
    Card(Modifier.fillMaxWidth().clearAndSetSemantics { contentDescription = description }) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Ltr { Text(unit.unitId, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                Spacer(Modifier.weight(1f))
                DecisionChip(unit.decision)
            }
            unit.sourceText?.let { src ->
                Text(stringResource(R.string.detail_source), style = MaterialTheme.typography.labelMedium)
                Ltr { Text(src, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.fillMaxWidth(), textAlign = TextAlign.Start, maxLines = 6, overflow = TextOverflow.Ellipsis) }
            }
            Text(stringResource(R.string.detail_target), style = MaterialTheme.typography.labelMedium)
            Text(unit.targetText ?: noTarget, style = MaterialTheme.typography.bodyLarge, maxLines = 8, overflow = TextOverflow.Ellipsis)
            if (reasons.isNotEmpty()) Text(stringResource(R.string.detail_reasons, reasons), style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
private fun DecisionChip(decision: String) {
    val status = when (decision) {
        "accepted" -> StageStatuses.COMPLETED
        "rejected" -> StageStatuses.FAILED
        "needs_review" -> StageStatuses.RUNNING
        else -> StageStatuses.NOT_STARTED
    }
    val color = statusColor(status)
    Surface(color = color.copy(alpha = 0.14f), contentColor = color, shape = RoundedCornerShape(50)) {
        Text(decisionLabel(decision), Modifier.padding(horizontal = 10.dp, vertical = 2.dp), style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Bold)
    }
}

@Composable
private fun RejectionRow(rejection: Rejection, nowMs: Long) {
    Column(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}.padding(vertical = 4.dp)) {
        Ltr { Text(rejection.subject, style = MaterialTheme.typography.titleSmall, modifier = Modifier.fillMaxWidth()) }
        rejectionParts(rejection.reason).forEach { part ->
            part.title?.let {
                Text(it, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.error)
            }
            part.detail?.let { detail ->
                Text(detail, style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Content),
                    modifier = Modifier.fillMaxWidth(), maxLines = 4, overflow = TextOverflow.Ellipsis)
            }
        }
        agoText(nowMs, parseIsoMillis(rejection.at))?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

@Composable
private fun OutputCard(output: OutputInfo, nowMs: Long) {
    Card(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Ltr { Text("${output.stage} · ${output.key}", style = MaterialTheme.typography.labelMedium) }
            Text(stringResource(R.string.detail_output, output.status, agoText(nowMs, parseIsoMillis(output.statusChangedAt)) ?: ""),
                style = MaterialTheme.typography.bodyMedium)
            output.statusReason?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
            output.gates.forEach { GateRow(it) }
        }
    }
}

@Composable
private fun GateRow(gate: GateInfo) {
    val metrics = gate.metrics.entries.take(6).joinToString(" · ") { (k, v) -> "$k=${v.toString().trim('"')}" }
    Column {
        Ltr { Text(gate.gateId, style = MaterialTheme.typography.labelMedium) }
        Text(gateLabel(if (gate.passed) "passed" else "failed") + " · " + gate.decidedBy, style = MaterialTheme.typography.bodySmall)
        if (metrics.isNotEmpty()) Ltr { Text(metrics, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}
