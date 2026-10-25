package com.book2course.learner.ui.player

import android.content.Context
import android.view.accessibility.AccessibilityManager
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.TextDecrease
import androidx.compose.material.icons.filled.TextIncrease
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.CompositionLocalProvider
import com.book2course.learner.R
import com.book2course.learner.data.api.EpisodeStatus
import com.book2course.learner.data.api.LearnerQuestion
import com.book2course.learner.data.api.Segment
import com.book2course.learner.data.api.Voices
import com.book2course.learner.data.local.LearnerSettings
import com.book2course.learner.domain.clock
import com.book2course.learner.ui.common.EmptyState
import com.book2course.learner.ui.common.Heading
import com.book2course.learner.ui.common.VoiceChip
import com.book2course.learner.ui.common.sectionTitle

private sealed interface TranscriptRow {
    data class SectionHeader(val id: String, val title: String, val startS: Double?) : TranscriptRow
    data class Line(val segment: Segment, val showVoice: Boolean) : TranscriptRow
}

private fun isScreenReaderOn(context: Context): Boolean =
    (context.getSystemService(Context.ACCESSIBILITY_SERVICE) as? AccessibilityManager)?.isTouchExplorationEnabled == true

@Composable
fun TranscriptTab(ui: PlayerUi, actions: PlayerActions) {
    val detail = ui.episode.data
    if (detail == null || detail.status == EpisodeStatus.SCRIPT_PENDING || detail.sections.isEmpty()) {
        EmptyState(stringResource(R.string.transcript_pending))
        return
    }
    val rows = remember(detail) {
        buildList {
            detail.sections.forEach { section ->
                add(TranscriptRow.SectionHeader(section.sectionId, section.title, section.startS))
                var previousVoice: String? = null
                section.segments.forEach { seg ->
                    add(TranscriptRow.Line(seg, seg.voice != previousVoice || seg.voice == Voices.EXTERNAL))
                    previousVoice = seg.voice
                }
            }
        }
    }
    val scale = ui.settings.transcriptScale
    val listState = rememberLazyListState()
    val context = LocalContext.current
    val currentIndex = rows.indexOfFirst { it is TranscriptRow.Line && it.segment.segmentId == ui.segment?.segmentId }
    // Following the audio moves the list; with TalkBack on it would steal focus, so it stays put.
    LaunchedEffect(currentIndex, ui.settings.followTranscript, ui.isPlaying) {
        if (ui.settings.followTranscript && ui.isPlaying && currentIndex >= 0 && !isScreenReaderOn(context)) {
            listState.animateScrollToItem((currentIndex - 1).coerceAtLeast(0))
        }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { actions.updateSettings { it.copy(transcriptScale = (it.transcriptScale - 0.15f).coerceAtLeast(LearnerSettings.MIN_SCALE)) } }) {
                Icon(Icons.Filled.TextDecrease, stringResource(R.string.transcript_font_smaller))
            }
            IconButton(onClick = { actions.updateSettings { it.copy(transcriptScale = (it.transcriptScale + 0.15f).coerceAtMost(LearnerSettings.MAX_SCALE)) } }) {
                Icon(Icons.Filled.TextIncrease, stringResource(R.string.transcript_font_larger))
            }
            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
                Text(stringResource(R.string.transcript_follow), style = MaterialTheme.typography.labelLarge)
                Switch(checked = ui.settings.followTranscript, onCheckedChange = { on -> actions.updateSettings { it.copy(followTranscript = on) } },
                    modifier = Modifier.padding(start = 8.dp))
            }
        }
        val base = MaterialTheme.typography.bodyLarge
        val textStyle = base.copy(fontSize = base.fontSize * scale, lineHeight = base.lineHeight * scale)
        val playHere = stringResource(R.string.transcript_play_here)
        LazyColumn(state = listState, contentPadding = PaddingValues(16.dp), modifier = Modifier.fillMaxSize()) {
            itemsIndexed(rows, key = { _, row -> if (row is TranscriptRow.Line) "seg-" + row.segment.segmentId else "sec-" + (row as TranscriptRow.SectionHeader).id }) { _, row ->
                when (row) {
                    is TranscriptRow.SectionHeader -> Heading(
                        sectionTitle(detail.sections.first { it.sectionId == row.id }.kind, row.title),
                        Modifier.padding(top = 16.dp, bottom = 8.dp).clickable(enabled = row.startS != null && ui.audioAvailable, onClickLabel = playHere) {
                            row.startS?.let(actions.seekToSeconds)
                        },
                    )
                    is TranscriptRow.Line -> SegmentLine(row, row.segment.segmentId == ui.segment?.segmentId && ui.isCurrent, textStyle, ui, actions, playHere)
                }
            }
        }
    }
}

