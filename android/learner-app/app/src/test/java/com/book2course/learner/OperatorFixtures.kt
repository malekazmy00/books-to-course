package com.book2course.learner

import com.book2course.learner.data.api.BookList
import com.book2course.learner.data.api.BookStages
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.StageDetail

/** Operator API v3 responses shaped like the server's (tests/unit/api/test_operator_v3.py). */
object OperatorFixtures {
    const val NOW = "2026-10-26T12:00:00+00:00"
    const val NOW_MS = 1_793_016_000_000L // 2026-10-26T12:00:00Z

    val booksJson = """
        {"books":[{"course_id":"b2c-demo","book_id":"book-aaea5c4b55e6a7519bdea580","title":"Structured Analytic Techniques",
          "display_name":"Structured Analytic Techniques · aaea5c4b","short_id":"aaea5c4b","file_sha256":"aaea5c4b",
          "uploaded_at":null,"role":"spine","current_stage":"translation","current_stage_title":"الترجمة",
          "current_status":"running","overall_percent":29.0,"completed_stages":2,"stage_count":9,
          "updated_at":"2026-10-26T11:59:00+00:00","future_field":1}],
         "generated_at":"$NOW"}
    """.trimIndent()

    private fun stage(order: Int, id: String, title: String, status: String, extra: String = "") = """
        {"stage_id":"$id","order":$order,"title":"$title","status":"$status","status_text":"x",
         "progress":{"done":null,"total":null,"percent":${if (status == "completed") "100.0" else "null"},"rate_per_hour":null,"eta_s":null},
         "counts":{},"gate":{"state":"none","record_status":null,"results":[]},"updated_at":null,"workflow":null,"note":null$extra}
    """.trimIndent()

    val translationStageJson = """
        {"stage_id":"translation","order":4,"title":"الترجمة","status":"running","status_text":"جارية",
         "progress":{"done":2763,"total":4560,"percent":60.6,"rate_per_hour":120.0,"eta_s":53910},
         "counts":{"accepted":2457,"note":204,"dropped":98,"rejected":4,"pending":1797,"total":4560},
         "gate":{"state":"none","record_status":null,"results":[]},"updated_at":"2026-10-26T11:57:00+00:00",
         "workflow":{"workflow_id":"b2c-translate-book-1","state":"running","status":"RUNNING",
                     "current_activity":"translate_unit_batch","activity_attempt":1,"started_at":null,"closed_at":null},
         "note":null}
    """.trimIndent()

    val stagesJson = """
        {"course_id":"b2c-demo","book_id":"book-aaea5c4b55e6a7519bdea580","title":"Structured Analytic Techniques",
         "stages":[${stage(1, "extraction", "الاستخراج", "completed")},${stage(2, "outline", "المخطط", "not_started")},
           ${stage(3, "termbase", "قاموس المصطلحات", "completed")},$translationStageJson,
           ${stage(5, "translator_notes", "حواشي المترجم", "waiting")},${stage(6, "knowledge", "المعرفة والمفاهيم", "not_started")},
           ${stage(7, "episodes", "الحلقات", "waiting")},${stage(8, "audio", "الصوت", "waiting")},${stage(9, "mp3", "MP3", "waiting")}],
         "generated_at":"$NOW"}
    """.trimIndent()

    val detailJson = """
        {"course_id":"b2c-demo","book_id":"book-aaea5c4b55e6a7519bdea580","stage":$translationStageJson,
         "outputs":[],"runs":[{"run_id":"66f03cab402670ab","created_at":"2026-10-25T00:22:40+00:00","updated_at":"$NOW",
           "unit_count":4560,"counts":{"accepted":2457,"note":204,"dropped":98,"rejected":4,"pending":1797,"total":4560},
           "progress":{"done":2763,"total":4560,"percent":60.6,"rate_per_hour":120.0,"eta_s":53910},"workflow":null}],
         "recent_units":[{"unit_id":"book-main:u02851","seq":2851,"decision":"accepted","source_text":"When analysts are too cautious",
           "target_text":"عندما يكون المحللون حذرين للغاية","reasons":[],"updated_at":"2026-10-26T11:59:00+00:00"}],
         "translator_notes":[{"unit_id":"book-main:u00003","seq":3,"decision":"needs_review","source_text":"RANDOLPH H. PHERSON",
           "target_text":"راندولف إتش. فيرسون","reasons":["addition"],"updated_at":"$NOW"}],
         "rejections":[{"subject":"book-main:u00007","reason":"untranslated_english (critical): lang.latin — English prose left in place","at":"$NOW"}],
         "generated_at":"$NOW"}
    """.trimIndent()

    val books: BookList get() = LearnerApi.json.decodeFromString(BookList.serializer(), booksJson)
    val stages: BookStages get() = LearnerApi.json.decodeFromString(BookStages.serializer(), stagesJson)
    val detail: StageDetail get() = LearnerApi.json.decodeFromString(StageDetail.serializer(), detailJson)
}
