package com.book2course.learner.data

import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.ConceptList
import com.book2course.learner.data.api.CourseDetail
import com.book2course.learner.data.api.CourseList
import com.book2course.learner.data.api.EpisodeDetail
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.RevealRequest
import com.book2course.learner.data.api.RevealResponse
import com.book2course.learner.data.api.httpError
import com.book2course.learner.data.api.toAppError
import com.book2course.learner.data.local.ResponseCache
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.last
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import retrofit2.Response

/**
 * Data shown on screen: the last good value (possibly from cache) plus refresh state.
 * A screen with data never shows a blocking spinner; errors are shown next to the data.
 */
data class Loadable<out T>(val data: T? = null, val refreshing: Boolean = false, val error: AppError? = null) {
    val isInitialLoading: Boolean get() = data == null && refreshing
}

class LearnerRepository(
    private val api: () -> LearnerApi?,
    private val cache: ResponseCache,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val json = LearnerApi.json

    fun courses(): Flow<Loadable<CourseList>> =
        cached("courses", CourseList.serializer()) { etag -> courses(etag) }

    fun course(courseId: String): Flow<Loadable<CourseDetail>> =
        cached("course/$courseId", CourseDetail.serializer()) { etag -> course(courseId, etag) }

    fun episode(courseId: String, number: Int): Flow<Loadable<EpisodeDetail>> =
        cached("episode/$courseId/$number", EpisodeDetail.serializer()) { etag -> episode(courseId, number, etag) }

    fun concepts(courseId: String): Flow<Loadable<ConceptList>> =
        cached("concepts/$courseId", ConceptList.serializer()) { etag -> concepts(courseId, etag) }

    /** Cached episode if there is one, else fetched; for playback and downloads. */
    suspend fun episodeOnce(courseId: String, number: Int): EpisodeDetail? {
        val key = "episode/$courseId/$number"
        cachedValue(key, EpisodeDetail.serializer())?.let { return it }
        return episode(courseId, number).last().data
    }

    /** Refresh the cached copy (used before a download so the episode works offline). */
    suspend fun refreshEpisode(courseId: String, number: Int): EpisodeDetail? = episode(courseId, number).last().data

    suspend fun reveal(courseId: String, number: Int, questionId: String, selectedOption: Int?): Result<RevealResponse> {
        val client = api() ?: return Result.failure(AppErrorException(AppError.NotConfigured))
        return try {
            Result.success(client.reveal(courseId, number, questionId, RevealRequest(selectedOption = selectedOption)))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Result.failure(AppErrorException(e.toAppError()))
        }
    }

    fun clearCache() = cache.clear()

    private suspend fun <T> cachedValue(key: String, serializer: KSerializer<T>): T? = withContext(io) {
        cache.get(key)?.let { runCatching { json.decodeFromString(serializer, it.body) }.getOrNull() }
    }

    private fun <T : Any> cached(
        key: String,
        serializer: KSerializer<T>,
        fetch: suspend LearnerApi.(etag: String?) -> Response<T>,
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
