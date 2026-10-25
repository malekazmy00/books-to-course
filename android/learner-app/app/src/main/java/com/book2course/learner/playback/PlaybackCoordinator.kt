package com.book2course.learner.playback

import com.book2course.learner.data.api.CourseDetail
import com.book2course.learner.data.api.EpisodeDetail
import com.book2course.learner.data.download.EpisodeFiles
import com.book2course.learner.data.local.EpisodeKey
import com.book2course.learner.data.local.LocalStore
import com.book2course.learner.data.local.resumePositionMs
import com.book2course.learner.domain.Timeline
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class NowPlaying(
    val key: EpisodeKey,
    val courseTitle: String,
    val label: String,
    val title: String,
    val episode: EpisodeDetail? = null,
)

sealed interface SleepMode {
    data object Off : SleepMode
    data class Minutes(val minutes: Int, val endsAtMs: Long) : SleepMode
    data object EndOfSection : SleepMode
    data object EndOfEpisode : SleepMode
}

data class SleepState(val mode: SleepMode = SleepMode.Off, val remainingMs: Long? = null)

/** Paused before a hidden answer (questions mode): the learner reveals it or continues. */
data class AnswerGate(val key: EpisodeKey, val atS: Double, val questionId: String?)

enum class PlayResult { STARTED, NO_AUDIO, NOT_CONFIGURED }

/**
 * App-scoped playback logic that must keep working with the screen closed: queue building
 * (downloaded file first, else stream), resume, progress recording, sleep timer, section skips
 * and the questions-mode pause before answers.
 */
