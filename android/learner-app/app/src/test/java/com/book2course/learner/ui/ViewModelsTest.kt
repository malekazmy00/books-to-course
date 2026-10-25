package com.book2course.learner.ui

import com.book2course.learner.FakeApi
import com.book2course.learner.FakePlayer
import com.book2course.learner.Fixtures
import com.book2course.learner.R
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.api.LearnerMeta
import com.book2course.learner.data.config.ServerConfig
import com.book2course.learner.data.download.EpisodeFiles
import com.book2course.learner.data.local.EpisodeKey
import com.book2course.learner.data.local.LocalStore
import com.book2course.learner.data.local.ResponseCache
import com.book2course.learner.playback.PlaybackCoordinator
import com.book2course.learner.ui.course.CourseViewModel
import com.book2course.learner.ui.library.LibraryViewModel
import com.book2course.learner.ui.player.PlayerMessage
import com.book2course.learner.ui.player.PlayerViewModel
import com.book2course.learner.ui.setup.SetupViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

@OptIn(ExperimentalCoroutinesApi::class)
class ViewModelsTest {
    @get:Rule
    val tmp = TemporaryFolder()
    private val main = UnconfinedTestDispatcher()

    @Before
    fun setUp() = Dispatchers.setMain(main)

    @After
    fun tearDown() = Dispatchers.resetMain()

    private fun TestScope.world(): Triple<FakeApi, LearnerRepository, LocalStore> {
        val api = FakeApi()
        val io = UnconfinedTestDispatcher(testScheduler)
        return Triple(api, LearnerRepository({ api }, ResponseCache(tmp.newFolder()), io), LocalStore(File(tmp.root, "s.json"), io))
    }

    @Test
    fun `library loads courses and can refresh`() = runTest(main) {
        val (api, repo, _) = world()
        val vm = LibraryViewModel(repo)
        advanceUntilIdle()
        assertEquals("c1", vm.courses.value.data!!.courses.single().courseId)
        vm.refresh()
        advanceUntilIdle()
        assertEquals(2, api.calls)
    }

    @Test
    fun `course resume target prefers the last unfinished episode`() = runTest(main) {
        val (_, repo, store) = world()
        val vm = CourseViewModel("c1", repo, store)
        advanceUntilIdle()
        val detail = vm.course.value.data!!
        assertEquals(0, vm.resumeTarget(detail, store.state.value)!!.number)
        store.recordProgress(EpisodeKey("c1", 2), 10_000, 200_000)
        assertEquals(2, vm.resumeTarget(detail, store.state.value)!!.number)
        vm.setCompleted(0, true)
        advanceUntilIdle()
        assertTrue(store.state.value.progress["c1/0"]!!.completed)
    }

    @Test
    fun `player reveals an answer only on request and keeps it locally`() = runTest(main) {
        val (api, repo, store) = world()
        val playback = PlaybackCoordinator(backgroundScope, FakePlayer(), store, EpisodeFiles(tmp.newFolder()),
            repo::episodeOnce, { "https://s/" }, { testScheduler.currentTime })
        val vm = PlayerViewModel("c1", 1, repo, store, playback)
        advanceUntilIdle()
        val ui = vm.ui.value
        assertEquals("الهيمنة", ui.episode.data!!.title)
        assertTrue(ui.revealed.isEmpty())
        assertTrue(ui.episode.data!!.sections.flatMap { it.segments }.single { it.segmentId == "q1a" }.text == null)
        vm.reveal(ui.episode.data!!.questions.single(), null)
        advanceUntilIdle()
        assertEquals("q1", api.revealed.single().first)
        assertEquals("قيادة بالرضا والإقناع", vm.ui.value.revealed["q1"]!!.answer)
        assertEquals("قيادة بالرضا والإقناع", store.state.value.revealed["c1/1/q1"]!!.answer)
    }

    @Test
    fun `player explains when an episode has no audio yet, and plays one that has`() = runTest(main) {
        val (_, repo, store) = world()
        val player = FakePlayer()
        val playback = PlaybackCoordinator(backgroundScope, player, store, EpisodeFiles(tmp.newFolder()),
            repo::episodeOnce, { "https://s/" }, { testScheduler.currentTime })
        val intro = PlayerViewModel("c1", 0, repo, store, playback)
        advanceUntilIdle()
        intro.togglePlay()
        assertEquals(PlayerMessage.NoAudio, intro.ui.value.message)
        val first = PlayerViewModel("c1", 1, repo, store, playback)
        advanceUntilIdle()
        first.togglePlay()
        advanceUntilIdle()
        assertTrue(first.ui.value.isCurrent)
        assertNull(first.ui.value.message)
        assertEquals("queue:2@0", player.log.first { it.startsWith("queue") })
    }

    @Test
    fun `setup validates the address and checks the server before saving`() = runTest(main) {
        var saved: ServerConfig? = null
        var minClient = 1
        val vm = SetupViewModel("", allowCleartextLocal = false,
            check = { LearnerMeta("3.0.0", minClient, "m") }, save = { saved = it })
        vm.connect()
        assertEquals(R.string.setup_url_empty, vm.state.value.error)
        vm.onUrl("http://host.ts.net")
        vm.connect()
        assertEquals(R.string.setup_url_not_https, vm.state.value.error)
        vm.onUrl("host.ts.net")
        vm.connect()
        assertEquals(R.string.setup_token_empty, vm.state.value.error)
        vm.onToken("t0ken")
        minClient = 99
        vm.connect()
        advanceUntilIdle()
        assertEquals(R.string.setup_client_too_old, vm.state.value.error)
        assertNull(saved)
        minClient = 1
        vm.connect()
        advanceUntilIdle()
        assertEquals(ServerConfig("https://host.ts.net/", "t0ken"), saved)
        assertTrue(vm.state.value.done)
    }
}
