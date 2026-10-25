package com.book2course.learner.ui.player

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.CourseDetail
import com.book2course.learner.data.api.EpisodeDetail
import com.book2course.learner.data.api.LearnerQuestion
import com.book2course.learner.data.api.Section
import com.book2course.learner.data.api.Segment
import com.book2course.learner.data.local.Bookmark
import com.book2course.learner.data.local.EpisodeKey
import com.book2course.learner.data.local.LearnerSettings
import com.book2course.learner.data.local.LocalStore
import com.book2course.learner.data.local.RevealedAnswer
import com.book2course.learner.data.local.revealKey
import com.book2course.learner.domain.Timeline
import com.book2course.learner.playback.AnswerGate
import com.book2course.learner.playback.PlayResult
import com.book2course.learner.playback.PlaybackCoordinator
import com.book2course.learner.playback.PlaybackState
import com.book2course.learner.playback.SleepMode
import com.book2course.learner.playback.SleepState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** Everything the player screen draws, derived in one place so the UI stays a pure function. */
data class PlayerUi(
    val episode: Loadable<EpisodeDetail> = Loadable(refreshing = true),
    val isCurrent: Boolean = false,
    val playback: PlaybackState = PlaybackState(),
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val section: Section? = null,
    val segment: Segment? = null,
    val settings: LearnerSettings = LearnerSettings(),
    val revealed: Map<String, RevealedAnswer> = emptyMap(),
    val bookmarks: List<Bookmark> = emptyList(),
    val sleep: SleepState = SleepState(),
    val gate: AnswerGate? = null,
    val message: PlayerMessage? = null,
) {
    val isPlaying: Boolean get() = isCurrent && playback.isPlaying
    val audioAvailable: Boolean get() = episode.data?.audio?.available == true
}

sealed interface PlayerMessage {
    data object NoAudio : PlayerMessage
    data class Error(val error: AppError) : PlayerMessage
}