@Composable
private fun SegmentLine(row: TranscriptRow.Line, current: Boolean, style: TextStyle, ui: PlayerUi, actions: PlayerActions, playHere: String) {
    val seg = row.segment
    val highlight = if (current) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface
    Column(
        Modifier.fillMaxWidth().background(highlight, RoundedCornerShape(8.dp))
            .semantics(mergeDescendants = true) { selected = current }
            .clickable(enabled = seg.startS != null && ui.audioAvailable && !seg.hidden, onClickLabel = playHere) { seg.startS?.let(actions.seekToSeconds) }
            .padding(horizontal = 8.dp, vertical = 6.dp),
    ) {
        if (row.showVoice) VoiceChip(seg.voice, seg.sourceLabel, Modifier.padding(bottom = 4.dp))
        if (seg.hidden) {
            val question = ui.episode.data?.questions?.firstOrNull { it.answerSegmentId == seg.segmentId }
            val revealed = question?.let { ui.revealed[it.questionId] }
            if (revealed != null) {
                Text(stringResource(R.string.question_answer, revealed.answer), style = style)
            } else {
                Text(stringResource(R.string.transcript_hidden_answer), style = style, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (question != null) TextButton(onClick = { actions.reveal(question, null) }) { Text(stringResource(R.string.question_reveal)) }
            }
        } else {
            val text = seg.text.orEmpty()
            Text(text, style = style, fontWeight = if (seg.role == "heading") FontWeight.Bold else null)
            if (seg.pages.isNotEmpty()) {
                Text(stringResource(R.string.pages, seg.pages.joinToString("، ")), style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
fun SectionsTab(ui: PlayerUi, actions: PlayerActions) {
    val sections = ui.episode.data?.sections.orEmpty()
    if (sections.isEmpty()) {
        EmptyState(stringResource(R.string.transcript_pending))
        return
    }
    LazyColumn(contentPadding = PaddingValues(vertical = 8.dp), modifier = Modifier.fillMaxSize()) {
        itemsIndexed(sections, key = { _, s -> s.sectionId }) { _, section ->
            val title = sectionTitle(section.kind, section.title)
            val current = ui.section?.sectionId == section.sectionId
            val go = stringResource(R.string.section_go, title)
            Row(
                Modifier.fillMaxWidth().heightIn(min = 56.dp)
                    .semantics { selected = current }
                    .clickable(enabled = section.startS != null && ui.audioAvailable, onClickLabel = go, role = Role.Button) {
                        section.startS?.let(actions.seekToSeconds)
                    }
                    .background(if (current) MaterialTheme.colorScheme.secondaryContainer else MaterialTheme.colorScheme.surface)
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                section.startS?.let { Text(clock((it * 1000).toLong()), style = MaterialTheme.typography.labelLarge) }
            }
        }
    }
}

@Composable
fun TermsTab(ui: PlayerUi) {
    val terms = ui.episode.data?.keyTerms.orEmpty()
    if (terms.isEmpty()) {
        EmptyState(stringResource(R.string.terms_empty))
        return
    }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxSize()) {
        itemsIndexed(terms, key = { i, t -> "$i-${t.term}" }) { _, term ->
            Card(Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
                Column(Modifier.padding(16.dp)) {
                    Text(term.term, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
                    term.original?.let { original ->
                        // The original term is Latin: render it LTR so it is not reordered.
                        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
                            Text(stringResource(R.string.term_original, original), style = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.ContentOrRtl))
                        }
                    }
                    term.definition?.let { Text(it, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(top = 4.dp)) }
                }
            }
        }
    }
}

@Composable
fun QuestionsTab(ui: PlayerUi, actions: PlayerActions) {
    val questions = ui.episode.data?.questions.orEmpty()
    if (questions.isEmpty()) {
        EmptyState(stringResource(R.string.questions_empty))
        return
    }
    LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxSize()) {
        itemsIndexed(questions, key = { _, q -> q.questionId }) { index, q -> QuestionCard(index + 1, q, ui, actions) }
    }
}

@Composable
fun QuestionCard(n: Int, question: LearnerQuestion, ui: PlayerUi, actions: PlayerActions) {
    val revealed = ui.revealed[question.questionId]
    var selected by rememberSaveable(question.questionId) { mutableStateOf(revealed?.selectedOption) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Heading(stringResource(R.string.question_n, n))
            Text(question.prompt, style = MaterialTheme.typography.bodyLarge)
            val options = question.options
            if (options != null) {
                Column(Modifier.selectableGroup()) {
                    options.forEachIndexed { i, option ->
                        Row(
                            Modifier.fillMaxWidth().heightIn(min = 48.dp)
                                .selectable(selected = selected == i, enabled = revealed == null, role = Role.RadioButton, onClick = { selected = i }),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = selected == i, onClick = null)
                            Text(option, Modifier.padding(start = 8.dp), style = MaterialTheme.typography.bodyLarge)
                        }
                    }
                }
            }
            if (revealed == null) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (options != null) {
                        OutlinedButton(onClick = { actions.reveal(question, selected) }, enabled = selected != null,
                            modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.question_check)) }
                    }
                    TextButton(onClick = { actions.reveal(question, null) }, modifier = Modifier.heightIn(min = 48.dp)) {
                        Text(stringResource(R.string.question_reveal))
                    }
                }
            } else {
                revealed.correct?.let { ok ->
                    Text(stringResource(if (ok) R.string.question_correct else R.string.question_wrong),
                        color = if (ok) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                }
                Text(stringResource(R.string.question_answer, revealed.answer), style = MaterialTheme.typography.bodyLarge)
                if (question.answerSegmentId != null && ui.audioAvailable) {
                    TextButton(onClick = { actions.listenToAnswer(question) }) { Text(stringResource(R.string.question_listen_answer)) }
                }
            }
        }
    }
}

