package com.book2course.learner.ui

import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionLayout
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.toRoute
import com.book2course.learner.AppGraph
import com.book2course.learner.BuildConfig
import com.book2course.learner.data.api.EpisodeSummary
import com.book2course.learner.ui.common.LocalNavAnimatedScope
import com.book2course.learner.ui.common.LocalSharedTransitionScope
import com.book2course.learner.ui.concepts.ConceptsScreen
import com.book2course.learner.ui.concepts.ConceptsViewModel
import com.book2course.learner.ui.course.CourseScreen
import com.book2course.learner.ui.course.CourseViewModel
import com.book2course.learner.ui.course.EpisodeRowActions
import com.book2course.learner.ui.library.ContinueItem
import com.book2course.learner.ui.library.LibraryScreen
import com.book2course.learner.ui.library.LibraryViewModel
import com.book2course.learner.ui.player.PlayerActions
import com.book2course.learner.ui.player.PlayerScreen
import com.book2course.learner.ui.player.PlayerViewModel
import com.book2course.learner.ui.settings.SettingsScreen
import com.book2course.learner.ui.setup.SetupScreen
import com.book2course.learner.ui.setup.SetupViewModel
import com.book2course.learner.ui.theme.LearnerTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable

@Serializable data object SetupRoute
@Serializable data object LibraryRoute
@Serializable data object SettingsRoute
@Serializable data class CourseRoute(val courseId: String, val title: String)
@Serializable data class PlayerRoute(val courseId: String, val number: Int, val label: String, val title: String)
@Serializable data class ConceptsRoute(val courseId: String)

private const val NAV_MS = 320

@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
fun LearnerRoot(graph: AppGraph) {
    val learner by graph.localStore.state.collectAsStateWithLifecycle()
    LearnerTheme(learner.settings.theme) {
        // The content is Arabic: lay out right-to-left whatever the device language is.
        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
            val nav = rememberNavController()
            val config by graph.serverConfig.config.collectAsStateWithLifecycle()
            val now by graph.playback.nowPlaying.collectAsStateWithLifecycle()
            val playback by graph.playback.playback.collectAsStateWithLifecycle()
            val entry by nav.currentBackStackEntryAsState()
            val onPlayer = entry?.destination?.route?.contains("PlayerRoute") == true
            val start: Any = remember { if (graph.serverConfig.config.value == null) SetupRoute else LibraryRoute }
            Column(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background).imePadding()) {
                Box(Modifier.weight(1f).fillMaxWidth()) {
                    SharedTransitionLayout {
                        CompositionLocalProvider(LocalSharedTransitionScope provides this) {
                            LearnerNavHost(graph, nav, start, config != null)
                        }
                    }
                }
                val current = now
                if (current != null && !onPlayer) {
                    MiniPlayer(current, playback,
                        onOpen = { nav.navigate(PlayerRoute(current.key.courseId, current.key.number, current.label, current.title)) },
                        onToggle = graph.playback::toggle)
                }
                Spacer(Modifier.navigationBarsPadding())
            }
        }
    }
}

