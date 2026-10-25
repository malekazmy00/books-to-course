package com.book2course.learner.playback

import com.book2course.learner.FakePlayer
import com.book2course.learner.Fixtures
import com.book2course.learner.data.download.EpisodeFiles
import com.book2course.learner.data.local.EpisodeKey
import com.book2course.learner.data.local.LocalStore
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class PlaybackCoordinatorTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var player: FakePlayer
    private lateinit var store: LocalStore
    private lateinit var files: EpisodeFiles

    private fun TestScope.coordinator(): PlaybackCoordinator {
        player = FakePlayer()
        store = LocalStore(File(tmp.root, "state.json"), kotlinx.coroutines.test.StandardTestDispatcher(testScheduler)) { testScheduler.currentTime }
        files = EpisodeFiles(tmp.newFolder())
        return PlaybackCoordinator(
            scope = backgroundScope, player = player, store = store, files = files,
            episodeLoader = { _, n -> Fixtures.episode.copy(number = n) },
            baseUrl = { "https://server.example/" }, clock = { testScheduler.currentTime },
        )
    }

    @Test
    fun `queue holds episodes with audio, prefers downloaded files, and resumes`() = runTest {
        val c = coordinator()
        store.recordProgress(EpisodeKey("c1", 1), 30_000, 100_000)
        files.fileFor("c1", "02").apply { parentFile!!.mkdirs(); writeBytes(byteArrayOf(1, 2, 3)) }
        assertEquals(PlayResult.STARTED, c.playEpisode(Fixtures.course, 1))
        assertEquals(listOf(1, 2), player.queue.map { it.key.number })
        assertEquals("https://server.example/v3/learner/courses/c1/episodes/1/audio", player.queue[0].uri)
        assertTrue(player.queue[1].uri.startsWith("file:"))
        assertEquals(28_000L, player.startPositionMs)
        assertEquals(PlayResult.NO_AUDIO, c.playEpisode(Fixtures.course, 0))
    }

    @Test
    fun `questions mode pauses right before an unrevealed answer`() = runTest {
        val c = coordinator()
        c.playEpisode(Fixtures.course, 1, startMs = 0)
        runCurrent()
        assertEquals("الهيمنة", c.nowPlaying.value!!.episode!!.title)
        player.tick(79_000); runCurrent()
        player.tick(80_250); runCurrent()
        assertTrue(player.log.containsAll(listOf("pause", "seek:80000")))
        assertEquals("q1", c.gate.value!!.questionId)
        c.continueAfterGate()
        assertNull(c.gate.value)
        assertEquals("play", player.log.last())
    }

    @Test
    fun `no pause before answers when the learner turned it off`() = runTest {
        val c = coordinator()
        store.updateSettings { it.copy(pauseBeforeAnswers = false) }
        c.playEpisode(Fixtures.course, 1, startMs = 0)
        runCurrent()
        player.tick(79_000); runCurrent()
        player.tick(80_250); runCurrent()
        assertNull(c.gate.value)
    }

    @Test
    fun `progress is saved on pause and section skips use the script`() = runTest {
        val c = coordinator()
        c.playEpisode(Fixtures.course, 1, startMs = 0)
        runCurrent()
        player.tick(45_000); runCurrent()
        player.pause(); runCurrent()
        assertEquals(45_000L, store.state.value.progress["c1/1"]!!.positionMs)
        assertTrue(c.nextSection())
        assertEquals("seek:60000", player.log.last())
        player.tick(61_000, playing = false); runCurrent()
        c.previousSection()
        assertEquals("seek:10000", player.log.last())
    }

    @Test
    fun `sleep timer pauses after the chosen minutes`() = runTest {
        val c = coordinator()
        c.playEpisode(Fixtures.course, 1, startMs = 0)
        player.play(); runCurrent()
        c.setSleep(SleepMode.Minutes(1, 0))
        advanceTimeBy(30_000); runCurrent()
        assertEquals(30_000L, c.sleep.value.remainingMs)
        advanceTimeBy(31_000); runCurrent()
        assertEquals("pause", player.log.last())
        assertEquals(SleepMode.Off, c.sleep.value.mode)
    }

    @Test
    fun `speed is applied and remembered`() = runTest {
        val c = coordinator()
        c.setSpeed(1.75f)
        runCurrent()
        assertEquals("speed:1.75", player.log.last())
        assertEquals(1.75f, store.state.value.settings.speed)
    }
}
