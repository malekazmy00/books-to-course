package com.book2course.learner.playback

import android.app.PendingIntent
import android.content.Intent
import android.os.Bundle
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.CommandButton
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionError
import androidx.media3.session.SessionResult
import com.book2course.learner.LearnerApp
import com.book2course.learner.MainActivity
import com.book2course.learner.R
import com.google.common.collect.ImmutableList
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture

/**
 * Background playback (Media3 MediaSessionService): media notification, lock screen, Bluetooth
 * and headset buttons, audio focus, pause when headphones are unplugged. Streams go through the
 * app's OkHttp client, whose interceptor adds the bearer token only for the configured server.
 */
@OptIn(UnstableApi::class)
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        val graph = (application as LearnerApp).graph
        val dataSource = DefaultDataSource.Factory(this, OkHttpDataSource.Factory(graph.httpClient))
        val player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSource))
            .setAudioAttributes(
                AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .setSeekBackIncrementMs(15_000)
            .setSeekForwardIncrementMs(30_000)
            .build()
        val openApp = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, player)
            .setSessionActivity(openApp)
            .setCallback(Callback(graph))
            .setCustomLayout(customLayout(graph.localStore.state.value.settings))
            .build()
    }

    private fun customLayout(settings: com.book2course.learner.data.local.LearnerSettings): ImmutableList<CommandButton> = ImmutableList.of(
        CommandButton.Builder(CommandButton.ICON_SKIP_BACK)
            .setDisplayName(getString(R.string.player_back, settings.skipBackSeconds))
            .setCustomIconResId(R.drawable.ic_replay)
            .setSessionCommand(SessionCommand(CMD_BACK, Bundle.EMPTY))
            .build(),
        CommandButton.Builder(CommandButton.ICON_SKIP_FORWARD)
            .setDisplayName(getString(R.string.player_forward, settings.skipForwardSeconds))
            .setCustomIconResId(R.drawable.ic_forward)
            .setSessionCommand(SessionCommand(CMD_FORWARD, Bundle.EMPTY))
            .build(),
    )

    private class Callback(private val graph: com.book2course.learner.AppGraph) : MediaSession.Callback {
        override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult {
            val commands = MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                .add(SessionCommand(CMD_BACK, Bundle.EMPTY))
                .add(SessionCommand(CMD_FORWARD, Bundle.EMPTY))
                .build()
            return MediaSession.ConnectionResult.AcceptedResultBuilder(session)
                .setAvailableSessionCommands(commands)
                .build()
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            val settings = graph.localStore.state.value.settings
            val player = session.player
            when (customCommand.customAction) {
                CMD_BACK -> player.seekTo((player.currentPosition - settings.skipBackSeconds * 1000L).coerceAtLeast(0))
                CMD_FORWARD -> player.seekTo(player.currentPosition + settings.skipForwardSeconds * 1000L)
                else -> return Futures.immediateFuture(SessionResult(SessionError.ERROR_NOT_SUPPORTED))
            }
            return Futures.immediateFuture(SessionResult(SessionResult.RESULT_SUCCESS))
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        val player = session?.player
        if (player == null || !player.playWhenReady || player.mediaItemCount == 0) stopSelf()
    }

    override fun onDestroy() {
        session?.run {
            player.release()
            release()
        }
        session = null
        super.onDestroy()
    }

    companion object {
        const val CMD_BACK = "b2c.skip_back"
        const val CMD_FORWARD = "b2c.skip_forward"
    }
}
