package com.book2course.learner.data

import android.content.ContentResolver
import android.net.Uri
import com.book2course.learner.data.api.WorkflowActionsApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.ResponseBody
import retrofit2.Response
import java.io.IOException

/** Explicit command client, kept separate from the read-only operator repository. */
class WorkflowActionsRepository(private val api: () -> WorkflowActionsApi?) {
    suspend fun startBookTranslation(courseId: String, bookId: String, sourceLanguage: String, targetLanguage: String): String = withApi { client ->
        require(sourceLanguage in setOf("en", "ar") && targetLanguage in setOf("en", "ar") && sourceLanguage != targetLanguage) {
            "اختر لغتين مختلفتين للترجمة."
        }
        val progress = client.bookProgress(courseId, bookId).bodyOrThrow("تعذر فحص سجل الكتاب قبل بدء الترجمة")
        val stages = progress["stages"]?.jsonObject ?: throw IOException("لا يوجد سجل مراحل لهذا الكتاب.")
        val ingest = stages["ingest"]?.jsonObject ?: throw IOException("لا يوجد استخراج OCR مرتبط بهذا الكتاب.")
        val ingestId = ingest["workflow_id"]?.jsonPrimitive?.contentOrNull
            ?: throw IOException("معرّف الاستخراج غير موجود؛ لا يمكن بدء الترجمة بأمان.")
        val ingestion = client.ingestion(ingestId).bodyOrThrow("تعذر قراءة ناتج الاستخراج")
        if (ingestion["status"]?.jsonPrimitive?.contentOrNull?.uppercase() != "COMPLETED") {
            throw IOException("يجب اكتمال استخراج الكتاب قبل بدء الترجمة.")
        }
        val sourceRef = ingestion["result"]?.jsonObject?.get("parsed_book")?.jsonObject?.get("ref")?.jsonObject
            ?: throw IOException("ناتج OCR لا يحتوي مرجع النص المستخرج.")
        val existing = stages["book_translation"]?.jsonObject
        val existingStatus = existing?.get("status")?.jsonPrimitive?.contentOrNull?.uppercase()
        val existingWorkflow = existing?.get("workflow_id")?.jsonPrimitive?.contentOrNull
        if (!existingWorkflow.isNullOrBlank() && existingStatus !in setOf("FAILED", "CANCELED", "TERMINATED", "TIMED_OUT")) {
            throw IOException("يوجد مسار ترجمة محفوظ أو حالته غير محسومة لهذه البصمة؛ استخدم الاستئناف أو افحص حالته بدل إنشاء مسار مكرر.")
        }
        val request = buildJsonObject {
            put("source_artifact_ref", sourceRef)
            put("source_kind", "book")
            put("source_language", sourceLanguage)
            put("target_language", targetLanguage)
            put("max_tokens", 8192)
        }
        val started = client.startTranslation(courseId, request).bodyOrThrow("تعذر بدء الترجمة")
        val workflowId = started["workflow_id"]?.jsonPrimitive?.contentOrNull
            ?: throw IOException("قبل الخادم الطلب لكنه لم يُرجع معرّف التشغيل.")
        "بدأت الترجمة · $workflowId"
    }

    suspend fun pause(workflowId: String): String = withApi { client ->
        client.pause(workflowId).bodyOrThrow("تعذر طلب الإيقاف المؤقت")
        "سُجل طلب الإيقاف؛ ستتوقف المرحلة بعد حفظ نقطة التقدم الحالية."
    }