class PlayerViewModel(
    val courseId: String,
    val number: Int,
    private val repository: LearnerRepository,
    private val store: LocalStore,
    private val playback: PlaybackCoordinator,
) : ViewModel() {
    val key = EpisodeKey(courseId, number)
    private val course = MutableStateFlow<CourseDetail?>(null)
    private val episode = MutableStateFlow<Loadable<EpisodeDetail>>(Loadable(refreshing = true))
    private val message = MutableStateFlow<PlayerMessage?>(null)
    private val lastSeekMs = MutableStateFlow<Long?>(null)

    init {
        viewModelScope.launch {
            repository.episode(courseId, number).collect { value ->
                episode.value = value
                value.data?.let(playback::updateEpisode)
            }
        }
        viewModelScope.launch { repository.course(courseId).collect { if (it.data != null) course.value = it.data } }
    }

    private val base = combine(episode, playback.playback, playback.nowPlaying, store.state, lastSeekMs) { ep, pb, now, learner, seek ->
        val current = now?.key == key
        val saved = learner.progress[key.id]
        val position = when {
            current -> pb.positionMs
            seek != null -> seek
            else -> saved?.positionMs ?: 0
        }
        val duration = when {
            current && pb.durationMs > 0 -> pb.durationMs
            else -> saved?.durationMs?.takeIf { it > 0 } ?: ep.data?.durationS?.let { (it * 1000).toLong() } ?: 0
        }
        val detail = ep.data
        val positionS = position / 1000.0
        PlayerUi(
            episode = ep, isCurrent = current, playback = pb, positionMs = position, durationMs = duration,
            section = detail?.let { Timeline.sectionAt(it, positionS) },
            segment = detail?.let { Timeline.segmentAt(it, positionS) },
            settings = learner.settings,
            revealed = learner.revealed.filterKeys { it.startsWith("${key.courseId}/${key.number}/") }
                .mapKeys { it.key.substringAfterLast('/') },
            bookmarks = learner.bookmarks.filter { it.courseId == courseId && it.number == number }.sortedBy { it.positionMs },
        )
    }

    val ui: StateFlow<PlayerUi> = combine(base, playback.sleep, playback.gate, message) { b, sleep, gate, msg ->
        b.copy(sleep = sleep, gate = gate?.takeIf { it.key == key }, message = msg)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, PlayerUi())

    fun togglePlay() {
        val state = ui.value
        if (state.isCurrent) return playback.toggle()
        val detail = course.value
        if (detail == null) {
            message.value = PlayerMessage.Error(AppError.Offline)
            return
        }
        when (playback.playEpisode(detail, number, lastSeekMs.value)) {
            PlayResult.STARTED -> message.value = null
            PlayResult.NO_AUDIO -> message.value = PlayerMessage.NoAudio
            PlayResult.NOT_CONFIGURED -> message.value = PlayerMessage.Error(AppError.NotConfigured)
        }
    }

    fun skip(back: Boolean) {
        val s = ui.value.settings
        val seconds = if (back) -s.skipBackSeconds else s.skipForwardSeconds
        if (ui.value.isCurrent) playback.seekBy(seconds) else seekToMs(ui.value.positionMs + seconds * 1000L)
    }

    fun seekToMs(ms: Long) {
        val target = ms.coerceIn(0, ui.value.durationMs.takeIf { it > 0 } ?: Long.MAX_VALUE)
        if (ui.value.isCurrent) playback.seekToSeconds(target / 1000.0) else lastSeekMs.value = target
    }

    fun seekToSeconds(seconds: Double) = seekToMs((seconds * 1000).toLong())

    fun nextSection() {
        val detail = episode.value.data ?: return
        val next = Timeline.nextSectionStart(detail, ui.value.positionMs / 1000.0) ?: return
        seekToSeconds(next)
    }

    fun previousSection() {
        val detail = episode.value.data ?: return
        seekToSeconds(Timeline.previousSectionStart(detail, ui.value.positionMs / 1000.0))
    }

    fun setSpeed(speed: Float) = playback.setSpeed(speed)

    fun setSleep(mode: SleepMode) = playback.setSleep(mode)

    fun continueAfterGate() = playback.continueAfterGate()

    fun addBookmark(note: String) {
        val position = ui.value.positionMs
        val section = ui.value.section?.title
        viewModelScope.launch { store.addBookmark(key, position, section, note) }
    }

    fun editBookmark(id: String, note: String) {
        viewModelScope.launch { store.editBookmark(id, note) }
    }

    fun deleteBookmark(id: String) {
        viewModelScope.launch { store.deleteBookmark(id) }
    }

    fun updateSettings(transform: (LearnerSettings) -> LearnerSettings) {
        viewModelScope.launch { store.updateSettings(transform) }
    }

    /** Questions mode: the answer comes from the server only now, on the learner's request. */
    fun reveal(question: LearnerQuestion, selectedOption: Int?) {
        viewModelScope.launch {
            repository.reveal(courseId, number, question.questionId, selectedOption)
                .onSuccess { r ->
                    store.saveReveal(revealKey(courseId, number, question.questionId), RevealedAnswer(r.answer, r.correct, selectedOption))
                    message.value = null
                }
                .onFailure { e -> message.value = PlayerMessage.Error((e as? AppErrorException)?.error ?: AppError.Unexpected(null)) }
        }
    }

    fun revealAtGate() {
        val gate = ui.value.gate ?: return
        val question = episode.value.data?.questions?.firstOrNull { it.questionId == gate.questionId }
        if (question != null) reveal(question, null)
    }

    /** Plays the spoken answer of a revealed question. */
    fun listenToAnswer(question: LearnerQuestion) {
        val detail = episode.value.data ?: return
        val start = question.answerSegmentId?.let { Timeline.segmentById(detail, it)?.startS } ?: return
        seekToSeconds(start)
        if (!ui.value.isPlaying) togglePlay()
    }

    fun dismissMessage() {
        message.value = null
    }

    fun refresh() {
        viewModelScope.launch {
            repository.episode(courseId, number).collect { value ->
                episode.value = value
                value.data?.let(playback::updateEpisode)
            }
        }
    }

}
