package com.book2course.learner.data.api

import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.POST
import retrofit2.http.Path
import java.util.concurrent.TimeUnit

/**
 * Typed client of the learner API v3 (docs/api/learner-v3.openapi.json).
 * GETs take an optional If-None-Match so cached screens revalidate with a cheap 304.
 */
interface LearnerApi {
    @GET("v3/learner/meta")
    suspend fun meta(): LearnerMeta

    @GET("v3/learner/courses")
    suspend fun courses(@Header("If-None-Match") etag: String? = null): retrofit2.Response<CourseList>

    @GET("v3/learner/courses/{courseId}")
    suspend fun course(
        @Path("courseId") courseId: String,
        @Header("If-None-Match") etag: String? = null,
    ): retrofit2.Response<CourseDetail>

    @GET("v3/learner/courses/{courseId}/episodes/{number}")
    suspend fun episode(
        @Path("courseId") courseId: String,
        @Path("number") number: Int,
        @Header("If-None-Match") etag: String? = null,
    ): retrofit2.Response<EpisodeDetail>

    @GET("v3/learner/courses/{courseId}/concepts")
    suspend fun concepts(
        @Path("courseId") courseId: String,
        @Header("If-None-Match") etag: String? = null,
    ): retrofit2.Response<ConceptList>

    @POST("v3/learner/courses/{courseId}/episodes/{number}/questions/{questionId}/reveal")
    suspend fun reveal(
        @Path("courseId") courseId: String,
        @Path("number") number: Int,
        @Path("questionId") questionId: String,
        @Body body: RevealRequest,
    ): RevealResponse

    companion object {
        const val CLIENT_VERSION = 1

        val json = Json {
            ignoreUnknownKeys = true // additive server fields must not break old clients
            explicitNulls = false
            encodeDefaults = true
        }

        fun create(baseUrl: String, client: OkHttpClient): LearnerApi = Retrofit.Builder()
            .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(LearnerApi::class.java)
    }
}

/**
 * Adds the bearer token (D-030) only to requests for the configured server, so a redirect or a
 * foreign URL never receives it. Redirects are not followed at all (see [httpClient]).
 */
class AuthInterceptor(
    private val serverHost: () -> String?,
    private val token: () -> String?,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val host = serverHost()
        val value = token()
        if (host == null || value.isNullOrBlank() || !request.url.host.equals(host, ignoreCase = true)) {
            return chain.proceed(request)
        }
        return chain.proceed(request.newBuilder().header("Authorization", "Bearer $value").build())
    }
}

fun httpClient(auth: AuthInterceptor): OkHttpClient = OkHttpClient.Builder()
    .addInterceptor(auth)
    .followRedirects(false)
    .followSslRedirects(false)
    .connectTimeout(10, TimeUnit.SECONDS)
    .readTimeout(30, TimeUnit.SECONDS)
    .build()
