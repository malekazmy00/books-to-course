package com.book2course.learner.data.local

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.security.MessageDigest

/**
 * Last good API responses, kept in app-private storage so every screen renders instantly and
 * offline. Entries are replaced, never merged; the ETag lets the next refresh be a 304.
 */
class ResponseCache(private val dir: File) {
    @Serializable
    data class Entry(val etag: String? = null, val body: String, val savedAt: Long)

    private val json = Json { ignoreUnknownKeys = true }

    private fun fileOf(key: String): File {
        val digest = MessageDigest.getInstance("SHA-256").digest(key.toByteArray()).joinToString("") { "%02x".format(it) }
        return File(dir, "$digest.json")
    }

    @Synchronized
    fun get(key: String): Entry? = try {
        val f = fileOf(key)
        if (f.exists()) json.decodeFromString(Entry.serializer(), f.readText()) else null
    } catch (_: Exception) {
        null
    }

    @Synchronized
    fun put(key: String, entry: Entry) {
        dir.mkdirs()
        val f = fileOf(key)
        val tmp = File(dir, f.name + ".tmp")
        tmp.writeText(json.encodeToString(Entry.serializer(), entry))
        if (!tmp.renameTo(f)) {
            f.delete()
            tmp.renameTo(f)
        }
    }

    @Synchronized
    fun clear() {
        dir.listFiles()?.forEach { it.delete() }
    }
}