@Composable
fun BookmarksTab(ui: PlayerUi, actions: PlayerActions, onAdd: () -> Unit) {
    var editing by remember { mutableStateOf<String?>(null) }
    Column(Modifier.fillMaxSize()) {
        if (ui.audioAvailable) {
            TextButton(onClick = onAdd, modifier = Modifier.padding(horizontal = 8.dp).heightIn(min = 48.dp)) {
                Text(stringResource(R.string.player_add_bookmark))
            }
        }
        if (ui.bookmarks.isEmpty()) {
            EmptyState(stringResource(R.string.bookmarks_empty))
            return@Column
        }
        LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            itemsIndexed(ui.bookmarks, key = { _, b -> b.id }) { _, b ->
                Card(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(
                            Modifier.weight(1f).clickable(onClickLabel = stringResource(R.string.transcript_play_here)) { actions.seekToMs(b.positionMs) }
                                .padding(16.dp),
                        ) {
                            Text(stringResource(R.string.bookmark_at, clock(b.positionMs)), style = MaterialTheme.typography.titleMedium)
                            b.sectionTitle?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                            if (b.note.isNotBlank()) Text(b.note, style = MaterialTheme.typography.bodyLarge)
                        }
                        IconButton(onClick = { editing = b.id }) { Icon(Icons.Filled.Edit, stringResource(R.string.bookmark_edit)) }
                        IconButton(onClick = { actions.deleteBookmark(b.id) }) { Icon(Icons.Filled.Delete, stringResource(R.string.bookmark_delete)) }
                    }
                }
            }
        }
    }
    editing?.let { id ->
        val bookmark = ui.bookmarks.firstOrNull { it.id == id }
        if (bookmark != null) {
            NoteDialog(bookmark.note, stringResource(R.string.bookmark_edit), onDismiss = { editing = null }) {
                actions.editBookmark(id, it)
                editing = null
            }
        }
    }
}
