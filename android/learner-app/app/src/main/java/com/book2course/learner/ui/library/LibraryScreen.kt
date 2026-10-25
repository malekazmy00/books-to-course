package com.book2course.learner.ui.library

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.book2course.learner.R
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.api.CourseList
import com.book2course.learner.data.api.CourseSummary
import com.book2course.learner.data.local.LearnerState
import com.book2course.learner.domain.spokenDuration
import com.book2course.learner.ui.common.EmptyState
import com.book2course.learner.ui.common.ErrorBanner
import com.book2course.learner.ui.common.LabeledProgress
import com.book2course.learner.ui.common.LearnerScaffold
import com.book2course.learner.ui.common.RefreshBar
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn

@OptIn(ExperimentalCoroutinesApi::class)
class LibraryViewModel(repository: LearnerRepository) : ViewModel() {
    private val trigger = MutableStateFlow(0)
    val courses: StateFlow<Loadable<CourseList>> = trigger
        .flatMapLatest { repository.courses() }
        .stateIn(viewModelScope, SharingStarted.Eagerly, Loadable(refreshing = true))

    fun refresh() {
        trigger.value++
    }
}

/** Course completion from local progress only (works offline). */
fun courseFraction(learner: LearnerState, course: CourseSummary): Float {
    if (course.episodeCount <= 0) return 0f
    val prefix = course.courseId + "/"
    val sum = learner.progress.filterKeys { it.startsWith(prefix) }.values.sumOf { it.fraction.toDouble() }
    return (sum / course.episodeCount).toFloat().coerceIn(0f, 1f)
}

data class ContinueItem(val courseTitle: String, val label: String, val title: String, val remainingMs: Long)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(
    courses: Loadable<CourseList>,
    learner: LearnerState,
    continueItem: ContinueItem?,
    onOpenCourse: (CourseSummary) -> Unit,
    onContinue: () -> Unit,
    onRefresh: () -> Unit,
    onSettings: () -> Unit,
) {
    LearnerScaffold(
        title = stringResource(R.string.library_title), onBack = null,
        actions = {
            IconButton(onClick = onSettings) { Icon(Icons.Filled.Settings, contentDescription = stringResource(R.string.settings)) }
        },
    ) {
        RefreshBar(courses.refreshing && courses.data != null)
        courses.error?.let { ErrorBanner(it, courses.data != null, onRefresh) }
        PullToRefreshBox(isRefreshing = courses.isInitialLoading, onRefresh = onRefresh, modifier = Modifier.fillMaxSize()) {
            LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxSize()) {
                if (continueItem != null) item(key = "continue") { ContinueCard(continueItem, onContinue) }
                val list = courses.data?.courses.orEmpty()
                if (list.isEmpty() && !courses.refreshing && courses.error == null) {
                    item(key = "empty") { EmptyState(stringResource(R.string.library_empty)) }
                }
                items(list, key = { it.courseId }) { course -> CourseCard(course, courseFraction(learner, course)) { onOpenCourse(course) } }
            }
        }
    }
}

@Composable
private fun ContinueCard(item: ContinueItem, onOpen: () -> Unit) {
    val description = stringResource(R.string.library_continue_desc, item.courseTitle, item.label, item.title, spokenDuration(item.remainingMs))
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
        modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable(onClick = onOpen)
            .clearAndSetSemantics { contentDescription = description; onClick(label = null, action = { onOpen(); true }) },
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(stringResource(R.string.library_continue), style = MaterialTheme.typography.labelLarge)
            Text("${item.label} · ${item.title}", style = MaterialTheme.typography.titleMedium)
            Text(item.courseTitle, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun CourseCard(course: CourseSummary, fraction: Float, onOpen: () -> Unit) {
    val percent = (fraction * 100).toInt()
    val description = stringResource(R.string.course_card_desc, course.title, course.episodeCount, percent)
    Card(
        modifier = Modifier.fillMaxWidth().heightIn(min = 72.dp).clickable(onClick = onOpen)
            .clearAndSetSemantics { contentDescription = description; onClick(label = null, action = { onOpen(); true }) },
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(course.title, style = MaterialTheme.typography.titleLarge)
            Text(stringResource(R.string.episodes_count, course.episodeCount), style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(8.dp))
            LabeledProgress(fraction, stringResource(R.string.course_progress, percent))
        }
    }
}
