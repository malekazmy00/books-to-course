package com.book2course.learner.data.local

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

/** Learner-owned state. It stays on the device: the learner API is read-only. */
@Serializable
data class LearnerState(
    val progress: Map<String, EpisodeProgress> = emptyMap(),
    val lastPlayed: EpisodeKey? = null,
    val bookmarks: List<Bookmark> = emptyList(),
    val revealed: Map<String, RevealedAnswer> = emptyMap(),
    val settings: LearnerSettings = LearnerSettings(),
)

@Serializable
data class EpisodeKey(val courseId: String, val number: Int) {
    val id: String get() = "$courseId/$number"
}

@Serializable
data class EpisodeProgress(
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val completed: Boolean = false,
    val updatedAt: Long = 0,
) {
    /** 0..1; a completed episode is 1 even if it was not played to the last millisecond. */
    val fraction: Float
        get() = when {
            completed -> 1f
            durationMs <= 0 -> 0f
            else -> (positionMs.toFloat() / durationMs).coerceIn(0f, 1f)
        }
}

@Serializable
data class Bookmark(
    val id: String,
    val courseId: String,
    val number: Int,
    val positionMs: Long,
    val sectionTitle: String? = null,
    val note: String = "",
    val createdAt: Long,
)

@Serializable
data class RevealedAnswer(val answer: String, val correct: Boolean? = null, val selectedOption: Int? = null)

enum class ThemeMode { SYSTEM, LIGHT, DARK, SEPIA }

@Serializable
data class LearnerSettings(
    val skipBackSeconds: Int = 15,
    val skipForwardSeconds: Int = 30,
    val speed: Float = 1.0f,
    val transcriptScale: Float = 1.0f,
    val theme: ThemeMode = ThemeMode.SYSTEM,
    val pauseBeforeAnswers: Boolean = true,
    val followTranscript: Boolean = true,
) {
    companion object {
        val SPEEDS = listOf(0.75f, 1.0f, 1.25f, 1.5f, 1.75f, 2.0f)
        val SKIP_CHOICES = listOf(5, 10, 15, 30, 60)
        const val MIN_SCALE = 0.85f
        const val MAX_SCALE = 2.0f
    }
}

fun revealKey(courseId: String, number: Int, questionId: String) = "$courseId/$number/$questionId"

/**
 * One small JSON file, written atomically (temp file + rename) after every change.
 * Reads are synchronous and cheap (the file is a few KB), so screens never wait for it.
 */
class LocalStore(
    private val file: File,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val mutex = Mutex()
    private val _state = MutableStateFlow(load())
    val state: StateFlow<LearnerState> = _state.asStateFlow()

    private fun load(): LearnerState = try {
        if (file.exists()) json.decodeFromString(LearnerState.serializer(), file.readText()) else LearnerState()
    } catch (_: Exception) {
        LearnerState() // a corrupt file must not lock the learner out; it is rewritten on next change
    }

    suspend fun update(transform: (LearnerState) -> LearnerState) {
        mutex.withLock {
            val next = transform(_state.value)
            if (next == _state.value) return
            _state.value = next
            withContext(io) { write(next) }
        }
    }

    private fun write(value: LearnerState) {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(json.encodeToString(LearnerState.serializer(), value))
        if (!tmp.renameTo(file)) {
            file.delete()
            tmp.renameTo(file)
        }
    }

    suspend fun recordProgress(key: EpisodeKey, positionMs: Long, durationMs: Long) = update { s ->
        val old = s.progress[key.id] ?: EpisodeProgress()
        val completed = old.completed || (durationMs > 0 && positionMs >= durationMs - COMPLETE_MARGIN_MS)
        s.copy(
            progress = s.progress + (key.id to EpisodeProgress(positionMs, durationMs.coerceAtLeast(old.durationMs), completed, clock())),
            lastPlayed = key,
        )
    }

    suspend fun setCompleted(key: EpisodeKey, completed: Boolean) = update { s ->
        val old = s.progress[key.id] ?: EpisodeProgress()
        s.copy(progress = s.progress + (key.id to old.copy(completed = completed, positionMs = if (completed) old.positionMs else 0, updatedAt = clock())))
    }

    suspend fun addBookmark(key: EpisodeKey, positionMs: Long, sectionTitle: String?, note: String): Bookmark {
        val bookmark = Bookmark("bm-${clock()}-${positionMs}", key.courseId, key.number, positionMs, sectionTitle, note.trim(), clock())
        update { s -> s.copy(bookmarks = s.bookmarks + bookmark) }
        return bookmark
    }

    suspend fun editBookmark(id: String, note: String) = update { s ->
        s.copy(bookmarks = s.bookmarks.map { if (it.id == id) it.copy(note = note.trim()) else it })
    }

    suspend fun deleteBookmark(id: String) = update { s -> s.copy(bookmarks = s.bookmarks.filterNot { it.id == id }) }

    suspend fun saveReveal(key: String, answer: RevealedAnswer) = update { s -> s.copy(revealed = s.revealed + (key to answer)) }

    suspend fun updateSettings(transform: (LearnerSettings) -> LearnerSettings) = update { s -> s.copy(settings = transform(s.settings)) }

    companion object {
        /** Within this much of the end an episode counts as listened to (outro silence, rounding). */
        const val COMPLETE_MARGIN_MS = 3_000L
    }
}

/** Resume point: a little before the saved position so the sentence restarts; 0 when finished. */
fun resumePositionMs(progress: EpisodeProgress?): Long = when {
    progress == null || progress.completed -> 0
    else -> (progress.positionMs - 2_000).coerceAtLeast(0)
}

fun courseProgress(state: LearnerState, courseId: String, episodeNumbers: List<Int>): Float {
    if (episodeNumbers.isEmpty()) return 0f
    return episodeNumbers.sumOf { (state.progress["$courseId/$it"]?.fraction ?: 0f).toDouble() }.toFloat() / episodeNumbers.size
}
