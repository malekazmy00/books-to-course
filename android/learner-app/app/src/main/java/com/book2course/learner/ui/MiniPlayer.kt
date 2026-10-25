package com.book2course.learner.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.book2course.learner.R
import com.book2course.learner.playback.NowPlaying
import com.book2course.learner.playback.PlaybackState

/** Persistent bar on every screen except the player: what is playing, play/pause, open the player. */
@Composable
fun MiniPlayer(now: NowPlaying, playback: PlaybackState, onOpen: () -> Unit, onToggle: () -> Unit) {
    val description = stringResource(R.string.mini_player_desc, "${now.label} · ${now.title}")
    val openLabel = stringResource(R.string.player_open)
    Surface(tonalElevation = 3.dp, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
        Column {
            if (playback.durationMs > 0) {
                LinearProgressIndicator(
                    progress = { (playback.positionMs.toFloat() / playback.durationMs).coerceIn(0f, 1f) },
                    modifier = Modifier.fillMaxWidth().semantics { contentDescription = "" },
                )
            }
            Row(Modifier.fillMaxWidth().heightIn(min = 64.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(
                    Modifier.weight(1f).clickable(onClickLabel = openLabel, onClick = onOpen)
                        .semantics(mergeDescendants = true) { contentDescription = description }
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                ) {
                    Text("${now.label} · ${now.title}", style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(now.courseTitle, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                IconButton(onClick = onToggle, modifier = Modifier.size(56.dp)) {
                    Icon(
                        if (playback.isPlaying) Icons.Filled.Pause else Icons.Filled.PlayArrow,
                        stringResource(if (playback.isPlaying) R.string.player_pause else R.string.player_play),
                    )
                }
            }
        }
    }
}
