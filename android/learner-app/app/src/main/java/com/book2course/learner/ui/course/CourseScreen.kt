package com.book2course.learner.ui.course

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.automirrored.filled.MenuBook
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.book2course.learner.R
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.api.CourseDetail
import com.book2course.learner.data.api.EpisodeSummary
import com.book2course.learner.data.download.DownloadState
import com.book2course.learner.data.local.EpisodeKey
import com.book2course.learner.data.local.EpisodeProgress
import com.book2course.learner.data.local.LearnerState
import com.book2course.learner.data.local.LocalStore
import com.book2course.learner.data.local.courseProgress
import com.book2course.learner.domain.clock
import com.book2course.learner.domain.spokenDuration
import com.book2course.learner.ui.common.EmptyState
import com.book2course.learner.ui.common.ErrorBanner
import com.book2course.learner.ui.common.LabeledProgress
import com.book2course.learner.ui.common.LearnerScaffold
import com.book2course.learner.ui.common.RefreshBar
import com.book2course.learner.ui.common.sharedEpisode
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@OptIn(ExperimentalCoroutinesApi::class)
class CourseViewModel(
    val courseId: String,
    repository: LearnerRepository,
    private val store: LocalStore,
) : ViewModel() {
    private val trigger = MutableStateFlow(0)
    val course: StateFlow<Loadable<CourseDetail>> = trigger
        .flatMapLatest { repository.course(courseId) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, Loadable(refreshing = true))

    fun refresh() {
        trigger.value++
    }

    fun setCompleted(number: Int, completed: Boolean) {
        viewModelScope.launch { store.setCompleted(EpisodeKey(courseId, number), completed) }
    }

    /** The episode to continue with: the last one played in this course, else the first unfinished. */
    fun resumeTarget(detail: CourseDetail, learner: LearnerState): EpisodeSummary? {
        learner.lastPlayed?.takeIf { it.courseId == courseId }?.let { key ->
            val episode = detail.episodes.firstOrNull { it.number == key.number }
            if (episode != null && learner.progress[key.id]?.completed != true) return episode
        }
        return detail.episodes.firstOrNull { learner.progress["$courseId/${it.number}"]?.completed != true } ?: detail.episodes.firstOrNull()
    }
}

data class EpisodeRowActions(
    val open: (EpisodeSummary) -> Unit,
    val download: (EpisodeSummary) -> Unit,
    val cancelDownload: (EpisodeSummary) -> Unit,
    val deleteDownload: (EpisodeSummary) -> Unit,
    val setCompleted: (EpisodeSummary, Boolean) -> Unit,
)

@Composable
fun CourseScreen(
    course: Loadable<CourseDetail>,
    learner: LearnerState,
    downloads: (EpisodeSummary) -> DownloadState,
    resume: EpisodeSummary?,
    titleFallback: String,
    onBack: () -> Unit,
    onRefresh: () -> Unit,
    onConcepts: () -> Unit,
    actions: EpisodeRowActions,
) {
    val detail = course.data
    LearnerScaffold(title = detail?.title ?: titleFallback, onBack = onBack) {
        RefreshBar(course.refreshing && detail != null)
        course.error?.let { ErrorBanner(it, detail != null, onRefresh) }
        if (detail == null) {
            if (!course.refreshing) EmptyState(stringResource(R.string.error_not_found))
            return@LearnerScaffold
        }
        val numbers = detail.episodes.map { it.number }
        val percent = (courseProgress(learner, detail.courseId, numbers) * 100).toInt()
        LazyColumn(contentPadding = PaddingValues(bottom = 24.dp), modifier = Modifier.fillMaxSize()) {
            item(key = "header") {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(stringResource(R.string.course_progress, percent), style = MaterialTheme.typography.bodyLarge)
                    LabeledProgress(percent / 100f, stringResource(R.string.course_progress, percent))
                    if (resume != null) {
                        val started = (learner.progress["${detail.courseId}/${resume.number}"]?.positionMs ?: 0) > 0
                        Button(onClick = { actions.open(resume) }, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
                            Text(stringResource(if (started) R.string.course_resume else R.string.course_start, resume.label))
                        }
                    }
                    if (detail.conceptsAvailable) {
                        OutlinedButton(onClick = onConcepts, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {
                            Icon(Icons.AutoMirrored.Filled.MenuBook, contentDescription = null)
                            Text(stringResource(R.string.course_concepts), Modifier.padding(start = 8.dp))
                        }
                    }
                }
                HorizontalDivider()
            }
            items(detail.episodes, key = { it.number }) { episode ->
                EpisodeRow(
                    courseId = detail.courseId, episode = episode,
                    progress = learner.progress["${detail.courseId}/${episode.number}"],
                    download = downloads(episode), actions = actions,
                )
                HorizontalDivider()
            }
        }
    }
}

