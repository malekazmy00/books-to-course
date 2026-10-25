package com.book2course.learner.ui.common

import androidx.compose.animation.AnimatedVisibilityScope
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.Modifier

/** Set by the nav host; null in previews and UI tests, where the modifier is a no-op. */
@OptIn(ExperimentalSharedTransitionApi::class)
val LocalSharedTransitionScope = compositionLocalOf<SharedTransitionScope?> { null }
val LocalNavAnimatedScope = compositionLocalOf<AnimatedVisibilityScope?> { null }

/** The episode label/title moves from the episode list into the player header. */
@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
fun Modifier.sharedEpisode(key: String): Modifier {
    val shared = LocalSharedTransitionScope.current ?: return this
    val animated = LocalNavAnimatedScope.current ?: return this
    return with(shared) { this@sharedEpisode.sharedBounds(rememberSharedContentState(key), animated) }
}
