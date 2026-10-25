package com.book2course.learner.playback

import android.content.ComponentName
import android.content.Context
import android.net.Uri
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * [PlayerConnection] over a Media3 [MediaController] bound to [PlaybackService]. Playback lives in
 * the service, so it continues in the background, on the lock screen and with Bluetooth buttons.
 * Commands issued before the controller is connected are queued and replayed.
 */
class MediaControllerConnection(
    private val context: Context,
    private val scope: CoroutineScope,
) : PlayerConnection {
    private val _state = MutableStateFlow(PlaybackState())
    override val state: StateFlow<PlaybackState> = _state.asStateFlow()
    private var controller: MediaController? = null
    private var connecting = false
    private val pending = ArrayDeque<(MediaController) -> Unit>()
    private var ticker: Job? = null

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) = publish(player)
        override fun onPlayerError(error: PlaybackException) {
            _state.value = _state.value.copy(error = error.errorCodeName)
        }
    }

    private fun connect() {
        if (controller != null || connecting) return
        connecting = true
        val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val future = MediaController.Builder(context, token).buildAsync()
        future.addListener({
            connecting = false
            val c = runCatching { future.get() }.getOrNull() ?: return@addListener
            controller = c
            c.addListener(listener)
            while (pending.isNotEmpty()) pending.removeFirst().invoke(c)
            publish(c)
        }, ContextCompat.getMainExecutor(context))
    }

    private fun withController(action: (MediaController) -> Unit) {
        controller?.let(action) ?: run {
            pending.addLast(action)
            connect()
        }
    }

    private fun publish(player: Player) {
        _state.value = PlaybackState(
            mediaId = player.currentMediaItem?.mediaId,
            isPlaying = player.isPlaying,
            isBuffering = player.playbackState == Player.STATE_BUFFERING,
            positionMs = player.currentPosition.coerceAtLeast(0),
            durationMs = player.duration.takeIf { it > 0 } ?: 0,
            speed = player.playbackParameters.speed,
            ended = player.playbackState == Player.STATE_ENDED,
            error = player.playerError?.errorCodeName,
        )
        if (player.isPlaying) startTicker() else stopTicker()
    }

    /** Position is not an event; poll it while playing (4 Hz is enough for transcript sync). */
    private fun startTicker() {
        if (ticker?.isActive == true) return
        ticker = scope.launch {
            while (isActive) {
                controller?.let { c -> _state.value = _state.value.copy(positionMs = c.currentPosition.coerceAtLeast(0)) }
                delay(250)
            }
        }
    }

    private fun stopTicker() {
        ticker?.cancel()
        ticker = null
    }

    override fun setQueue(items: List<EpisodeMedia>, startIndex: Int, startPositionMs: Long, playWhenReady: Boolean) =
        withController { c ->
            c.setMediaItems(items.map { it.toMediaItem() }, startIndex, startPositionMs)
            c.prepare()
            c.playWhenReady = playWhenReady
        }

    override fun play() = withController { it.play() }
    override fun pause() = withController { it.pause() }
    override fun seekTo(positionMs: Long) = withController { it.seekTo(positionMs.coerceAtLeast(0)) }
    override fun setSpeed(speed: Float) = withController { it.playbackParameters = PlaybackParameters(speed) }
}

private fun EpisodeMedia.toMediaItem(): MediaItem = MediaItem.Builder()
    .setMediaId(mediaIdOf(key))
    .setUri(Uri.parse(uri))
    .setMimeType("audio/mpeg")
    .setMediaMetadata(
        MediaMetadata.Builder()
            .setTitle("$label · $title")
            .setArtist(courseTitle)
            .setAlbumTitle(courseTitle)
            .setTrackNumber(key.number + 1)
            .setMediaType(MediaMetadata.MEDIA_TYPE_PODCAST_EPISODE)
            .build(),
    )
    .build()
