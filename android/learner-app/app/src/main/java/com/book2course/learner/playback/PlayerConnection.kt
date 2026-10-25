package com.book2course.learner.playback

import com.book2course.learner.data.local.EpisodeKey
import kotlinx.coroutines.flow.StateFlow

data class PlaybackState(
    val mediaId: String? = null,
    val isPlaying: Boolean = false,
    val isBuffering: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val speed: Float = 1f,
    val ended: Boolean = false,
    val error: String? = null,
) {
    val key: EpisodeKey? get() = mediaId?.let(::episodeKeyOf)
}

data class EpisodeMedia(
    val key: EpisodeKey,
    val uri: String,
    val title: String,
    val courseTitle: String,
    val label: String,
)

fun mediaIdOf(key: EpisodeKey) = "${key.courseId}/${key.number}"

fun episodeKeyOf(mediaId: String): EpisodeKey? {
    val slash = mediaId.lastIndexOf('/')
    if (slash <= 0) return null
    val number = mediaId.substring(slash + 1).toIntOrNull() ?: return null
    return EpisodeKey(mediaId.substring(0, slash), number)
}

/** What the app needs from the player. The real one talks to [PlaybackService] through Media3. */
interface PlayerConnection {
    val state: StateFlow<PlaybackState>
    fun setQueue(items: List<EpisodeMedia>, startIndex: Int, startPositionMs: Long, playWhenReady: Boolean)
    fun play()
    fun pause()
    fun seekTo(positionMs: Long)
    fun setSpeed(speed: Float)
}
