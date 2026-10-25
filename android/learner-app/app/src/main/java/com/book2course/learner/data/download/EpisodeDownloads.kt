package com.book2course.learner.data.download

import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.httpError
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import kotlin.coroutines.coroutineContext

sealed interface DownloadState {
    data object None : DownloadState
    data object Queued : DownloadState
    data class Running(val fraction: Float?) : DownloadState
    data class Done(val bytes: Long) : DownloadState
    data class Failed(val error: AppError) : DownloadState
}

/**
 * Episode audio stored in app-private storage (never the public Downloads folder, and the token
 * never goes to a system service: audit 09 §2.2). Files are written to `.part` and renamed when
 * complete, so a half-written file is never played.
 */
class EpisodeFiles(private val root: File) {
    fun fileFor(courseId: String, label: String): File = File(File(root, safe(courseId)), "${safe(label)}.mp3")

    fun downloaded(courseId: String, label: String): File? = fileFor(courseId, label).takeIf { it.isFile && it.length() > 0 }

    fun delete(courseId: String, label: String) {
        fileFor(courseId, label).delete()
    }

    fun deleteAll() {
        root.deleteRecursively()
    }

    fun totalBytes(): Long = root.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    private fun safe(value: String) = value.replace(Regex("[^A-Za-z0-9._-]"), "_")
}

class EpisodeDownloader(
    private val client: OkHttpClient,
    private val files: EpisodeFiles,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) {
    /** Downloads [audioUrl] for the episode; returns the finished file. */
    suspend fun download(
        courseId: String,
        label: String,
        audioUrl: String,
        onProgress: (Float?) -> Unit = {},
    ): File = withContext(io) {
        val target = files.fileFor(courseId, label)
        target.parentFile?.mkdirs()
        val part = File(target.parentFile, target.name + ".part")
        val response = client.newCall(Request.Builder().url(audioUrl).build()).execute()
        response.use { r ->
            if (!r.isSuccessful) throw AppErrorException(httpError(r.code))
            val body = r.body ?: throw AppErrorException(AppError.ServerUnavailable)
            val total = body.contentLength().takeIf { it > 0 }
            var read = 0L
            body.byteStream().use { input ->
                part.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        coroutineContext.ensureActive()
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                        read += n
                        onProgress(total?.let { read.toFloat() / it })
                    }
                }
            }
            if (total != null && read != total) {
                part.delete()
                throw AppErrorException(AppError.Offline)
            }
        }
        if (!part.renameTo(target)) {
            target.delete()
            if (!part.renameTo(target)) throw AppErrorException(AppError.Unexpected(null))
        }
        target
    }
}

/** In-memory view of download states, merged with what is on disk. */
class DownloadStates(private val files: EpisodeFiles) {
    private val _states = MutableStateFlow<Map<String, DownloadState>>(emptyMap())
    val states: StateFlow<Map<String, DownloadState>> = _states.asStateFlow()

    fun key(courseId: String, label: String) = "$courseId/$label"

    fun stateOf(courseId: String, label: String): DownloadState =
        _states.value[key(courseId, label)]
            ?: files.downloaded(courseId, label)?.let { DownloadState.Done(it.length()) }
            ?: DownloadState.None

    fun set(courseId: String, label: String, state: DownloadState) {
        _states.update { it + (key(courseId, label) to state) }
    }

    fun forget(courseId: String, label: String) {
        _states.update { it - key(courseId, label) }
    }

    fun clear() {
        _states.value = emptyMap()
    }
}
