package com.book2course.learner.data

import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.BookList
import com.book2course.learner.data.api.BookStages
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.OperatorApi
import com.book2course.learner.data.api.StageDetail
import com.book2course.learner.data.api.httpError
import com.book2course.learner.data.api.toAppError
import com.book2course.learner.data.local.ResponseCache
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import retrofit2.Response

/**
 * Cache-first reads of the operator section, same rules as [LearnerRepository]: the last good
 * response is emitted at once (also offline), then revalidated with If-None-Match (a cheap 304).
 * Its own cache directory, so clearing one section never empties the other.
 */
class OperatorRepository(
    private val api: () -> OperatorApi?,
    private val cache: ResponseCache,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val json = LearnerApi.json

    fun books(): Flow<Loadable<BookList>> = cached("operator/books", BookList.serializer()) { books(it) }

    fun stages(courseId: String, bookId: String): Flow<Loadable<BookStages>> =
        cached("operator/stages/$courseId/$bookId", BookStages.serializer()) { stages(courseId, bookId, it) }

    fun stage(courseId: String, bookId: String, stageId: String): Flow<Loadable<StageDetail>> =
        cached("operator/stage/$courseId/$bookId/$stageId", StageDetail.serializer()) { stage(courseId, bookId, stageId, it) }

    fun clearCache() = cache.clear()

    private fun <T : Any> cached(
        key: String,
        serializer: KSerializer<T>,
        fetch: suspend OperatorApi.(etag: String?) -> Response<T>,
    ): Flow<Loadable<T>> = flow {
        val entry = withContext(io) { cache.get(key) }
        val cachedValue = entry?.let { runCatching { json.decodeFromString(serializer, it.body) }.getOrNull() }
        emit(Loadable(cachedValue, refreshing = true))
        val client = api()
        if (client == null) {
            emit(Loadable(cachedValue, refreshing = false, error = AppError.NotConfigured))
            return@flow
        }
        val result: Loadable<T> = try {
            val response = client.fetch(if (cachedValue != null) entry?.etag else null)
            val body = response.body()
            when {
                response.code() == 304 && cachedValue != null -> Loadable(cachedValue)
                response.isSuccessful && body != null -> {
                    withContext(io) {
                        cache.put(key, ResponseCache.Entry(response.headers()["ETag"], json.encodeToString(serializer, body), clock()))
                    }
                    Loadable(body)
                }
                else -> Loadable(cachedValue, error = httpError(response.code()))
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Loadable(cachedValue, error = e.toAppError())
        }
        emit(result)
    }
}
