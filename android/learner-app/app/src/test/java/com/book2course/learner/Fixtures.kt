package com.book2course.learner

import com.book2course.learner.data.api.ConceptList
import com.book2course.learner.data.api.CourseDetail
import com.book2course.learner.data.api.CourseList
import com.book2course.learner.data.api.CourseSummary
import com.book2course.learner.data.api.EpisodeAudio
import com.book2course.learner.data.api.EpisodeDetail
import com.book2course.learner.data.api.EpisodeSummary
import com.book2course.learner.data.api.KeyTerm
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.LearnerMeta
import com.book2course.learner.data.api.LearnerQuestion
import com.book2course.learner.data.api.RevealRequest
import com.book2course.learner.data.api.RevealResponse
import com.book2course.learner.data.api.Section
import com.book2course.learner.data.api.Segment
import com.book2course.learner.playback.EpisodeMedia
import com.book2course.learner.playback.PlaybackState
import com.book2course.learner.playback.PlayerConnection
import com.book2course.learner.playback.mediaIdOf
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import retrofit2.Response

object Fixtures {
    fun segment(id: String, start: Double, end: Double, voice: String = "course", role: String = "text", text: String? = "نص $id", hidden: Boolean = false) =
        Segment(segmentId = id, voice = voice, role = role, text = if (hidden) null else text, hidden = hidden, startS = start, endS = end)

    val episode = EpisodeDetail(
        courseId = "c1", number = 1, label = "01", title = "الهيمنة", kind = "lesson", lessonId = "l1", status = "ready",
        language = "ar", durationS = 100.0, audio = EpisodeAudio(true, "/v3/learner/courses/c1/episodes/1/audio", 100.0, 1000),
        sections = listOf(
            Section("intro", "intro", "تقدمة", 0.0, 10.0, listOf(segment("i1", 0.0, 10.0))),
            Section("book", "book_text", "نص الكتاب", 10.0, 60.0, listOf(
                segment("b1", 10.0, 40.0, voice = "author"),
                segment("b2", 40.0, 60.0, voice = "external").copy(sourceLabel = "Britannica"),
            )),
            Section("sum", "summary", "ملخص", 60.0, 70.0, listOf(segment("s1", 60.0, 70.0))),
            Section("qa", "questions", "أسئلة", 70.0, 100.0, listOf(
                segment("q1s", 70.0, 75.0, role = "question", text = "ما الهيمنة؟"),
                segment("q1a", 80.0, 90.0, role = "answer", hidden = true),
            )),
        ),
        keyTerms = listOf(KeyTerm("الهيمنة", "hegemony", "قيادة بالرضا")),
        questions = listOf(LearnerQuestion("q1", "ما الهيمنة؟", null, "q1s", "q1a")),
    )

    val course = CourseDetail(
        courseId = "c1", title = "دفاتر السجن", language = "en", conceptsAvailable = true, updatedAt = "2026-10-25T12:00:00Z",
        episodes = listOf(
            EpisodeSummary(0, "00", "مقدمة الكورس", "course_intro", null, scriptAvailable = false, audioAvailable = false),
            EpisodeSummary(1, "01", "الهيمنة", "lesson", "l1", scriptAvailable = true, audioAvailable = true, durationS = 100.0),
            EpisodeSummary(2, "02", "الرضا", "lesson", "l2", scriptAvailable = true, audioAvailable = true, durationS = 200.0),
        ),
    )
}

/** In-memory API; counts calls so repository tests can tell cache from network. */
class FakeApi : LearnerApi {
    var calls = 0
    var fail: Exception? = null
    var revealed = mutableListOf<Pair<String, RevealRequest>>()
    val courseList = CourseList(listOf(CourseSummary("c1", "دفاتر السجن", "en", 3, "2026-10-25T12:00:00Z")))

    private fun <T> respond(body: T): Response<T> {
        calls++
        fail?.let { throw it }
        return Response.success(body)
    }

    override suspend fun meta() = LearnerMeta("3.0.0", 1, "m", emptyMap())
    override suspend fun courses(etag: String?) = respond(courseList)
    override suspend fun course(courseId: String, etag: String?) = respond(Fixtures.course)
    override suspend fun episode(courseId: String, number: Int, etag: String?) = respond(Fixtures.episode.copy(number = number))
    override suspend fun concepts(courseId: String, etag: String?) = respond(ConceptList(courseId, "en", "pending", emptyList()))
    override suspend fun reveal(courseId: String, number: Int, questionId: String, body: RevealRequest): RevealResponse {
        calls++
        fail?.let { throw it }
        revealed += questionId to body
        return RevealResponse(questionId, "قيادة بالرضا والإقناع", correct = null, answerSegmentId = "q1a", answerSectionId = "qa")
    }
}

class FakePlayer : PlayerConnection {
    val mutable = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = mutable
    var queue: List<EpisodeMedia> = emptyList()
    var startPositionMs = -1L
    val log = mutableListOf<String>()

    override fun setQueue(items: List<EpisodeMedia>, startIndex: Int, startPositionMs: Long, playWhenReady: Boolean) {
        queue = items
        this.startPositionMs = startPositionMs
        log += "queue:${items.size}@$startPositionMs"
        mutable.value = PlaybackState(mediaId = mediaIdOf(items[startIndex].key), positionMs = startPositionMs, durationMs = 0, isPlaying = false)
    }

    override fun play() {
        log += "play"
        mutable.value = mutable.value.copy(isPlaying = true)
    }

    override fun pause() {
        log += "pause"
        mutable.value = mutable.value.copy(isPlaying = false)
    }

    override fun seekTo(positionMs: Long) {
        log += "seek:$positionMs"
        mutable.value = mutable.value.copy(positionMs = positionMs)
    }

    override fun setSpeed(speed: Float) {
        log += "speed:$speed"
        mutable.value = mutable.value.copy(speed = speed)
    }

    /** Simulates playback reaching [positionMs] of a prepared item. */
    fun tick(positionMs: Long, durationMs: Long = 100_000, playing: Boolean = true) {
        mutable.value = mutable.value.copy(positionMs = positionMs, durationMs = durationMs, isPlaying = playing)
    }
}
