package com.book2course.learner.data.local

import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class LocalStoreTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private val key = EpisodeKey("c1", 3)

    @Test
    fun `progress survives a restart and marks completion near the end`() = runTest {
        val file = File(tmp.root, "state.json")
        val dispatcher = StandardTestDispatcher(testScheduler)
        val store = LocalStore(file, dispatcher) { 1000 }
        store.recordProgress(key, 30_000, 100_000)
        assertFalse(store.state.value.progress[key.id]!!.completed)
        assertEquals(key, store.state.value.lastPlayed)
        store.recordProgress(key, 98_000, 100_000)
        val reloaded = LocalStore(file, dispatcher)
        val progress = reloaded.state.value.progress[key.id]!!
        assertTrue(progress.completed)
        assertEquals(1f, progress.fraction)
        assertEquals(0L, resumePositionMs(progress))
    }

    @Test
    fun `resume point steps back two seconds`() {
        assertEquals(28_000L, resumePositionMs(EpisodeProgress(30_000, 100_000)))
        assertEquals(0L, resumePositionMs(EpisodeProgress(1_000, 100_000)))
        assertEquals(0L, resumePositionMs(null))
    }

    @Test
    fun `bookmarks with notes can be added, edited and deleted`() = runTest {
        val store = LocalStore(File(tmp.root, "s.json"), StandardTestDispatcher(testScheduler)) { 5 }
        val b = store.addBookmark(key, 12_000, "نص الكتاب", "  مهم  ")
        assertEquals("مهم", store.state.value.bookmarks.single().note)
        store.editBookmark(b.id, "أهم")
        assertEquals("أهم", store.state.value.bookmarks.single().note)
        store.deleteBookmark(b.id)
        assertTrue(store.state.value.bookmarks.isEmpty())
    }

    @Test
    fun `a corrupt file starts fresh instead of crashing`() {
        val file = File(tmp.root, "bad.json").apply { writeText("{not json") }
        assertEquals(LearnerState(), LocalStore(file).state.value)
    }

    @Test
    fun `course progress averages its episodes`() = runTest {
        val store = LocalStore(File(tmp.root, "p.json"), StandardTestDispatcher(testScheduler))
        store.setCompleted(EpisodeKey("c1", 0), true)
        store.recordProgress(EpisodeKey("c1", 1), 50_000, 100_000)
        assertEquals(0.375f, courseProgress(store.state.value, "c1", listOf(0, 1, 2, 3)), 0.001f)
    }

    @Test
    fun `settings and revealed answers persist`() = runTest {
        val file = File(tmp.root, "x.json")
        val dispatcher = StandardTestDispatcher(testScheduler)
        val store = LocalStore(file, dispatcher)
        store.updateSettings { it.copy(speed = 1.5f, theme = ThemeMode.SEPIA, transcriptScale = 1.6f) }
        store.saveReveal(revealKey("c1", 1, "q1"), RevealedAnswer("ج", true, 2))
        val again = LocalStore(file, dispatcher).state.value
        assertEquals(1.5f, again.settings.speed)
        assertEquals(ThemeMode.SEPIA, again.settings.theme)
        assertEquals(true, again.revealed["c1/1/q1"]!!.correct)
    }
}
