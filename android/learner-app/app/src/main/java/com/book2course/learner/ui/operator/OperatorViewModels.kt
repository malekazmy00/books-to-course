package com.book2course.learner.ui.operator

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberUpdatedState
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ViewModel
import android.content.ContentResolver
import android.net.Uri
import com.book2course.learner.data.WorkflowActionsRepository
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewModelScope
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.OperatorRepository
import com.book2course.learner.data.api.BookList
import com.book2course.learner.data.api.BookStages
import com.book2course.learner.data.api.StageDetail
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** What an operator screen shows: the data (cached first) and whether the *user* asked for a refresh. */
data class OperatorUi<out T>(val loadable: Loadable<T> = Loadable(refreshing = true), val userRefreshing: Boolean = false) {
    val data: T? get() = loadable.data
    /** A visible indicator only for a first load without data or a pull-to-refresh, never for the background tick. */
    val showRefreshing: Boolean get() = userRefreshing || loadable.isInitialLoading
}

/**
 * Cache-first state for one operator screen. [refresh] is the user's pull-to-refresh; [refreshSilently]
 * is the 15 s tick while the screen is visible: it keeps the data on screen and shows no indicator.
 */
open class OperatorScreenViewModel<T>(private val source: () -> Flow<Loadable<T>>) : ViewModel() {
    private val _ui = MutableStateFlow(OperatorUi<T>())
    val ui: StateFlow<OperatorUi<T>> = _ui.asStateFlow()
    private var job: Job? = null

    init {
        load(user = false)
    }

    fun refresh() = load(user = true)

    fun refreshSilently() {
        if (job?.isActive == true) return
        load(user = false)
    }

    private fun load(user: Boolean) {
        job?.cancel()
        job = viewModelScope.launch {
            source().collect { next ->
                val previous = _ui.value.loadable
                val merged = Loadable(data = next.data ?: previous.data, refreshing = next.refreshing, error = next.error)
                _ui.value = OperatorUi(merged, userRefreshing = user && next.refreshing)
            }
        }
    }
}

class OperatorLibraryViewModel(repository: OperatorRepository) :
    OperatorScreenViewModel<BookList>({ repository.books() })

class BookStagesViewModel(repository: OperatorRepository, courseId: String, bookId: String) :
    OperatorScreenViewModel<BookStages>({ repository.stages(courseId, bookId) })

class StageDetailViewModel(repository: OperatorRepository, courseId: String, bookId: String, stageId: String) :
    OperatorScreenViewModel<StageDetail>({ repository.stage(courseId, bookId, stageId) })

const val AUTO_REFRESH_MS = 15_000L

/** Calls [onTick] every [intervalMs] while this destination is RESUMED (visible), and never otherwise. */
@Composable
fun AutoRefresh(intervalMs: Long = AUTO_REFRESH_MS, onTick: () -> Unit) {
    val owner = LocalLifecycleOwner.current
    val tick by rememberUpdatedState(onTick)
    LaunchedEffect(owner, intervalMs) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                delay(intervalMs)
                tick()
            }
        }
    }
}


enum class StageAction { PAUSE, RESUME_OR_RETRY }

data class StageActionUi(val busy: Boolean = false, val message: String? = null, val error: Boolean = false)

class StageActionsViewModel(
    private val repository: WorkflowActionsRepository,
    private val contentResolver: ContentResolver,
) : ViewModel() {
    private val _state = MutableStateFlow(StageActionUi())
    val state: StateFlow<StageActionUi> = _state.asStateFlow()

    fun control(action: StageAction, courseId: String, bookId: String, workflowId: String) {
        if (_state.value.busy) return
        viewModelScope.launch {
            _state.value = StageActionUi(busy = true)
            try {
                val message = when (action) {
                    StageAction.PAUSE -> repository.pause(workflowId)
                    StageAction.RESUME_OR_RETRY -> repository.resumeOrRetry(courseId, bookId, workflowId)
                }
                _state.value = StageActionUi(message = message)
            } catch (e: Exception) {
                _state.value = StageActionUi(message = e.message ?: "تعذر تنفيذ الطلب.", error = true)
            }
        }
    }

    fun startBookTranslation(courseId: String, bookId: String, sourceLanguage: String, targetLanguage: String, onStarted: () -> Unit) {
        if (_state.value.busy) return
        viewModelScope.launch {
            _state.value = StageActionUi(busy = true)
            try {
                val message = repository.startBookTranslation(courseId, bookId, sourceLanguage, targetLanguage)
                _state.value = StageActionUi(message = message)
                onStarted()
            } catch (e: Exception) {
                _state.value = StageActionUi(message = e.message ?: "تعذر بدء الترجمة.", error = true)
            }
        }
    }

    fun export(courseId: String, workflowId: String?, audio: Boolean, destination: Uri) {
        if (_state.value.busy) return
        viewModelScope.launch {
            _state.value = StageActionUi(busy = true)
            try {
                val message = if (audio) {
                    val id = workflowId ?: throw IllegalStateException("لا يوجد مسار صوت مرتبط بهذه المرحلة.")
                    repository.exportAudio(courseId, id, contentResolver, destination)
                } else repository.exportResults(courseId, contentResolver, destination)
                _state.value = StageActionUi(message = message)
            } catch (e: Exception) {
                _state.value = StageActionUi(message = e.message ?: "تعذر حفظ التصدير.", error = true)
            }
        }
    }

    fun clearMessage() { _state.value = _state.value.copy(message = null, error = false) }
}
