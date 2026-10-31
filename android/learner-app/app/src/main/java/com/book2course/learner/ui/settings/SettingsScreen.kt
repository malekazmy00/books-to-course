package com.book2course.learner.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.book2course.learner.R
import com.book2course.learner.data.local.LearnerSettings
import com.book2course.learner.data.local.ThemeMode
import com.book2course.learner.ui.common.Heading
import com.book2course.learner.ui.common.LearnerScaffold
import java.util.Locale

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(
    settings: LearnerSettings,
    storageBytes: Long,
    onChange: ((LearnerSettings) -> LearnerSettings) -> Unit,
    onDeleteDownloads: () -> Unit,
    onChangeServer: () -> Unit,
    onOpenOperator: () -> Unit,
    onBack: () -> Unit,
) {
    LearnerScaffold(title = stringResource(R.string.settings_title), onBack = onBack) {
        Column(Modifier.verticalScroll(rememberScrollState()).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Heading(stringResource(R.string.settings_listening))
            ChoiceRow(stringResource(R.string.settings_skip_back), LearnerSettings.SKIP_CHOICES, settings.skipBackSeconds) { v -> onChange { it.copy(skipBackSeconds = v) } }
            ChoiceRow(stringResource(R.string.settings_skip_forward), LearnerSettings.SKIP_CHOICES, settings.skipForwardSeconds) { v -> onChange { it.copy(skipForwardSeconds = v) } }
            SwitchRow(stringResource(R.string.settings_pause_answers), settings.pauseBeforeAnswers) { v -> onChange { it.copy(pauseBeforeAnswers = v) } }
            SwitchRow(stringResource(R.string.settings_follow), settings.followTranscript) { v -> onChange { it.copy(followTranscript = v) } }
            HorizontalDivider()
            Heading(stringResource(R.string.settings_display))
            val fontLabel = stringResource(R.string.settings_font)
            Text(fontLabel + ": " + String.format(Locale.ROOT, "%.0f٪", settings.transcriptScale * 100))
            Slider(
                value = settings.transcriptScale, valueRange = LearnerSettings.MIN_SCALE..LearnerSettings.MAX_SCALE, steps = 7,
                onValueChange = { v -> onChange { it.copy(transcriptScale = v) } },
                modifier = Modifier.semantics { contentDescription = fontLabel },
            )
            Text(stringResource(R.string.settings_font_sample), style = MaterialTheme.typography.bodyLarge.let {
                it.copy(fontSize = it.fontSize * settings.transcriptScale, lineHeight = it.lineHeight * settings.transcriptScale)
            })
            Text(stringResource(R.string.settings_theme))
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                ThemeMode.entries.forEach { mode ->
                    FilterChip(selected = settings.theme == mode, onClick = { onChange { it.copy(theme = mode) } },
                        label = { Text(stringResource(themeName(mode))) }, modifier = Modifier.heightIn(min = 48.dp))
                }
            }
            HorizontalDivider()
            Heading(stringResource(R.string.settings_storage))
            Text(stringResource(R.string.settings_storage_used, humanBytes(storageBytes)))
            OutlinedButton(onClick = onDeleteDownloads, enabled = storageBytes > 0, modifier = Modifier.heightIn(min = 48.dp)) {
                Text(stringResource(R.string.settings_delete_downloads))
            }
            HorizontalDivider()
            Heading("إدارة المعالجة")
            Text("إعداد المراحل والنماذج، اختيار ناتج كل كتاب، متابعة الأعمال، بدء المراحل، الإيقاف الآمن والاستئناف والتصدير.")
            OutlinedButton(onClick = onOpenOperator, modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp)) { Text("فتح لوحة التشغيل الكاملة") }
            HorizontalDivider()
            Heading(stringResource(R.string.settings_server))
            OutlinedButton(onClick = onChangeServer, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.settings_change_server)) }
        }
    }
}

private fun themeName(mode: ThemeMode) = when (mode) {
    ThemeMode.SYSTEM -> R.string.theme_system
    ThemeMode.LIGHT -> R.string.theme_light
    ThemeMode.DARK -> R.string.theme_dark
    ThemeMode.SEPIA -> R.string.theme_sepia
}

fun humanBytes(bytes: Long): String = when {
    bytes >= 1L shl 30 -> String.format(Locale.ROOT, "%.1f GB", bytes / (1L shl 30).toDouble())
    bytes >= 1L shl 20 -> String.format(Locale.ROOT, "%.1f MB", bytes / (1L shl 20).toDouble())
    bytes >= 1L shl 10 -> String.format(Locale.ROOT, "%.0f KB", bytes / (1L shl 10).toDouble())
    else -> "$bytes B"
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChoiceRow(title: String, choices: List<Int>, value: Int, onSelect: (Int) -> Unit) {
    Text(title)
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        choices.forEach { c ->
            FilterChip(selected = value == c, onClick = { onSelect(c) }, label = { Text(stringResource(R.string.settings_seconds, c)) },
                modifier = Modifier.heightIn(min = 48.dp))
        }
    }
}

@Composable
private fun SwitchRow(title: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).toggleable(value = checked, role = Role.Switch, onValueChange = onChange),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(title, Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = null)
    }
}
