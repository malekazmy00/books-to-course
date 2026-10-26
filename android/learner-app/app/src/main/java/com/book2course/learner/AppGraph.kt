package com.book2course.learner

import android.app.Application
import android.content.Context
import com.book2course.learner.data.LearnerRepository
import com.book2course.learner.data.OperatorRepository
import com.book2course.learner.data.api.AuthInterceptor
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.OperatorApi
import com.book2course.learner.data.api.httpClient
import com.book2course.learner.data.config.KeystoreTokenCipher
import com.book2course.learner.data.config.ServerConfig
import com.book2course.learner.data.config.ServerConfigStore
import com.book2course.learner.data.download.DownloadController
import com.book2course.learner.data.download.DownloadStates
import com.book2course.learner.data.download.EpisodeDownloader
import com.book2course.learner.data.download.EpisodeFiles
import com.book2course.learner.data.local.LocalStore
import com.book2course.learner.data.local.ResponseCache
import com.book2course.learner.playback.MediaControllerConnection
import com.book2course.learner.playback.PlaybackCoordinator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.io.File

/** Manual dependency graph (one per process). Small enough that a DI framework would cost more. */
class AppGraph(context: Context) {
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    val localStore = LocalStore(File(context.filesDir, "learner-state.json"))
    val serverConfig = ServerConfigStore(
        context.getSharedPreferences("learner", Context.MODE_PRIVATE), KeystoreTokenCipher(), BuildConfig.DEFAULT_API_BASE_URL,
    )
    val httpClient = httpClient(AuthInterceptor({ serverConfig.config.value?.host }, { serverConfig.config.value?.token }))
    private var api: Pair<String, LearnerApi>? = null

    @Synchronized
    fun api(): LearnerApi? {
        val config = serverConfig.config.value ?: return null
        api?.let { (url, client) -> if (url == config.baseUrl) return client }
        return LearnerApi.create(config.baseUrl, httpClient).also { api = config.baseUrl to it }
    }

    /** A client for a configuration that is not saved yet (setup's connection test). */
    fun apiFor(config: ServerConfig): LearnerApi =
        LearnerApi.create(config.baseUrl, httpClient(AuthInterceptor({ config.host }, { config.token })))

    val repository = LearnerRepository(::api, ResponseCache(File(context.filesDir, "api-cache")))

    private var operatorClient: Pair<String, OperatorApi>? = null

    /** The read-only operator API on the same server, with the same token (section «المراحل والتقدم»). */
    @Synchronized
    fun operatorApi(): OperatorApi? {
        val config = serverConfig.config.value ?: return null
        operatorClient?.let { (url, client) -> if (url == config.baseUrl) return client }
        return OperatorApi.create(config.baseUrl, httpClient).also { operatorClient = config.baseUrl to it }
    }

    val operatorRepository = OperatorRepository(::operatorApi, ResponseCache(File(context.filesDir, "operator-cache")))
    val episodeFiles = EpisodeFiles(File(context.filesDir, "episodes"))
    val downloadStates = DownloadStates(episodeFiles)
    val downloader = EpisodeDownloader(httpClient, episodeFiles)
    val downloads = DownloadController(context, downloadStates, episodeFiles)
    val playback = PlaybackCoordinator(
        scope = appScope,
        player = MediaControllerConnection(context, appScope),
        store = localStore,
        files = episodeFiles,
        episodeLoader = repository::episodeOnce,
        baseUrl = { serverConfig.config.value?.baseUrl },
    )
}

class LearnerApp : Application() {
    lateinit var graph: AppGraph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
    }
}

val Context.graph: AppGraph get() = (applicationContext as LearnerApp).graph
