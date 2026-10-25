package com.book2course.learner.ui.setup

import androidx.annotation.StringRes
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.book2course.learner.R
import com.book2course.learner.data.api.AppErrorException
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.LearnerMeta
import com.book2course.learner.data.api.toAppError
import com.book2course.learner.data.config.ServerConfig
import com.book2course.learner.data.config.UrlCheck
import com.book2course.learner.data.config.normalizeServerUrl
import com.book2course.learner.ui.common.message
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class SetupState(
    val url: String = "",
    val token: String = "",
    val showToken: Boolean = false,
    val checking: Boolean = false,
    @StringRes val error: Int? = null,
    val done: Boolean = false,
)

class SetupViewModel(
    initialUrl: String,
    private val allowCleartextLocal: Boolean,
    private val check: suspend (ServerConfig) -> LearnerMeta,
    private val save: (ServerConfig) -> Unit,
) : ViewModel() {
    private val _state = MutableStateFlow(SetupState(url = initialUrl))
    val state: StateFlow<SetupState> = _state.asStateFlow()

    fun onUrl(value: String) = _state.update { it.copy(url = value, error = null) }
    fun onToken(value: String) = _state.update { it.copy(token = value, error = null) }
    fun toggleToken() = _state.update { it.copy(showToken = !it.showToken) }

    fun connect() {
        val s = _state.value
        val url = when (val r = normalizeServerUrl(s.url, allowCleartextLocal)) {
            is UrlCheck.Ok -> r.url
            UrlCheck.Empty -> return fail(R.string.setup_url_empty)
            UrlCheck.NotHttps -> return fail(R.string.setup_url_not_https)
            UrlCheck.Invalid -> return fail(R.string.setup_url_invalid)
        }
        if (s.token.isBlank()) return fail(R.string.setup_token_empty)
        val config = ServerConfig(url, s.token.trim())
        _state.update { it.copy(checking = true, error = null) }
        viewModelScope.launch {
            try {
                val meta = check(config)
                if (meta.minClientVersion > LearnerApi.CLIENT_VERSION) {
                    fail(R.string.setup_client_too_old)
                } else {
                    save(config)
                    _state.update { it.copy(checking = false, done = true, url = url) }
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val error = (e as? AppErrorException)?.error ?: e.toAppError()
                fail(error.message())
            }
        }
    }

    private fun fail(@StringRes message: Int) = _state.update { it.copy(checking = false, error = message) }
}

@Composable
fun SetupScreen(state: SetupState, onUrl: (String) -> Unit, onToken: (String) -> Unit, onToggleToken: () -> Unit, onConnect: () -> Unit) {
    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(stringResource(R.string.setup_title), style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
        Text(stringResource(R.string.setup_intro), style = MaterialTheme.typography.bodyLarge)
        // URLs and tokens are Latin text: keep them left-to-right inside the RTL screen.
        CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Ltr) {
            OutlinedTextField(
                value = state.url, onValueChange = onUrl, singleLine = true,
                label = { Text(stringResource(R.string.setup_url)) },
                placeholder = { Text(stringResource(R.string.setup_url_hint)) },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = state.token, onValueChange = onToken, singleLine = true,
                label = { Text(stringResource(R.string.setup_token)) },
                visualTransformation = if (state.showToken) VisualTransformation.None else PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
                trailingIcon = {
                    IconButton(onClick = onToggleToken) {
                        Icon(
                            if (state.showToken) Icons.Filled.VisibilityOff else Icons.Filled.Visibility,
                            contentDescription = stringResource(if (state.showToken) R.string.setup_hide_token else R.string.setup_show_token),
                        )
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        state.error?.let {
            Text(stringResource(it), color = MaterialTheme.colorScheme.error,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Assertive })
        }
        if (state.checking) {
            Text(stringResource(R.string.setup_checking), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        Button(onClick = onConnect, enabled = !state.checking, modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp)) {
            Text(stringResource(R.string.setup_connect))
        }
    }
}
