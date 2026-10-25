package com.book2course.learner.data.api

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Learner API v3 models. They mirror docs/api/learner-v3.openapi.json field for field;
 * OpenApiConformanceTest fails when a schema and its model drift apart.
 * GET models never carry an answer; only RevealResponse does (R0).
 */

@Serializable
data class LearnerMeta(
    @SerialName("api_version") val apiVersion: String,
    @SerialName("min_client_version") val minClientVersion: Int,
    @SerialName("mapping_version") val mappingVersion: String,
    val sources: Map<String, String> = emptyMap(),
)

@Serializable
data class CourseSummary(
    @SerialName("course_id") val courseId: String,
    val title: String,
    val language: String,
    @SerialName("episode_count") val episodeCount: Int,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class CourseList(val courses: List<CourseSummary>)

@Serializable
data class EpisodeSummary(
    val number: Int,
    val label: String,
    val title: String,
    val kind: String,
    @SerialName("lesson_id") val lessonId: String? = null,
    @SerialName("script_available") val scriptAvailable: Boolean,
    @SerialName("audio_available") val audioAvailable: Boolean,
    @SerialName("duration_s") val durationS: Double? = null,
)

@Serializable
data class CourseDetail(
    @SerialName("course_id") val courseId: String,
    val title: String,
    val language: String,
    val episodes: List<EpisodeSummary>,
    @SerialName("concepts_available") val conceptsAvailable: Boolean,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class Segment(
    @SerialName("segment_id") val segmentId: String,
    val voice: String,
    val role: String,
    val text: String? = null,
    val hidden: Boolean = false,
    @SerialName("start_s") val startS: Double? = null,
    @SerialName("end_s") val endS: Double? = null,
    @SerialName("source_label") val sourceLabel: String? = null,
    val pages: List<Int> = emptyList(),
)

@Serializable
data class Section(
    @SerialName("section_id") val sectionId: String,
    val kind: String,
    val title: String,
    @SerialName("start_s") val startS: Double? = null,
    @SerialName("end_s") val endS: Double? = null,
    val segments: List<Segment>,
)

@Serializable
data class KeyTerm(
    val term: String,
    val original: String? = null,
    val definition: String? = null,
    @SerialName("concept_id") val conceptId: String? = null,
)

@Serializable
data class LearnerQuestion(
    @SerialName("question_id") val questionId: String,
    val prompt: String,
    val options: List<String>? = null,
    @SerialName("question_segment_id") val questionSegmentId: String? = null,
    @SerialName("answer_segment_id") val answerSegmentId: String? = null,
)

@Serializable
data class EpisodeAudio(
    val available: Boolean,
    val path: String? = null,
    @SerialName("duration_s") val durationS: Double? = null,
    @SerialName("size_bytes") val sizeBytes: Long? = null,
)

@Serializable
data class EpisodeDetail(
    @SerialName("course_id") val courseId: String,
    val number: Int,
    val label: String,
    val title: String,
    val kind: String,
    @SerialName("lesson_id") val lessonId: String? = null,
    val status: String,
    val language: String,
    @SerialName("duration_s") val durationS: Double? = null,
    val audio: EpisodeAudio,
    val sections: List<Section>,
    @SerialName("key_terms") val keyTerms: List<KeyTerm>,
    val questions: List<LearnerQuestion>,
)

@Serializable
data class RevealRequest(
    @SerialName("selected_option") val selectedOption: Int? = null,
    @SerialName("response_text") val responseText: String? = null,
)

@Serializable
data class RevealResponse(
    @SerialName("question_id") val questionId: String,
    val answer: String,
    val correct: Boolean? = null,
    @SerialName("answer_segment_id") val answerSegmentId: String? = null,
    @SerialName("answer_section_id") val answerSectionId: String? = null,
)

@Serializable
data class AuthorQuote(val quote: String, val pages: List<Int> = emptyList())

@Serializable
data class ExternalNote(val statement: String, val urls: List<String>)

@Serializable
data class ConceptRelationOut(
    @SerialName("concept_id") val conceptId: String,
    val term: String,
    val relation: String,
)

@Serializable
data class ConceptCard(
    @SerialName("concept_id") val conceptId: String,
    val term: String,
    @SerialName("term_ar") val termAr: String? = null,
    val kind: String,
    val gloss: String? = null,
    @SerialName("author_quotes") val authorQuotes: List<AuthorQuote> = emptyList(),
    @SerialName("external_notes") val externalNotes: List<ExternalNote> = emptyList(),
    @SerialName("first_lesson_id") val firstLessonId: String? = null,
    @SerialName("first_episode") val firstEpisode: Int? = null,
    val relations: List<ConceptRelationOut> = emptyList(),
)

@Serializable
data class ConceptList(
    @SerialName("course_id") val courseId: String,
    val language: String,
    val status: String,
    val concepts: List<ConceptCard>,
)

object EpisodeStatus {
    const val READY = "ready"
    const val SCRIPT_PENDING = "script_pending"
}

object Voices {
    const val AUTHOR = "author"
    const val COURSE = "course"
    const val EXTERNAL = "external"
}

object SectionKinds {
    const val INTRO = "intro"
    const val BOOK_TEXT = "book_text"
    const val SUMMARY = "summary"
    const val KEY_TERMS = "key_terms"
    const val QUESTIONS = "questions"
}