class PlaybackCoordinator(
    private val scope: CoroutineScope,
    private val player: PlayerConnection,
    private val store: LocalStore,
    private val files: EpisodeFiles,
    private val episodeLoader: suspend (String, Int) -> EpisodeDetail?,
    private val baseUrl: () -> String?,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val _nowPlaying = MutableStateFlow<NowPlaying?>(null)
    val nowPlaying: StateFlow<NowPlaying?> = _nowPlaying.asStateFlow()
    private val _sleep = MutableStateFlow(SleepState())
    val sleep: StateFlow<SleepState> = _sleep.asStateFlow()
    private val _gate = MutableStateFlow<AnswerGate?>(null)
    val gate: StateFlow<AnswerGate?> = _gate.asStateFlow()
    val playback: StateFlow<PlaybackState> get() = player.state

    private var queueTitles: Map<String, NowPlaying> = emptyMap()
    private var lastMediaId: String? = null
    private var lastPositionMs = 0L
    private var lastDurationMs = 0L
    private var lastSavedAtMs = 0L
    private var lastSavedPositionMs = -1L
    private var sleepJob: Job? = null
    private var sleepSectionEndS: Double? = null

    init {
        scope.launch { player.state.collect(::onState) }
    }

    fun audioUri(courseId: String, number: Int, label: String): String? {
        files.downloaded(courseId, label)?.let { return it.toURI().toString() }
        val base = baseUrl() ?: return null
        return "${base.trimEnd('/')}/v3/learner/courses/$courseId/episodes/$number/audio"
    }

    /** Plays [number] of [course]; the queue continues with the following episodes that have audio. */
    fun playEpisode(course: CourseDetail, number: Int, startMs: Long? = null): PlayResult {
        if (baseUrl() == null) return PlayResult.NOT_CONFIGURED
        val playable = course.episodes.filter { it.audioAvailable || files.downloaded(course.courseId, it.label) != null }
        val index = playable.indexOfFirst { it.number == number }
        if (index < 0) return PlayResult.NO_AUDIO
        val items = playable.drop(index).mapNotNull { e ->
            audioUri(course.courseId, e.number, e.label)?.let { EpisodeMedia(EpisodeKey(course.courseId, e.number), it, e.title, course.title, e.label) }
        }
        queueTitles = items.associate { mediaIdOf(it.key) to NowPlaying(it.key, it.courseTitle, it.label, it.title) }
        val key = EpisodeKey(course.courseId, number)
        val start = startMs ?: resumePositionMs(store.state.value.progress[key.id])
        _gate.value = null
        player.setSpeed(store.state.value.settings.speed)
        player.setQueue(items, 0, start, playWhenReady = true)
        select(mediaIdOf(key))
        return PlayResult.STARTED
    }

    /** Shows an episode (transcript, questions) without audio, e.g. while its audio is pending. */
    fun show(course: CourseDetail, number: Int) {
        val e = course.episodes.firstOrNull { it.number == number } ?: return
        val key = EpisodeKey(course.courseId, number)
        if (_nowPlaying.value?.key == key) return
        queueTitles = queueTitles + (mediaIdOf(key) to NowPlaying(key, course.title, e.label, e.title))
    }

    private fun select(mediaId: String) {
        val base = queueTitles[mediaId] ?: return
        val current = _nowPlaying.value
        if (current?.key == base.key) return
        _nowPlaying.value = base
        scope.launch {
            val detail = runCatching { episodeLoader(base.key.courseId, base.key.number) }.getOrNull()
            if (_nowPlaying.value?.key == base.key) _nowPlaying.value = _nowPlaying.value?.copy(episode = detail)
        }
    }

    fun updateEpisode(detail: EpisodeDetail) {
        val now = _nowPlaying.value ?: return
        if (now.key == EpisodeKey(detail.courseId, detail.number)) _nowPlaying.value = now.copy(episode = detail)
    }

    fun toggle() {
        val s = player.state.value
        if (s.isPlaying) player.pause() else {
            _gate.value = null
            player.play()
        }
    }

    fun pause() = player.pause()

    fun seekBy(seconds: Int) {
        val s = player.state.value
        val target = (s.positionMs + seconds * 1000L).coerceAtLeast(0)
        player.seekTo(if (s.durationMs > 0) target.coerceAtMost(s.durationMs) else target)
        lastPositionMs = target
    }

    fun seekToSeconds(seconds: Double) {
        val ms = (seconds * 1000).toLong()
        player.seekTo(ms)
        lastPositionMs = ms
    }

    fun nextSection(): Boolean {
        val episode = _nowPlaying.value?.episode ?: return false
        val next = Timeline.nextSectionStart(episode, player.state.value.positionMs / 1000.0) ?: return false
        seekToSeconds(next)
        return true
    }

    fun previousSection() {
        val episode = _nowPlaying.value?.episode ?: return seekToSeconds(0.0)
        seekToSeconds(Timeline.previousSectionStart(episode, player.state.value.positionMs / 1000.0))
    }

    fun setSpeed(speed: Float) {
        player.setSpeed(speed)
        scope.launch { store.updateSettings { it.copy(speed = speed) } }
    }

    fun continueAfterGate() {
        _gate.value = null
        player.play()
    }

    fun setSleep(mode: SleepMode) {
        sleepJob?.cancel()
        sleepSectionEndS = null
        val resolved = when (mode) {
            is SleepMode.Minutes -> mode.copy(endsAtMs = clock() + mode.minutes * 60_000L)
            SleepMode.EndOfSection -> {
                val episode = _nowPlaying.value?.episode
                val pos = player.state.value.positionMs / 1000.0
                sleepSectionEndS = episode?.let { Timeline.sectionAt(it, pos)?.endS }
                if (sleepSectionEndS == null) SleepMode.EndOfEpisode else mode
            }
            else -> mode
        }
        _sleep.value = SleepState(resolved, (resolved as? SleepMode.Minutes)?.let { it.minutes * 60_000L })
        if (resolved is SleepMode.Minutes) {
            sleepJob = scope.launch {
                while (isActive) {
                    val left = resolved.endsAtMs - clock()
                    if (left <= 0) {
                        sleepNow()
                        break
                    }
                    _sleep.value = SleepState(resolved, left)
                    delay(minOf(1_000L, left))
                }
            }
        }
    }

    private fun sleepNow() {
        player.pause()
        sleepJob?.cancel()
        sleepJob = null
        sleepSectionEndS = null
        _sleep.value = SleepState()
    }

    private suspend fun onState(s: PlaybackState) {
        val mediaId = s.mediaId
        if (mediaId != null && mediaId != lastMediaId) {
            val previous = lastMediaId
            // The queue moved on by itself (the previous episode played to its end), not a manual pick.
            val finishedPrevious = previous != null && lastDurationMs > 0 && lastPositionMs >= lastDurationMs - END_MARGIN_MS
            lastMediaId = mediaId
            lastPositionMs = s.positionMs
            lastDurationMs = 0
            if (finishedPrevious) {
                previous?.let(::episodeKeyOf)?.let { store.setCompleted(it, true) }
                if (_sleep.value.mode == SleepMode.EndOfEpisode) {
                    sleepNow()
                    player.seekTo(0)
                }
            }
            select(mediaId)
        }
        val key = s.key ?: return
        val episode = _nowPlaying.value?.takeIf { it.key == key }?.episode
        if (s.isPlaying && episode != null) {
            val from = lastPositionMs / 1000.0
            val to = s.positionMs / 1000.0
            if (to > from && to - from < 5.0) { // a jump (seek) is not "playing into" an answer
                if (store.state.value.settings.pauseBeforeAnswers) {
                    val revealed = store.state.value.revealed.keys
                        .filter { it.startsWith("${key.courseId}/${key.number}/") }
                        .map { it.substringAfterLast('/') }.toSet()
                    Timeline.answerGateBetween(episode, from, to, revealed)?.let { gateAt ->
                        player.pause()
                        player.seekTo((gateAt * 1000).toLong())
                        val question = episode.questions.firstOrNull { q ->
                            q.answerSegmentId != null && Timeline.segmentById(episode, q.answerSegmentId)?.startS == gateAt
                        }
                        _gate.value = AnswerGate(key, gateAt, question?.questionId)
                        lastPositionMs = (gateAt * 1000).toLong()
                        save(key, lastPositionMs, s.durationMs, force = true)
                        return
                    }
                }
                val sectionEnd = sleepSectionEndS
                if (_sleep.value.mode == SleepMode.EndOfSection && sectionEnd != null && to >= sectionEnd) sleepNow()
            }
        }
        lastPositionMs = s.positionMs
        if (s.durationMs > 0) lastDurationMs = s.durationMs
        if (s.ended && s.durationMs > 0 && store.state.value.progress[key.id]?.completed != true) {
            store.setCompleted(key, true)
            if (_sleep.value.mode == SleepMode.EndOfEpisode) sleepNow()
        }
        save(key, s.positionMs, s.durationMs, force = !s.isPlaying || s.ended)
    }

    private suspend fun save(key: EpisodeKey, positionMs: Long, durationMs: Long, force: Boolean) {
        val now = clock()
        // Before the item is prepared the player reports 0/0; saving that would erase the resume point.
        if (durationMs <= 0 || positionMs == lastSavedPositionMs) return
        if (!force && now - lastSavedAtMs < SAVE_EVERY_MS) return
        lastSavedAtMs = now
        lastSavedPositionMs = positionMs
        store.recordProgress(key, positionMs, durationMs)
    }

    companion object {
        const val SAVE_EVERY_MS = 5_000L
        const val END_MARGIN_MS = 6_000L
    }
}
