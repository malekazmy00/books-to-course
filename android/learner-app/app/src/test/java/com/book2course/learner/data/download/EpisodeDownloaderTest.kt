package com.book2course.learner.data.download

import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.AuthInterceptor
import com.book2course.learner.data.api.httpClient
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class EpisodeDownloaderTest {
    @get:Rule
    val tmp = TemporaryFolder()
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
    }

    @After
    fun tearDown() = server.shutdown()

    @Test
    fun `downloads into app storage with the token and reports progress`() = runTest {
        val bytes = ByteArray(200_000) { (it % 251).toByte() }
        server.enqueue(MockResponse().setBody(Buffer().write(bytes)))
        val files = EpisodeFiles(tmp.newFolder("episodes"))
        val host = server.hostName
        val downloader = EpisodeDownloader(httpClient(AuthInterceptor({ host }, { "tkn" })), files, StandardTestDispatcher(testScheduler))
        val progress = mutableListOf<Float?>()
        val file = downloader.download("c1", "03", server.url("/v3/learner/courses/c1/episodes/3/audio").toString()) { progress += it }
        assertEquals("Bearer tkn", server.takeRequest().getHeader("Authorization"))
        assertArrayEquals(bytes, file.readBytes())
        assertEquals(file, files.downloaded("c1", "03"))
        assertEquals(1f, progress.last()!!, 0f)
        assertTrue(DownloadStates(files).stateOf("c1", "03") is DownloadState.Done)
    }

    @Test
    fun `a failed download leaves no playable file`() = runTest {
        server.enqueue(MockResponse().setResponseCode(404))
        val files = EpisodeFiles(tmp.newFolder("e2"))
        val downloader = EpisodeDownloader(httpClient(AuthInterceptor({ null }, { null })), files, StandardTestDispatcher(testScheduler))
        try {
            downloader.download("c1", "04", server.url("/a").toString())
            fail("expected an error")
        } catch (e: AppErrorException) {
            assertEquals(AppError.NotFound, e.error)
        }
        assertNull(files.downloaded("c1", "04"))
        assertEquals(DownloadState.None, DownloadStates(files).stateOf("c1", "04"))
    }
}
