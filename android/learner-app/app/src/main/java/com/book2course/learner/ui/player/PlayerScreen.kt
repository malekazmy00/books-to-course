package com.book2course.learner.ui.player

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.NavigateBefore
import androidx.compose.material.icons.automirrored.filled.NavigateNext
import androidx.compose.material.icons.filled.BookmarkAdd
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.FastForward
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.FastRewind
import androidx.compose.material.icons.filled.Speed
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.PrimaryScrollableTabRow
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.book2course.learner.R
import com.book2course.learner.data.api.LearnerQuestion
import com.book2course.learner.data.local.LearnerSettings
import com.book2course.learner.domain.clock
import com.book2course.learner.domain.spokenDuration
import com.book2course.learner.playback.SleepMode
import com.book2course.learner.ui.common.ErrorBanner
import com.book2course.learner.ui.common.LearnerScaffold
import com.book2course.learner.ui.common.RefreshBar
import com.book2course.learner.ui.common.VoiceChip
import com.book2course.learner.ui.common.message
import com.book2course.learner.ui.common.sectionTitle
import com.book2course.learner.ui.common.sharedEpisode

/** Callbacks of the player screen (kept together so tests can pass no-ops). */
data class PlayerActions(
    val togglePlay: () -> Unit = {},
    val skip: (back: Boolean) -> Unit = {},
    val seekToMs: (Long) -> Unit = {},
    val seekToSeconds: (Double) -> Unit = {},
    val previousSection: () -> Unit = {},
    val nextSection: () -> Unit = {},
    val setSpeed: (Float) -> Unit = {},
    val setSleep: (SleepMode) -> Unit = {},
    val continueAfterGate: () -> Unit = {},
    val revealAtGate: () -> Unit = {},
    val reveal: (LearnerQuestion, Int?) -> Unit = { _, _ -> },
    val listenToAnswer: (LearnerQuestion) -> Unit = {},
    val addBookmark: (String) -> Unit = {},
    val editBookmark: (String, String) -> Unit = { _, _ -> },
    val deleteBookmark: (String) -> Unit = {},
    val updateSettings: ((LearnerSettings) -> LearnerSettings) -> Unit = {},
    val dismissMessage: () -> Unit = {},
    val refresh: () -> Unit = {},
)

enum class PlayerTab(val title: Int) {
    TRANSCRIPT(R.string.tab_transcript), SECTIONS(R.string.tab_sections), TERMS(R.string.tab_terms),
    QUESTIONS(R.string.tab_questions), BOOKMARKS(R.string.tab_bookmarks),
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PlayerScreen(
    courseId: String,
    number: Int,
    label: String,
    fallbackTitle: String,
    ui: PlayerUi,
    actions: PlayerActions,
    onBack: () -> Unit,
    initialTab: PlayerTab = PlayerTab.TRANSCRIPT,
) {
    val detail = ui.episode.data
    var tab by rememberSaveable { mutableStateOf(initialTab) }
    var bookmarkDialog by remember { mutableStateOf(false) }
    LearnerScaffold(title = stringResource(R.string.episode_number, detail?.label ?: label), onBack = onBack) {
        RefreshBar(ui.episode.refreshing && detail != null)
        ui.episode.error?.let { ErrorBanner(it, detail != null, actions.refresh) }
        Header(courseId, number, detail?.label ?: label, detail?.title ?: fallbackTitle, ui)
        ui.message?.let { msg ->
            val text = when (msg) {
                is PlayerMessage.NoAudio -> stringResource(R.string.player_no_audio)
                is PlayerMessage.Error -> stringResource(msg.error.message(hasData = detail != null))
            }
            Text(text, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(horizontal = 16.dp)
                .semantics { liveRegion = LiveRegionMode.Polite })
        }
        if (detail != null && !ui.audioAvailable) {
            Text(stringResource(R.string.player_no_audio), Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        AnimatedVisibility(ui.gate != null) { GateCard(actions) }
        if (ui.audioAvailable) {
            SeekBar(ui, actions)
            Controls(ui, actions)
            SecondaryControls(ui, actions, onBookmark = { bookmarkDialog = true })
        }
        PrimaryScrollableTabRow(selectedTabIndex = tab.ordinal, edgePadding = 8.dp) {
            PlayerTab.entries.forEach { t ->
                Tab(selected = tab == t, onClick = { tab = t }, text = { Text(stringResource(t.title)) },
                    modifier = Modifier.heightIn(min = 48.dp))
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            when (tab) {
                PlayerTab.TRANSCRIPT -> TranscriptTab(ui, actions)
                PlayerTab.SECTIONS -> SectionsTab(ui, actions)
                PlayerTab.TERMS -> TermsTab(ui)
                PlayerTab.QUESTIONS -> QuestionsTab(ui, actions)
                PlayerTab.BOOKMARKS -> BookmarksTab(ui, actions, onAdd = { bookmarkDialog = true })
            }
        }
    }
    if (bookmarkDialog) {
        NoteDialog(initial = "", title = stringResource(R.string.player_add_bookmark), onDismiss = { bookmarkDialog = false }) {
            actions.addBookmark(it)
            bookmarkDialog = false
        }
    }
}

@Composable
private fun Header(courseId: String, number: Int, label: String, title: String, ui: PlayerUi) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.widthIn(min = 52.dp).sharedEpisode("label-$courseId-$number")) {
            Text(label, Modifier.padding(horizontal = 10.dp, vertical = 8.dp), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        }
        Column(Modifier.weight(1f).padding(start = 12.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.sharedEpisode("title-$courseId-$number"))
            // Section and voice changes are announced politely while listening (D-032: voices are declared).
            val section = ui.section
            val segment = ui.segment
            if (ui.isCurrent && section != null) {
                Row(
                    Modifier.semantics { liveRegion = LiveRegionMode.Polite }.padding(top = 4.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(stringResource(R.string.player_now_section, sectionTitle(section.kind, section.title)),
                        style = MaterialTheme.typography.bodyMedium)
                    if (segment != null) VoiceChip(segment.voice, segment.sourceLabel)
                }
            }
        }
    }
}

@Composable
private fun GateCard(actions: PlayerActions) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)) {
        Column(Modifier.padding(12.dp)) {
            Text(stringResource(R.string.player_gate), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(onClick = actions.revealAtGate) { Text(stringResource(R.string.player_gate_reveal)) }
                TextButton(onClick = actions.continueAfterGate) { Text(stringResource(R.string.player_gate_continue)) }
            }
        }
    }
}

