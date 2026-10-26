package com.book2course.learner.data.api

import com.book2course.learner.OperatorFixtures
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class OperatorApiClientTest {
    private lateinit var server: MockWebServer
    private lateinit var api: OperatorApi

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val host = server.hostName
        api = OperatorApi.create(server.url("/").toString(), httpClient(AuthInterceptor({ host }, { "secret-token" })))
    }

    @After
    fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200, etag: String? = null) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json").apply { if (etag != null) setHeader("ETag", etag) }.setBody(body)

    @Test
    fun `books sends the token, parses the library and ignores new fields`() = runTest {
        server.enqueue(json(OperatorFixtures.booksJson, etag = "\"b1\""))
        val response = api.books()
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/v3/operator/books", request.path)
        assertEquals("Bearer secret-token", request.getHeader("Authorization"))
        assertNull(request.getHeader("If-None-Match"))
        val book = response.body()!!.books.single()
        assertEquals("aaea5c4b", book.shortId)
        assertEquals("translation", book.currentStage)
        assertNull(book.uploadedAt)
        assertEquals("\"b1\"", response.headers()["ETag"])
    }

    @Test
    fun `stages and detail use the book and stage paths and revalidate with 304`() = runTest {
        server.enqueue(json(OperatorFixtures.stagesJson))
        server.enqueue(MockResponse().setResponseCode(304))
        server.enqueue(json(OperatorFixtures.detailJson))
        val stages = api.stages("b2c-demo", "book-aaea5c4b55e6a7519bdea580").body()!!
        assertEquals(9, stages.stages.size)
        val translation = stages.stages.first { it.stageId == "translation" }
        assertEquals(1797, translation.counts["pending"])
        assertEquals(53910L, translation.progress.etaS)
        assertEquals("translate_unit_batch", translation.workflow!!.currentActivity)
        assertEquals("/v3/operator/courses/b2c-demo/books/book-aaea5c4b55e6a7519bdea580/stages", server.takeRequest().path)
        val again = api.stages("b2c-demo", "book-aaea5c4b55e6a7519bdea580", etag = "\"e\"")
        assertEquals(304, again.code())
        assertEquals("\"e\"", server.takeRequest().getHeader("If-None-Match"))
        val detail = api.stage("b2c-demo", "book-aaea5c4b55e6a7519bdea580", "translation").body()!!
        assertEquals("/v3/operator/courses/b2c-demo/books/book-aaea5c4b55e6a7519bdea580/stages/translation", server.takeRequest().path)
        assertEquals("needs_review", detail.translatorNotes.single().decision)
        assertEquals(listOf("addition"), detail.translatorNotes.single().reasons)
    }
}
