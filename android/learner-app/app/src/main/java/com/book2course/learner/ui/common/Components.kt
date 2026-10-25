package com.book2course.learner.ui.common

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.book2course.learner.R
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.api.Voices
import com.book2course.learner.ui.theme.LocalVoiceColors

@StringRes
fun AppError.message(hasData: Boolean = false): Int = when (this) {
    AppError.NotConfigured -> R.string.error_not_configured
    AppError.Unauthorized -> R.string.error_unauthorized
    AppError.NotFound -> R.string.error_not_found
    AppError.ServerUnavailable -> R.string.error_server
    AppError.Offline -> if (hasData) R.string.error_offline else R.string.error_offline_empty
    AppError.Incompatible -> R.string.error_incompatible
    is AppError.Unexpected -> R.string.error_unexpected
}

/** Non-blocking error line shown above data that is still visible (cached). Announced politely. */
@Composable
fun ErrorBanner(error: AppError, hasData: Boolean, onRetry: (() -> Unit)?, modifier: Modifier = Modifier) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        shape = RoundedCornerShape(12.dp),
        modifier = modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
    ) {
        Row(Modifier.padding(start = 16.dp, end = 8.dp, top = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(error.message(hasData)), Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
            if (onRetry != null) TextButton(onClick = onRetry) { Text(stringResource(R.string.retry)) }
        }
    }
}

@Composable
fun EmptyState(text: String, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(text, style = MaterialTheme.typography.bodyLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Thin, non-blocking refresh indicator (no spinner over content). */
@Composable
fun RefreshBar(refreshing: Boolean) {
    if (refreshing) {
        val label = stringResource(R.string.refreshing)
        LinearProgressIndicator(Modifier.fillMaxWidth().semantics { contentDescription = label })
    }
}

@Composable
fun Heading(text: String, modifier: Modifier = Modifier) {
    Text(
        text, modifier = modifier.semantics { heading() },
        style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
    )
}

@Composable
fun voiceLabel(voice: String, sourceLabel: String? = null): String = when (voice) {
    Voices.AUTHOR -> stringResource(R.string.voice_author)
    Voices.EXTERNAL -> if (sourceLabel != null) stringResource(R.string.voice_external_named, sourceLabel) else stringResource(R.string.voice_external)
    else -> stringResource(R.string.voice_course)
}

/** Voice chip: colour + text, never colour alone. */
@Composable
fun VoiceChip(voice: String, sourceLabel: String? = null, modifier: Modifier = Modifier) {
    val colors = LocalVoiceColors.current
    val color = when (voice) {
        Voices.AUTHOR -> colors.author
        Voices.EXTERNAL -> colors.external
        else -> colors.course
    }
    Surface(color = color.copy(alpha = 0.14f), contentColor = color, shape = RoundedCornerShape(50), modifier = modifier) {
        Text(voiceLabel(voice, sourceLabel), Modifier.padding(horizontal = 10.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.Bold)
    }
}

@Composable
fun sectionTitle(kind: String, fallback: String): String = when (kind) {
    "intro" -> stringResource(R.string.section_intro)
    "book_text" -> stringResource(R.string.section_book_text)
    "summary" -> stringResource(R.string.section_summary)
    "key_terms" -> stringResource(R.string.section_key_terms)
    "questions" -> stringResource(R.string.section_questions)
    else -> fallback
}

@Composable
fun LabeledProgress(fraction: Float, description: String, modifier: Modifier = Modifier) {
    Column(modifier.clearAndSetSemantics { contentDescription = description }, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        LinearProgressIndicator(progress = { fraction }, modifier = Modifier.fillMaxWidth().height(6.dp))
    }
}
