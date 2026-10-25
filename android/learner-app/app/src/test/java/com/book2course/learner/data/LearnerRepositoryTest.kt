package com.book2course.learner.data

import com.book2course.learner.FakeApi
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.local.ResponseCache
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.IOException

class LearnerRepositoryTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `first load shows a loading state, then data, and caches it`() = runTest {
        val api = FakeApi()
        val repo = LearnerRepository({ api }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
        val states = repo.courses().toList()
        assertEquals(2, states.size)
        assertTrue(states[0].isInitialLoading)
        assertEquals("c1", states[1].data!!.courses.single().courseId)
        assertNull(states[1].error)
    }

    @Test
    fun `cached data is shown immediately and kept when the network fails`() = runTest {
        val api = FakeApi()
        val dir = tmp.newFolder()
        val dispatcher = StandardTestDispatcher(testScheduler)
        LearnerRepository({ api }, ResponseCache(dir), dispatcher).course("c1").toList()
        api.fail = IOException("offline")
        val states = LearnerRepository({ api }, ResponseCache(dir), dispatcher).course("c1").toList()
        assertEquals("c1", states[0].data!!.courseId) // no blocking spinner: cached data first
        assertTrue(states[0].refreshing)
        assertEquals("c1", states[1].data!!.courseId)
        assertEquals(AppError.Offline, states[1].error)
    }

    @Test
    fun `episodeOnce reads the cache without calling the network`() = runTest {
        val api = FakeApi()
        val repo = LearnerRepository({ api }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
        repo.episode("c1", 1).toList()
        val before = api.calls
        assertEquals(1, repo.episodeOnce("c1", 1)!!.number)
        assertEquals(before, api.calls)
    }

    @Test
    fun `without a configured server the error says so`() = runTest {
        val repo = LearnerRepository({ null }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
        val last = repo.courses().toList().last()
        assertEquals(AppError.NotConfigured, last.error)
        assertTrue(repo.reveal("c1", 1, "q1", null).isFailure)
    }
}
