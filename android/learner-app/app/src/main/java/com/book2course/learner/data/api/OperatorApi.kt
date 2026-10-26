package com.book2course.learner.data.api

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.GET
import retrofit2.http.Header
import retrofit2.http.Path

/**
 * Typed client of the read-only operator API v3 (docs/api/operator-v3.openapi.json): the
 * «المراحل والتقدم» section. Same server, same token and same OkHttp client as the learner API.
 * Every call is a GET; nothing here changes server state.
 */
interface OperatorApi {
    @GET("v3/operator/meta")
    suspend fun meta(@Header("If-None-Match") etag: String? = null): retrofit2.Response<OperatorMeta>

    @GET("v3/operator/books")
    suspend fun books(@Header("If-None-Match") etag: String? = null): retrofit2.Response<BookList>

    @GET("v3/operator/courses/{courseId}/books/{bookId}/stages")
    suspend fun stages(
        @Path("courseId") courseId: String,
        @Path("bookId") bookId: String,
        @Header("If-None-Match") etag: String? = null,
    ): retrofit2.Response<BookStages>

    @GET("v3/operator/courses/{courseId}/books/{bookId}/stages/{stageId}")
    suspend fun stage(
        @Path("courseId") courseId: String,
        @Path("bookId") bookId: String,
        @Path("stageId") stageId: String,
        @Header("If-None-Match") etag: String? = null,
    ): retrofit2.Response<StageDetail>

    companion object {
        fun create(baseUrl: String, client: OkHttpClient): OperatorApi = Retrofit.Builder()
            .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
            .client(client)
            .addConverterFactory(LearnerApi.json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(OperatorApi::class.java)
    }
}
