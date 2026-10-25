package com.book2course.learner.data.download

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.toAppError
import com.book2course.learner.graph
import kotlinx.coroutines.CancellationException

/** Queues episode downloads in WorkManager so they survive the app being closed. */
class DownloadController(
    private val context: Context,
    private val states: DownloadStates,
    private val files: EpisodeFiles,
) {
    private fun name(courseId: String, label: String) = "download/$courseId/$label"

    fun enqueue(courseId: String, number: Int, label: String) {
        states.set(courseId, label, DownloadState.Queued)
        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(workDataOf(KEY_COURSE to courseId, KEY_NUMBER to number, KEY_LABEL to label))
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .addTag(TAG)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(name(courseId, label), ExistingWorkPolicy.KEEP, request)
    }

    fun cancel(courseId: String, label: String) {
        WorkManager.getInstance(context).cancelUniqueWork(name(courseId, label))
        states.forget(courseId, label)
    }

    fun delete(courseId: String, label: String) {
        cancel(courseId, label)
        files.delete(courseId, label)
        states.forget(courseId, label)
    }

    fun deleteAll() {
        WorkManager.getInstance(context).cancelAllWorkByTag(TAG)
        files.deleteAll()
        states.clear()
    }

    companion object {
        const val TAG = "episode-download"
        const val KEY_COURSE = "course"
        const val KEY_NUMBER = "number"
        const val KEY_LABEL = "label"
    }
}

class DownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = applicationContext.graph
        val courseId = inputData.getString(DownloadController.KEY_COURSE) ?: return Result.failure()
        val label = inputData.getString(DownloadController.KEY_LABEL) ?: return Result.failure()
        val number = inputData.getInt(DownloadController.KEY_NUMBER, -1).takeIf { it >= 0 } ?: return Result.failure()
        val states = graph.downloadStates
        states.set(courseId, label, DownloadState.Running(null))
        return try {
            graph.repository.refreshEpisode(courseId, number) // transcript, sections and questions offline too
            val base = graph.serverConfig.config.value?.baseUrl ?: throw AppErrorException(AppError.NotConfigured)
            val url = "${base.trimEnd('/')}/v3/learner/courses/$courseId/episodes/$number/audio"
            var lastPercent = -1
            val file = graph.downloader.download(courseId, label, url) { fraction ->
                val percent = fraction?.let { (it * 100).toInt() } ?: -1
                if (percent != lastPercent) {
                    lastPercent = percent
                    states.set(courseId, label, DownloadState.Running(fraction))
                }
            }
            states.set(courseId, label, DownloadState.Done(file.length()))
            Result.success()
        } catch (e: CancellationException) {
            states.forget(courseId, label)
            throw e
        } catch (e: Exception) {
            val error = e.toAppError()
            states.set(courseId, label, DownloadState.Failed(error))
            if ((error == AppError.Offline || error == AppError.ServerUnavailable) && runAttemptCount < 3) Result.retry() else Result.failure()
        }
    }
}
