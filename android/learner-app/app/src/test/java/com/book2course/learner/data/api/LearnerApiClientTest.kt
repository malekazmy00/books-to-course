package com.book2course.learner.data.api

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class LearnerApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: LearnerApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val host = server.hostName
        api = LearnerApi.create(server.url("/").toString(), httpClient(AuthInterceptor({ host }, { "secret-token" })))
    }

    @After
    fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200, etag: String? = null) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json").apply { if (etag != null) setHeader("ETag", etag) }.setBody(body)

    @Test
    fun `courses sends the bearer token and parses the response`() = runTest {
        server.enqueue(json("""{"courses":[{"course_id":"c1","title":"T","language":"en","episode_count":3,"updated_at":"x","future_field":1}]}""", etag = "\"e1\""))
        val response = api.courses()
        val request = server.takeRequest()
        assertEquals("/v3/learner/courses", request.path)
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertNull(request.getHeader("If-None-Match"))
        assertEquals("c1", response.body()!!.courses.single().courseId)
        assertEquals("\"e1\"", response.headers()["ETag"])
    }

    @Test
    fun `conditional request carries the etag and surfaces 304`() = runTest {
        server.enqueue(MockResponse().setResponseCode(304))
        val response = api.course("c1", "\"e1\"")
        assertEquals("\"e1\"", server.takeRequest().getHeader("If-None-Match"))
        assertEquals(304, response.code())
        assertNull(response.body())
    }

    @Test
    fun `episode with hidden answer segments parses and keeps text null`() = runTest {
        server.enqueue(json(
            """{"course_id":"c1","number":1,"label":"01","title":"t","kind":"lesson","lesson_id":null,"status":"ready","language":"ar",
            "duration_s":12.5,"audio":{"available":true,"path":"/v3/learner/courses/c1/episodes/1/audio","duration_s":12.5,"size_bytes":10},
            "sections":[{"section_id":"qa","kind":"questions","title":"q","start_s":0,"end_s":5,"segments":[
              {"segment_id":"a","voice":"course","role":"answer","text":null,"hidden":true,"start_s":1,"end_s":2,"source_label":null,"pages":[]}]}],
            "key_terms":[],"questions":[{"question_id":"q1","prompt":"p","options":null,"question_segment_id":null,"answer_segment_id":"a"}]}""",
        ))
        val episode = api.episode("c1", 1).body()!!
        val segment = episode.sections.single().segments.single()
        assertTrue(segment.hidden)
        assertNull(segment.text)
        assertEquals("a", episode.questions.single().answerSegmentId)
    }

    @Test
    fun `reveal posts the selection`() = runTest {
        server.enqueue(json("""{"question_id":"q2","answer":"B","correct":true,"answer_segment_id":null,"answer_section_id":null}"""))
        val r = api.reveal("c1", 1, "q2", RevealRequest(selectedOption = 1))
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/v3/learner/courses/c1/episodes/1/questions/q2/reveal", request.path)
        assertTrue(request.body.readUtf8().contains("\"selected_option\":1"))
        assertEquals(true, r.correct)
    }

    @Test
    fun `token is never sent to another host and redirects are not followed`() = runTest {
        val other = MockWebServer().apply { start() }
        try {
            server.enqueue(MockResponse().setResponseCode(302).setHeader("Location", other.url("/steal").toString()))
            val response = api.courses()
            assertEquals(302, response.code())
            assertEquals(0, other.requestCount)
            val foreign = httpClient(AuthInterceptor({ "configured.example" }, { "secret-token" }))
            other.enqueue(MockResponse().setBody("x"))
            foreign.newCall(okhttp3.Request.Builder().url(other.url("/x")).build()).execute().close()
            assertNull(other.takeRequest().getHeader("Authorization"))
        } finally {
            other.shutdown()
        }
    }

    @Test
    fun `http failures map to learner errors`() {
        assertEquals(AppError.Unauthorized, httpError(401))
        assertEquals(AppError.NotFound, httpError(404))
        assertEquals(AppError.ServerUnavailable, httpError(503))
        assertEquals(AppError.Offline, java.io.IOException("x").toAppError())
        assertFalse(httpError(500) == AppError.ServerUnavailable)
    }
}
