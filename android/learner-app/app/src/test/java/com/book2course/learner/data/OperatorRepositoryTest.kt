package com.book2course.learner.data

import com.book2course.learner.OperatorFixtures
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.BookList
import com.book2course.learner.data.api.BookStages
import com.book2course.learner.data.api.OperatorApi
import com.book2course.learner.data.api.OperatorMeta
import com.book2course.learner.data.api.StageDetail
import com.book2course.learner.data.local.ResponseCache
import com.book2course.learner.ui.operator.OperatorScreenViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.Headers
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import retrofit2.Response
import java.io.IOException

class FakeOperatorApi : OperatorApi {
    var books: BookList = OperatorFixtures.books
    var fail: Exception? = null
    var code = 200
    val etags = mutableListOf<String?>()

    override suspend fun meta(etag: String?): Response<OperatorMeta> = throw UnsupportedOperationException()

    override suspend fun books(etag: String?): Response<BookList> {
        etags += etag
        fail?.let { throw it }
        if (code != 200) return Response.error(code, "{}".toResponseBody())
        if (etag == "\"b1\"" && books == OperatorFixtures.books) return Response.error("".toResponseBody(), notModified())
        return Response.success(books, Headers.headersOf("ETag", "\"b1\""))
    }

    override suspend fun stages(courseId: String, bookId: String, etag: String?): Response<BookStages> =
        Response.success(OperatorFixtures.stages)

    override suspend fun stage(courseId: String, bookId: String, stageId: String, etag: String?): Response<StageDetail> =
        Response.success(OperatorFixtures.detail)
}

private fun notModified(): okhttp3.Response = okhttp3.Response.Builder()
    .code(304).message("Not Modified").protocol(okhttp3.Protocol.HTTP_1_1)
    .request(okhttp3.Request.Builder().url("http://localhost/").build()).build()

@OptIn(ExperimentalCoroutinesApi::class)
class OperatorRepositoryTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun `cached data comes first, then the 304 keeps it`() = runTest {
        val api = FakeOperatorApi()
        val dir = tmp.newFolder()
        val repo = OperatorRepository({ api }, ResponseCache(dir), StandardTestDispatcher(testScheduler))
        val first = repo.books().toList()
        assertTrue(first[0].isInitialLoading)
        assertEquals("aaea5c4b", first[1].data!!.books.single().shortId)
        val second = repo.books().toList()
        assertEquals("aaea5c4b", second[0].data!!.books.single().shortId) // instantly, from the cache
        assertTrue(second[0].refreshing)
        assertEquals("\"b1\"", api.etags.last()) // revalidated with If-None-Match
        assertEquals("aaea5c4b", second[1].data!!.books.single().shortId)
        assertNull(second[1].error)
    }

    @Test
    fun `offline or a server without the operator API keeps the cached data and names the problem`() = runTest {
        val api = FakeOperatorApi()
        val repo = OperatorRepository({ api }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
        repo.books().toList()
        api.fail = IOException("no route")
        val offline = repo.books().toList().last()
        assertNotNull(offline.data)
        assertEquals(AppError.Offline, offline.error)
        api.fail = null
        api.code = 404
        assertEquals(AppError.NotFound, OperatorRepository({ api }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
            .books().toList().last().error)
    }

    @Test
    fun `not configured`() = runTest {
        val repo = OperatorRepository({ null }, ResponseCache(tmp.newFolder()), StandardTestDispatcher(testScheduler))
        assertEquals(AppError.NotConfigured, repo.books().toList().last().error)
    }

    @Test
    fun `the background tick refreshes without any visible indicator, pull-to-refresh shows one`() = runTest {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        try {
            val api = FakeOperatorApi()
            val repo = OperatorRepository({ api }, ResponseCache(tmp.newFolder()), UnconfinedTestDispatcher(testScheduler))
            val vm = OperatorScreenViewModel { repo.books() }
            advanceUntilIdle()
            assertNotNull(vm.ui.value.data)
            assertFalse(vm.ui.value.showRefreshing)
            val seen = mutableListOf<Boolean>()
            api.books = OperatorFixtures.books.copy(generatedAt = "later")
            vm.refreshSilently()
            seen += vm.ui.value.showRefreshing
            advanceUntilIdle()
            assertFalse(seen.any { it })
            assertNotNull(vm.ui.value.data)
            vm.refresh()
            advanceUntilIdle()
            assertFalse(vm.ui.value.userRefreshing) // finished
            assertEquals(3, api.etags.size)
        } finally {
            Dispatchers.resetMain()
        }
    }
}