@Composable
private fun SeekBar(ui: PlayerUi, actions: PlayerActions) {
    val duration = ui.durationMs.coerceAtLeast(1)
    var dragging by remember { mutableStateOf<Float?>(null) }
    val position = dragging?.let { (it * duration).toLong() } ?: ui.positionMs
    val state = stringResource(R.string.player_position, spokenDuration(position), spokenDuration(ui.durationMs))
    val backLabel = stringResource(R.string.player_back, ui.settings.skipBackSeconds)
    val forwardLabel = stringResource(R.string.player_forward, ui.settings.skipForwardSeconds)
    val seekLabel = stringResource(R.string.player_seek)
    Column(Modifier.padding(horizontal = 16.dp)) {
        Slider(
            value = (position.toFloat() / duration).coerceIn(0f, 1f),
            onValueChange = { dragging = it },
            onValueChangeFinished = {
                dragging?.let { actions.seekToMs((it * duration).toLong()) }
                dragging = null
            },
            modifier = Modifier.fillMaxWidth().semantics {
                contentDescription = seekLabel
                stateDescription = state
                customActions = listOf(
                    CustomAccessibilityAction(backLabel) { actions.skip(true); true },
                    CustomAccessibilityAction(forwardLabel) { actions.skip(false); true },
                )
            },
        )
        Row(Modifier.fillMaxWidth()) {
            Text(clock(position), style = MaterialTheme.typography.labelMedium, modifier = Modifier.semantics { contentDescription = "" })
            Box(Modifier.weight(1f))
            Text(clock(ui.durationMs), style = MaterialTheme.typography.labelMedium, modifier = Modifier.semantics { contentDescription = "" })
        }
    }
}

@Composable
private fun Controls(ui: PlayerUi, actions: PlayerActions) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly,
        verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = actions.previousSection, modifier = Modifier.size(56.dp)) {
            Icon(Icons.AutoMirrored.Filled.NavigateBefore, stringResource(R.string.player_prev_section))
        }
        IconButton(onClick = { actions.skip(true) }, modifier = Modifier.size(56.dp)) {
            Icon(Icons.Filled.FastRewind, stringResource(R.string.player_back, ui.settings.skipBackSeconds))
        }
        FilledIconButton(onClick = actions.togglePlay, modifier = Modifier.size(76.dp),
            colors = IconButtonDefaults.filledIconButtonColors()) {
            Icon(if (ui.isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                stringResource(if (ui.isPlaying) R.string.player_pause else R.string.player_play), Modifier.size(40.dp))
        }
        IconButton(onClick = { actions.skip(false) }, modifier = Modifier.size(56.dp)) {
            Icon(Icons.Filled.FastForward, stringResource(R.string.player_forward, ui.settings.skipForwardSeconds))
        }
        IconButton(onClick = actions.nextSection, modifier = Modifier.size(56.dp)) {
            Icon(Icons.AutoMirrored.Filled.NavigateNext, stringResource(R.string.player_next_section))
        }
    }
}

