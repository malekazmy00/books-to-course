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
import java.io.File

/** The operator client against the committed contract docs/api/operator-v3.openapi.json (read-only: GET only). */
class OperatorOpenApiConformanceTest {
    private val doc: JsonObject = Json.parseToJsonElement(
        File(System.getProperty("b2c.operatorOpenApi") ?: "../../../docs/api/operator-v3.openapi.json").readText(),
    ).jsonObject

    private val models: Map<String, SerialDescriptor> = mapOf(
        "StageInfo" to StageInfo.serializer().descriptor,
        "OperatorMeta" to OperatorMeta.serializer().descriptor,
        "StageProgress" to StageProgress.serializer().descriptor,
        "GateInfo" to GateInfo.serializer().descriptor,
        "GateSummary" to GateSummary.serializer().descriptor,
        "WorkflowInfo" to WorkflowInfo.serializer().descriptor,
        "Stage" to Stage.serializer().descriptor,
        "BookSummary" to BookSummary.serializer().descriptor,
        "BookList" to BookList.serializer().descriptor,
        "BookStages" to BookStages.serializer().descriptor,
        "UnitSample" to UnitSample.serializer().descriptor,
        "Rejection" to Rejection.serializer().descriptor,
        "OutputInfo" to OutputInfo.serializer().descriptor,
        "RunInfo" to RunInfo.serializer().descriptor,
        "StageDetail" to StageDetail.serializer().descriptor,
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
    fun `every client route is a GET that exists in the contract, and the contract has only GETs`() {
        fun norm(path: String) = path.replace(Regex("\\{[^}]+}"), "{}")
        val contract = doc["paths"]!!.jsonObject.flatMap { (path, ops) -> ops.jsonObject.keys.map { it.uppercase() to norm(path) } }.toSet()
        assertTrue("the operator contract must be read-only: $contract", contract.all { it.first == "GET" })
        val client = OperatorApi::class.java.methods.map { m ->
            val get = m.getAnnotation(GET::class.java)
            assertTrue("${m.name} is not a GET", get != null || m.declaringClass != OperatorApi::class.java)
            "GET" to norm("/" + get!!.value)
        }.toSet()
        assertTrue("client calls routes missing from the contract: ${client - contract}", contract.containsAll(client))
    }
}
