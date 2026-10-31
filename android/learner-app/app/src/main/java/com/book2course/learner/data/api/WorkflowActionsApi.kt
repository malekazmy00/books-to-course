package com.book2course.learner.data.api

import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.ResponseBody
import retrofit2.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import retrofit2.http.Streaming

/** Mutating workflow controls and streamed exports, separate from read-only OperatorApi. */
interface WorkflowActionsApi {
    @GET("v1/courses/{courseId}/books/{bookId}/progress")
    suspend fun bookProgress(@Path("courseId") courseId: String, @Path("bookId") bookId: String): Response<JsonObject>

    @GET("v1/ingestions/{workflowId}")
    suspend fun ingestion(@Path("workflowId") workflowId: String): Response<JsonObject>

    @POST("v1/courses/{courseId}/translations")
    suspend fun startTranslation(@Path("courseId") courseId: String, @Body body: JsonObject): Response<JsonObject>

    @GET("v1/workflows/{workflowId}/control")
    suspend fun control(@Path("workflowId") workflowId: String): Response<JsonObject>

    @GET("v1/workflows/{workflowId}/resume-capability")
    suspend fun resumeCapability(@Path("workflowId") workflowId: String): Response<JsonObject>

    @GET("v1/translations/{workflowId}")
    suspend fun translation(@Path("workflowId") workflowId: String): Response<JsonObject>

    @POST("v1/workflows/{workflowId}/pause")
    suspend fun pause(@Path("workflowId") workflowId: String): Response<JsonObject>

    @POST("v1/workflows/{workflowId}/resume")
    suspend fun resume(@Path("workflowId") workflowId: String, @Body body: JsonObject): Response<JsonObject>

    @POST("v1/courses/{courseId}/translations")
    suspend fun resumeTranslation(@Path("courseId") courseId: String, @Body body: JsonObject): Response<JsonObject>

    @Streaming
    @POST("v1/courses/{courseId}/results/export")
    suspend fun exportResults(@Path("courseId") courseId: String, @Body body: JsonObject): Response<ResponseBody>

    @Streaming
    @GET("v1/courses/{courseId}/audio/zip")
    suspend fun exportAudio(
        @Path("courseId") courseId: String,
        @Query("workflow_id") workflowId: String,
    ): Response<ResponseBody>

    companion object {
        fun create(baseUrl: String, client: OkHttpClient): WorkflowActionsApi = Retrofit.Builder()
            .baseUrl(if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/")
            .client(client)
            .addConverterFactory(LearnerApi.json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(WorkflowActionsApi::class.java)
    }
}