    suspend fun resumeOrRetry(courseId: String, bookId: String, workflowId: String): String = withApi { client ->
        if (workflowId.startsWith("b2c-qa-")) {
            client.resume(workflowId, buildJsonObject { put("book_id", bookId) })
                .bodyOrThrow("تعذر استئناف الأسئلة من الحفظ")
            return@withApi "أُرسل استئناف الأسئلة؛ سيستخدم الخادم نقاط الحفظ المتاحة."
        }
        val control = client.control(workflowId).bodyOrThrow("تعذر قراءة حالة نقطة الحفظ")
        val temporalStatus = control["temporal_status"]?.jsonPrimitive?.contentOrNull.orEmpty()
        val controlStatus = control["status"]?.jsonPrimitive?.contentOrNull.orEmpty()
        if (temporalStatus == "RUNNING") {
            if (controlStatus !in setOf("PAUSE_REQUESTED", "PAUSED")) {
                throw IOException("المرحلة تعمل بالفعل ولم يصل تأكيد التوقف عند نقطة الحفظ بعد.")
            }
            client.resume(workflowId, buildJsonObject { put("book_id", bookId) })
                .bodyOrThrow("تعذر استئناف المرحلة")
            return@withApi "أُرسل استئناف المرحلة من نقطة الحفظ."
        }
        if (workflowId.startsWith("b2c-translate-")) {
            val status = client.translation(workflowId).bodyOrThrow("تعذر فحص حفظ الترجمة")
            val original = status["resume_request"]?.jsonObject
                ?: throw IOException("الخادم لا يوفّر الطلب الأصلي اللازم لاستئناف آمن؛ لم يبدأ أي عمل جديد.")
            val retry = JsonObject(original + mapOf(
                "resume_from_workflow_id" to JsonPrimitive(workflowId),
                "force_regenerate" to JsonPrimitive(false),
            ))
            val started = client.resumeTranslation(courseId, retry).bodyOrThrow("تعذر استئناف المقاطع غير المحفوظة")
            return@withApi "بدأ استئناف الترجمة من المحفوظ؛ المسار الجديد: ${started["workflow_id"]?.jsonPrimitive?.contentOrNull ?: "تم"}."
        }
        val capability = client.resumeCapability(workflowId).bodyOrThrow("تعذر فحص إتاحة الاستئناف")
        if (capability["resume_available"]?.jsonPrimitive?.contentOrNull != "true") {
            throw IOException("الخادم لا يتيح استئناف هذا التشغيل الفاشل الآن؛ لم تُكرر المرحلة.")
        }
        client.resume(workflowId, buildJsonObject { put("book_id", bookId) })
            .bodyOrThrow("تعذر إعادة معالجة المرحلة")
        "أُرسلت إعادة المعالجة عبر مسار الاستئناف المدعوم، مع الاحتفاظ بنقاط الحفظ."
    }

    suspend fun exportResults(courseId: String, resolver: ContentResolver, destination: Uri): String = withApi { client ->
        val response = client.exportResults(courseId, buildJsonObject { put("result_ids", buildJsonArray { }) })
        streamToUri(response, resolver, destination, "تعذر تصدير نتائج المقرر")
        "اكتمل حفظ ملف نتائج المقرر."
    }

    suspend fun exportAudio(courseId: String, workflowId: String, resolver: ContentResolver, destination: Uri): String = withApi { client ->
        val response = client.exportAudio(courseId, workflowId)
        streamToUri(response, resolver, destination, "تعذر تصدير الصوت")
        "اكتمل حفظ ملف الصوت المضغوط."
    }

    private suspend fun <T> withApi(block: suspend (WorkflowActionsApi) -> T): T =
        block(api() ?: throw IOException("أكمل إعداد اتصال الخادم أولًا."))

    private suspend fun streamToUri(
        response: Response<ResponseBody>,
        resolver: ContentResolver,
        destination: Uri,
        error: String,
    ) {
        withContext(Dispatchers.IO) {
            if (!response.isSuccessful) {
                val detail = response.errorBody()?.string()?.take(240)
                throw IOException(if (detail.isNullOrBlank()) "$error (HTTP ${response.code()})" else "$error: $detail")
            }
            val body = response.body() ?: throw IOException("استجابة التصدير فارغة.")
            val output = resolver.openOutputStream(destination, "w") ?: throw IOException("تعذر فتح ملف الحفظ.")
            body.use { input -> output.use { input.byteStream().copyTo(it, 64 * 1024) } }
        }
    }

    private fun Response<JsonObject>.bodyOrThrow(message: String): JsonObject {
        if (!isSuccessful) throw IOException("$message (HTTP ${code()})")
        return body() ?: throw IOException("$message: استجابة فارغة")
    }
}

