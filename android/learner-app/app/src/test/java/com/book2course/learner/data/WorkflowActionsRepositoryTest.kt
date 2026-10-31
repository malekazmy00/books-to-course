package com.book2course.learner.data

import com.book2course.learner.data.api.AuthInterceptor
import com.book2course.learner.data.api.WorkflowActionsApi
import com.book2course.learner.data.api.httpClient
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

class WorkflowActionsRepositoryTest {
    private lateinit var server: MockWebServer
    private lateinit var repository: WorkflowActionsRepository

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        val host = server.hostName
        val api = WorkflowActionsApi.create(server.url("/").toString(), httpClient(AuthInterceptor({ host }, { "test-token" })))
        repository = WorkflowActionsRepository { api }
    }

    @After
    fun tearDown() = server.shutdown()

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json").setBody(body)

    @Test
    fun startsBookTranslationOnlyAfterCompletedOcrAndUsesItsExactArtifact() = runTest {
        server.enqueue(json("""{"stages":{"ingest":{"workflow_id":"ing-1"}}}"""))
        server.enqueue(json("""{"status":"COMPLETED","result":{"parsed_book":{"ref":{"stage":"ingest-parse-pdf","uri":"s3://bucket/course/book.json","sha256":"abc"}}}}"""))
        server.enqueue(json("""{"workflow_id":"b2c-translate-new"}""", 202))

        assertEquals("بدأت الترجمة · b2c-translate-new", repository.startBookTranslation("course", "book", "en", "ar"))
        assertEquals("/v1/courses/course/books/book/progress", server.takeRequest().path)
        assertEquals("/v1/ingestions/ing-1", server.takeRequest().path)
        val start = server.takeRequest()
        assertEquals("POST", start.method)
        assertEquals("/v1/courses/course/translations", start.path)
        val body = start.body.readUtf8()
        assertTrue(body.contains("\"source_artifact_ref\":{"))
        assertTrue(body.contains("\"source_language\":\"en\""))
    }

    @Test
    fun refusesToStartWhenOcrIsNotComplete() = runTest {
        server.enqueue(json("""{"stages":{"ingest":{"workflow_id":"ing-1"}}}"""))
        server.enqueue(json("""{"status":"RUNNING"}"""))
        try {
            repository.startBookTranslation("course", "book", "en", "ar")
            throw AssertionError("translation must not start before OCR is complete")
        } catch (expected: IOException) {
            assertTrue(expected.message!!.contains("اكتمال استخراج"))
        }
        assertEquals(2, server.requestCount)
    }

    @Test
    fun refusesDuplicateOrAmbiguousSavedTranslation() = runTest {
        server.enqueue(json("""{"stages":{"ingest":{"workflow_id":"ing-1"},"book_translation":{"workflow_id":"existing","status":"UNKNOWN"}}}"""))
        server.enqueue(json("""{"status":"COMPLETED","result":{"parsed_book":{"ref":{"stage":"ingest-parse-pdf","uri":"s3://bucket/course/book.json","sha256":"abc"}}}}"""))
        try {
            repository.startBookTranslation("course", "book", "en", "ar")
            throw AssertionError("must not duplicate an existing/ambiguous workflow")
        } catch (expected: IOException) {
            assertTrue(expected.message!!.contains("غير محسومة"))
        }
        assertEquals(2, server.requestCount)
    }
}

