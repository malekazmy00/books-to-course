package com.book2course.learner.data.api

import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.http.GET
import retrofit2.http.POST
import java.io.File

/**
 * The hand-written client against the committed contract docs/api/learner-v3.openapi.json:
 * every schema has a model with the same fields, nothing the server may omit is required here,
 * and every route the client calls exists in the contract.
 */
class OpenApiConformanceTest {
    private val doc: JsonObject = Json.parseToJsonElement(
        File(System.getProperty("b2c.learnerOpenApi") ?: "../../../docs/api/learner-v3.openapi.json").readText(),
    ).jsonObject

    private val models: Map<String, SerialDescriptor> = mapOf(
        "LearnerMeta" to LearnerMeta.serializer().descriptor,
        "CourseSummary" to CourseSummary.serializer().descriptor,
        "CourseList" to CourseList.serializer().descriptor,
        "EpisodeSummary" to EpisodeSummary.serializer().descriptor,
        "CourseDetail" to CourseDetail.serializer().descriptor,
        "Segment" to Segment.serializer().descriptor,
        "Section" to Section.serializer().descriptor,
        "KeyTerm" to KeyTerm.serializer().descriptor,
        "LearnerQuestion" to LearnerQuestion.serializer().descriptor,
        "EpisodeAudio" to EpisodeAudio.serializer().descriptor,
        "EpisodeDetail" to EpisodeDetail.serializer().descriptor,
        "RevealRequest" to RevealRequest.serializer().descriptor,
        "RevealResponse" to RevealResponse.serializer().descriptor,
        "AuthorQuote" to AuthorQuote.serializer().descriptor,
        "ExternalNote" to ExternalNote.serializer().descriptor,
        "ConceptRelationOut" to ConceptRelationOut.serializer().descriptor,
        "ConceptCard" to ConceptCard.serializer().descriptor,
        "ConceptList" to ConceptList.serializer().descriptor,
    )

    private val schemas get() = doc["components"]!!.jsonObject["schemas"]!!.jsonObject

    @Test
    fun `every contract schema has a model with the same fields`() {
        val contractSchemas = schemas.keys - setOf("HTTPValidationError", "ValidationError")
        assertEquals(contractSchemas, models.keys)
        for ((name, descriptor) in models) {
            val schema = schemas[name]!!.jsonObject
            val properties = schema["properties"]!!.jsonObject.keys
            val fields = (0 until descriptor.elementsCount).map(descriptor::getElementName).toSet()
            assertEquals("fields of $name", properties, fields)
            val required = schema["required"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
            val mandatoryHere = (0 until descriptor.elementsCount).filterNot(descriptor::isElementOptional).map(descriptor::getElementName)
            assertTrue("$name requires fields the server may omit: ${mandatoryHere - required}", required.containsAll(mandatoryHere))
        }
    }

    @Test
    fun `get models carry no answer field`() {
        val forbidden = setOf("answer", "answer_index", "answer_text", "correct", "rationale")
        models.filterKeys { it != "RevealResponse" }.forEach { (name, d) ->
            val fields = (0 until d.elementsCount).map(d::getElementName).toSet()
            assertTrue("$name has an answer field", (fields intersect forbidden).isEmpty())
        }
    }

    @Test
    fun `every client route exists in the contract`() {
        fun norm(path: String) = path.replace(Regex("\\{[^}]+}"), "{}")
        val contract = doc["paths"]!!.jsonObject.flatMap { (path, ops) -> ops.jsonObject.keys.map { it.uppercase() to norm(path) } }.toSet()
        val client = LearnerApi::class.java.methods.mapNotNull { m ->
            m.getAnnotation(GET::class.java)?.let { "GET" to norm("/" + it.value) }
                ?: m.getAnnotation(POST::class.java)?.let { "POST" to norm("/" + it.value) }
        }.toSet()
        assertTrue("client calls routes missing from the contract: ${client - contract}", contract.containsAll(client))
    }
}