@Composable
private fun LearnerNavHost(graph: AppGraph, nav: NavHostController, start: Any, configured: Boolean) {
    NavHost(
        navController = nav, startDestination = start, modifier = Modifier.fillMaxSize(),
        // Start/End follow the layout direction, so "forward" slides the RTL way.
        enterTransition = { slideIntoContainer(AnimatedContentTransitionScope.SlideDirection.Start, tween(NAV_MS)) + fadeIn(tween(NAV_MS)) },
        exitTransition = { slideOutOfContainer(AnimatedContentTransitionScope.SlideDirection.Start, tween(NAV_MS)) + fadeOut(tween(NAV_MS)) },
        popEnterTransition = { slideIntoContainer(AnimatedContentTransitionScope.SlideDirection.End, tween(NAV_MS)) + fadeIn(tween(NAV_MS)) },
        popExitTransition = { slideOutOfContainer(AnimatedContentTransitionScope.SlideDirection.End, tween(NAV_MS)) + fadeOut(tween(NAV_MS)) },
    ) {
        composable<SetupRoute> {
            Animated(this) {
                val vm = viewModel {
                    SetupViewModel(
                        initialUrl = graph.serverConfig.savedUrl.ifBlank { BuildConfig.DEFAULT_API_BASE_URL },
                        allowCleartextLocal = BuildConfig.ALLOW_CLEARTEXT_LOCAL,
                        check = { config -> graph.apiFor(config).meta() },
                        save = { config ->
                            graph.serverConfig.save(config)
                            graph.repository.clearCache()
                        },
                    )
                }
                val state by vm.state.collectAsStateWithLifecycle()
                LaunchedEffect(state.done) {
                    if (state.done) nav.navigate(LibraryRoute) { popUpTo(0) { inclusive = true } }
                }
                Box(Modifier.statusBarsPadding()) {
                    SetupScreen(state, vm::onUrl, vm::onToken, vm::toggleToken, vm::connect)
                }
            }
        }
        composable<LibraryRoute> {
            Animated(this) {
                val vm = viewModel { LibraryViewModel(graph.repository) }
                val courses by vm.courses.collectAsStateWithLifecycle()
                val learner by graph.localStore.state.collectAsStateWithLifecycle()
                val now by graph.playback.nowPlaying.collectAsStateWithLifecycle()
                val last = learner.lastPlayed
                val continueItem = remember(now, last, courses.data, learner.progress) {
                    val key = now?.key ?: last ?: return@remember null
                    val progress = learner.progress[key.id]
                    if (progress?.completed == true) return@remember null
                    val title = courses.data?.courses?.firstOrNull { it.courseId == key.courseId }?.title ?: key.courseId
                    ContinueItem(title, now?.label ?: "%02d".format(key.number), now?.title ?: "",
                        ((progress?.durationMs ?: 0) - (progress?.positionMs ?: 0)).coerceAtLeast(0))
                }
                LibraryScreen(
                    courses = courses, learner = learner, continueItem = continueItem,
                    onOpenCourse = { nav.navigate(CourseRoute(it.courseId, it.title)) },
                    onContinue = {
                        val key = now?.key ?: last
                        if (key != null) nav.navigate(PlayerRoute(key.courseId, key.number, continueItem?.label ?: "", continueItem?.title ?: ""))
                    },
                    onRefresh = vm::refresh,
                    onSettings = { nav.navigate(SettingsRoute) },
                )
                if (!configured) LaunchedEffect(Unit) { nav.navigate(SetupRoute) }
            }
        }
        composable<CourseRoute> { backStack ->
            Animated(this) {
                val route = backStack.toRoute<CourseRoute>()
                val vm = viewModel(key = route.courseId) { CourseViewModel(route.courseId, graph.repository, graph.localStore) }
                val course by vm.course.collectAsStateWithLifecycle()
                val learner by graph.localStore.state.collectAsStateWithLifecycle()
                val downloadStates by graph.downloadStates.states.collectAsStateWithLifecycle()
                val detail = course.data
                val resume = remember(detail, learner) { detail?.let { vm.resumeTarget(it, learner) } }
                val open: (EpisodeSummary) -> Unit = { e -> nav.navigate(PlayerRoute(route.courseId, e.number, e.label, e.title)) }
                CourseScreen(
                    course = course, learner = learner,
                    downloads = { e -> downloadStates.let { graph.downloadStates.stateOf(route.courseId, e.label) } },
                    resume = resume, titleFallback = route.title,
                    onBack = { nav.popBackStack() }, onRefresh = vm::refresh,
                    onConcepts = { nav.navigate(ConceptsRoute(route.courseId)) },
                    actions = EpisodeRowActions(
                        open = open,
                        download = { e -> graph.downloads.enqueue(route.courseId, e.number, e.label) },
                        cancelDownload = { e -> graph.downloads.cancel(route.courseId, e.label) },
                        deleteDownload = { e -> graph.downloads.delete(route.courseId, e.label) },
                        setCompleted = { e, done -> vm.setCompleted(e.number, done) },
                    ),
                )
            }
        }
        composable<PlayerRoute> { backStack ->
            Animated(this) {
                val route = backStack.toRoute<PlayerRoute>()
                val vm = viewModel(key = "${route.courseId}/${route.number}") {
                    PlayerViewModel(route.courseId, route.number, graph.repository, graph.localStore, graph.playback)
                }
                val ui by vm.ui.collectAsStateWithLifecycle()
                PlayerScreen(
                    courseId = route.courseId, number = route.number, label = route.label, fallbackTitle = route.title, ui = ui,
                    actions = PlayerActions(
                        togglePlay = vm::togglePlay, skip = vm::skip, seekToMs = vm::seekToMs, seekToSeconds = vm::seekToSeconds,
                        previousSection = vm::previousSection, nextSection = vm::nextSection, setSpeed = vm::setSpeed,
                        setSleep = vm::setSleep, continueAfterGate = vm::continueAfterGate, revealAtGate = vm::revealAtGate,
                        reveal = vm::reveal, listenToAnswer = vm::listenToAnswer, addBookmark = vm::addBookmark,
                        editBookmark = vm::editBookmark, deleteBookmark = vm::deleteBookmark, updateSettings = vm::updateSettings,
                        dismissMessage = vm::dismissMessage, refresh = vm::refresh,
                    ),
                    onBack = { nav.popBackStack() },
                )
            }
        }
        composable<ConceptsRoute> { backStack ->
            Animated(this) {
                val route = backStack.toRoute<ConceptsRoute>()
                val vm = viewModel(key = "concepts/${route.courseId}") { ConceptsViewModel(route.courseId, graph.repository) }
                val concepts by vm.concepts.collectAsStateWithLifecycle()
                ConceptsScreen(
                    concepts = concepts, onBack = { nav.popBackStack() }, onRefresh = vm::refresh,
                    onOpenEpisode = { n -> nav.navigate(PlayerRoute(route.courseId, n, "%02d".format(n), "")) },
                )
            }
        }
        composable<SettingsRoute> {
            Animated(this) {
                val learner by graph.localStore.state.collectAsStateWithLifecycle()
                val states by graph.downloadStates.states.collectAsStateWithLifecycle()
                val bytes by produceState(0L, states) { value = withContext(Dispatchers.IO) { graph.episodeFiles.totalBytes() } }
                val scope = graph.appScope
                SettingsScreen(
                    settings = learner.settings, storageBytes = bytes,
                    onChange = { t -> scope.launch { graph.localStore.updateSettings(t) } },
                    onDeleteDownloads = graph.downloads::deleteAll,
                    onChangeServer = { nav.navigate(SetupRoute) },
                    onBack = { nav.popBackStack() },
                )
            }
        }
    }
}

@Composable
private fun Animated(scope: androidx.compose.animation.AnimatedContentScope, content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalNavAnimatedScope provides scope) { content() }
}