@Composable
fun episodeDescription(episode: EpisodeSummary, progress: EpisodeProgress?, download: DownloadState): String {
    val parts = mutableListOf(stringResource(R.string.episode_number, episode.label) + ": " + episode.title)
    if (episode.kind == "course_intro") parts += stringResource(R.string.episode_intro_kind)
    episode.durationS?.let { parts += spokenDuration((it * 1000).toLong()) }
    parts += when {
        progress?.completed == true -> stringResource(R.string.episode_completed)
        progress != null && progress.positionMs > 0 -> stringResource(R.string.episode_listened, (progress.fraction * 100).toInt())
        else -> stringResource(R.string.episode_not_started)
    }
    when (download) {
        is DownloadState.Done -> parts += stringResource(R.string.episode_downloaded)
        is DownloadState.Running, DownloadState.Queued -> parts += stringResource(R.string.episode_downloading)
        is DownloadState.Failed -> parts += stringResource(R.string.episode_download_failed)
        DownloadState.None -> Unit
    }
    if (!episode.audioAvailable) parts += stringResource(R.string.episode_audio_pending)
    if (!episode.scriptAvailable) parts += stringResource(R.string.episode_script_pending)
    return parts.joinToString("، ")
}

@Composable
fun EpisodeRow(courseId: String, episode: EpisodeSummary, progress: EpisodeProgress?, download: DownloadState, actions: EpisodeRowActions) {
    val description = episodeDescription(episode, progress, download)
    val downloadLabel = stringResource(R.string.action_download)
    val cancelLabel = stringResource(R.string.action_cancel_download)
    val deleteLabel = stringResource(R.string.action_delete_download)
    val doneLabel = stringResource(R.string.action_mark_done)
    val notDoneLabel = stringResource(R.string.action_mark_not_done)
    val openLabel = stringResource(R.string.action_open_episode)
    val customActions = buildList {
        when (download) {
            DownloadState.None, is DownloadState.Failed ->
                if (episode.audioAvailable) add(CustomAccessibilityAction(downloadLabel) { actions.download(episode); true })
            DownloadState.Queued, is DownloadState.Running -> add(CustomAccessibilityAction(cancelLabel) { actions.cancelDownload(episode); true })
            is DownloadState.Done -> add(CustomAccessibilityAction(deleteLabel) { actions.deleteDownload(episode); true })
        }
        if (progress?.completed == true) add(CustomAccessibilityAction(notDoneLabel) { actions.setCompleted(episode, false); true })
        else add(CustomAccessibilityAction(doneLabel) { actions.setCompleted(episode, true); true })
    }
    Row(
        Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable { actions.open(episode) }
            .clearAndSetSemantics {
                contentDescription = description
                onClick(label = openLabel, action = { actions.open(episode); true })
                this.customActions = customActions
            }
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = if (progress?.completed == true) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.primaryContainer,
            contentColor = if (progress?.completed == true) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onPrimaryContainer,
            modifier = Modifier.widthIn(min = 52.dp).sharedEpisode("label-$courseId-${episode.number}"),
        ) {
            Text(episode.label, Modifier.padding(horizontal = 10.dp, vertical = 8.dp), style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold)
        }
        Column(Modifier.weight(1f).padding(horizontal = 12.dp)) {
            Text(episode.title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.sharedEpisode("title-$courseId-${episode.number}"))
            val meta = buildList {
                episode.durationS?.let { add(clock((it * 1000).toLong())) }
                when {
                    progress?.completed == true -> add(stringResource(R.string.episode_completed))
                    progress != null && progress.positionMs > 0 -> add(stringResource(R.string.episode_listened, (progress.fraction * 100).toInt()))
                }
                if (!episode.audioAvailable) add(stringResource(R.string.episode_audio_pending))
            }
            if (meta.isNotEmpty()) Text(meta.joinToString(" · "), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (progress != null && !progress.completed && progress.positionMs > 0) {
                LabeledProgress(progress.fraction, "", Modifier.padding(top = 6.dp))
            }
        }
        if (progress?.completed == true) Icon(Icons.Filled.CheckCircle, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
        DownloadButton(episode, download, actions)
    }
}

/** Visible shortcut for sighted users; TalkBack users get the same actions on the row (custom actions). */
@Composable
private fun DownloadButton(episode: EpisodeSummary, download: DownloadState, actions: EpisodeRowActions) {
    val modifier = Modifier.size(48.dp).clearAndSetSemantics { }
    when (download) {
        DownloadState.None, is DownloadState.Failed -> if (episode.audioAvailable) {
            IconButton(onClick = { actions.download(episode) }, modifier = modifier) { Icon(Icons.Filled.Download, null) }
        }
        DownloadState.Queued -> IconButton(onClick = { actions.cancelDownload(episode) }, modifier = modifier) { Icon(Icons.Filled.Close, null) }
        is DownloadState.Running -> IconButton(onClick = { actions.cancelDownload(episode) }, modifier = modifier) {
            val f = download.fraction
            if (f != null) CircularProgressIndicator(progress = { f }, modifier = Modifier.size(24.dp))
            else CircularProgressIndicator(Modifier.size(24.dp))
        }
        is DownloadState.Done -> IconButton(onClick = { actions.deleteDownload(episode) }, modifier = modifier) {
            Icon(Icons.Filled.DownloadDone, null)
        }
    }
}
