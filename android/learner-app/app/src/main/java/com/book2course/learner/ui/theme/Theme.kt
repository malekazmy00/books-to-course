package com.book2course.learner.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import com.book2course.learner.data.local.ThemeMode

/** Colours chosen for contrast (WCAG 2.2 AA, ≥ 4.5:1 for body text) rather than brand. */
private val Light = lightColorScheme(
    primary = Color(0xFF1F5F5B), onPrimary = Color.White,
    primaryContainer = Color(0xFFBDEAE4), onPrimaryContainer = Color(0xFF00201E),
    secondary = Color(0xFF7A4B12), onSecondary = Color.White,
    secondaryContainer = Color(0xFFFFDDB8), onSecondaryContainer = Color(0xFF2A1700),
    tertiary = Color(0xFF3F4A8A), onTertiary = Color.White,
    tertiaryContainer = Color(0xFFDDE1FF), onTertiaryContainer = Color(0xFF0B154D),
    background = Color(0xFFFBF7F0), onBackground = Color(0xFF1B1C1A),
    surface = Color(0xFFFBF7F0), onSurface = Color(0xFF1B1C1A),
    surfaceVariant = Color(0xFFE3E3DA), onSurfaceVariant = Color(0xFF45483F),
    surfaceContainer = Color(0xFFF1EDE6), surfaceContainerHigh = Color(0xFFEBE7E0),
    error = Color(0xFFB3261E), onError = Color.White,
)

private val Dark = darkColorScheme(
    primary = Color(0xFF8FD4CC), onPrimary = Color(0xFF003733),
    primaryContainer = Color(0xFF00504A), onPrimaryContainer = Color(0xFFBDEAE4),
    secondary = Color(0xFFF5BC7E), onSecondary = Color(0xFF462A00),
    secondaryContainer = Color(0xFF633F00), onSecondaryContainer = Color(0xFFFFDDB8),
    tertiary = Color(0xFFBAC3FF), onTertiary = Color(0xFF232E6F),
    tertiaryContainer = Color(0xFF3A4585), onTertiaryContainer = Color(0xFFDDE1FF),
    background = Color(0xFF121412), onBackground = Color(0xFFE4E3DD),
    surface = Color(0xFF121412), onSurface = Color(0xFFE4E3DD),
    surfaceVariant = Color(0xFF44483F), onSurfaceVariant = Color(0xFFC5C8BC),
    surfaceContainer = Color(0xFF1E201D), surfaceContainerHigh = Color(0xFF292B27),
)

private val Sepia = Light.copy(
    background = Color(0xFFF4ECD8), surface = Color(0xFFF4ECD8), onBackground = Color(0xFF3B2F1E),
    onSurface = Color(0xFF3B2F1E), surfaceContainer = Color(0xFFEDE2C9), surfaceContainerHigh = Color(0xFFE6D9BD),
)

/** Colours of the three voices (D-032): author, course, external. Always paired with a text label. */
data class VoiceColors(val author: Color, val course: Color, val external: Color)

val LocalVoiceColors = staticCompositionLocalOf { VoiceColors(Color(0xFF1F5F5B), Color(0xFF7A4B12), Color(0xFF3F4A8A)) }

@Composable
fun LearnerTheme(mode: ThemeMode, content: @Composable () -> Unit) {
    val dark = when (mode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.DARK -> true
        else -> false
    }
    val scheme: ColorScheme = when {
        dark -> Dark
        mode == ThemeMode.SEPIA -> Sepia
        else -> Light
    }
    val voices = VoiceColors(scheme.primary, scheme.secondary, scheme.tertiary)
    CompositionLocalProvider(LocalVoiceColors provides voices) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}