@Composable
private fun SecondaryControls(ui: PlayerUi, actions: PlayerActions, onBookmark: () -> Unit) {
    var speedMenu by remember { mutableStateOf(false) }
    var sleepMenu by remember { mutableStateOf(false) }
    val speedText = formatSpeed(if (ui.isCurrent) ui.playback.speed else ui.settings.speed)
    Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.SpaceEvenly) {
        Box {
            TextButton(onClick = { speedMenu = true }, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Filled.Speed, null)
                Text(stringResource(R.string.player_speed, speedText), Modifier.padding(start = 4.dp))
            }
            DropdownMenu(expanded = speedMenu, onDismissRequest = { speedMenu = false }) {
                LearnerSettings.SPEEDS.forEach { s ->
                    DropdownMenuItem(text = { Text(formatSpeed(s) + "×") }, onClick = { actions.setSpeed(s); speedMenu = false })
                }
            }
        }
        Box {
            val sleepText = sleepLabel(ui)
            TextButton(onClick = { sleepMenu = true }, modifier = Modifier.heightIn(min = 48.dp)) {
                Icon(Icons.Filled.Bedtime, null)
                Text(sleepText, Modifier.padding(start = 4.dp))
            }
            DropdownMenu(expanded = sleepMenu, onDismissRequest = { sleepMenu = false }) {
                listOf(10, 15, 30, 45, 60).forEach { m ->
                    DropdownMenuItem(text = { Text(stringResource(R.string.player_sleep_minutes, m)) },
                        onClick = { actions.setSleep(SleepMode.Minutes(m, 0)); sleepMenu = false })
                }
                DropdownMenuItem(text = { Text(stringResource(R.string.player_sleep_section)) },
                    onClick = { actions.setSleep(SleepMode.EndOfSection); sleepMenu = false })
                DropdownMenuItem(text = { Text(stringResource(R.string.player_sleep_episode)) },
                    onClick = { actions.setSleep(SleepMode.EndOfEpisode); sleepMenu = false })
                if (ui.sleep.mode != SleepMode.Off) {
                    DropdownMenuItem(text = { Text(stringResource(R.string.player_sleep_off)) },
                        onClick = { actions.setSleep(SleepMode.Off); sleepMenu = false })
                }
            }
        }
        TextButton(onClick = onBookmark, modifier = Modifier.heightIn(min = 48.dp)) {
            Icon(Icons.Filled.BookmarkAdd, null)
            Text(stringResource(R.string.player_add_bookmark), Modifier.padding(start = 4.dp))
        }
    }
}

@Composable
private fun sleepLabel(ui: PlayerUi): String = when (val mode = ui.sleep.mode) {
    SleepMode.Off -> stringResource(R.string.player_sleep)
    is SleepMode.Minutes -> stringResource(R.string.player_sleep_on, clock(ui.sleep.remainingMs ?: 0))
    SleepMode.EndOfSection -> stringResource(R.string.player_sleep_on, stringResource(R.string.player_sleep_section))
    SleepMode.EndOfEpisode -> stringResource(R.string.player_sleep_on, stringResource(R.string.player_sleep_episode))
}

fun formatSpeed(speed: Float): String = if (speed % 1f == 0f) "%.1f".format(java.util.Locale.ROOT, speed) else "%.2f".format(java.util.Locale.ROOT, speed).trimEnd('0')

@Composable
fun NoteDialog(initial: String, title: String, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    var text by rememberSaveable { mutableStateOf(initial) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            OutlinedTextField(value = text, onValueChange = { text = it }, label = { Text(stringResource(R.string.bookmark_note)) },
                modifier = Modifier.fillMaxWidth(), minLines = 3)
        },
        confirmButton = { TextButton(onClick = { onSave(text) }) { Text(stringResource(R.string.bookmark_save)) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel)) } },
    )
}
