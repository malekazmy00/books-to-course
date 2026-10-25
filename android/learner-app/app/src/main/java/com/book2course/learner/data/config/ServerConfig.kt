package com.book2course.learner.data.config

import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.net.URI
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

data class ServerConfig(val baseUrl: String, val token: String) {
    val host: String? get() = runCatching { URI(baseUrl).host }.getOrNull()
}

sealed interface UrlCheck {
    data class Ok(val url: String) : UrlCheck
    data object Empty : UrlCheck
    data object NotHttps : UrlCheck
    data object Invalid : UrlCheck
}

/**
 * The API is tailnet-only behind HTTPS (D-030). Plain http is accepted only for the emulator /
 * loopback in debug builds. User info, query and fragment are refused (they would be sent with
 * every request); a trailing slash is normalised.
 */
fun normalizeServerUrl(input: String, allowCleartextLocal: Boolean): UrlCheck {
    val raw = input.trim()
    if (raw.isEmpty()) return UrlCheck.Empty
    val uri = runCatching { URI(if ("://" in raw) raw else "https://$raw") }.getOrNull() ?: return UrlCheck.Invalid
    val host = uri.host ?: return UrlCheck.Invalid
    if (uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null) return UrlCheck.Invalid
    when (uri.scheme?.lowercase()) {
        "https" -> Unit
        "http" -> if (!(allowCleartextLocal && host in LOCAL_HOSTS)) return UrlCheck.NotHttps
        else -> return UrlCheck.Invalid
    }
    val port = if (uri.port == -1) "" else ":${uri.port}"
    val path = (uri.rawPath ?: "").trimEnd('/')
    return UrlCheck.Ok("${uri.scheme.lowercase()}://${host.lowercase()}$port$path/")
}

private val LOCAL_HOSTS = setOf("10.0.2.2", "localhost", "127.0.0.1")

/** Token secrecy (port of the operator app's TokenVault): AES-GCM with a non-exportable Keystore key. */
interface TokenCipher {
    fun encrypt(plain: String): String
    fun decrypt(cipherText: String): String
}

class KeystoreTokenCipher(private val alias: String = "book2course.learner.api.token.v1") : TokenCipher {
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    override fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val payload = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(payload, Base64.NO_WRAP)
    }

    override fun decrypt(cipherText: String): String {
        val payload = Base64.decode(cipherText, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, payload.copyOfRange(0, 12)))
        return String(cipher.doFinal(payload.copyOfRange(12, payload.size)), Charsets.UTF_8)
    }
}

class ServerConfigStore(private val prefs: SharedPreferences, private val cipher: TokenCipher, defaultUrl: String) {
    private val _config = MutableStateFlow(load(defaultUrl))
    val config: StateFlow<ServerConfig?> = _config.asStateFlow()
    val savedUrl: String get() = prefs.getString(KEY_URL, null) ?: ""

    private fun load(defaultUrl: String): ServerConfig? {
        val url = prefs.getString(KEY_URL, null) ?: defaultUrl.takeIf { it.isNotBlank() } ?: return null
        val encrypted = prefs.getString(KEY_TOKEN, null) ?: return null
        val token = try {
            cipher.decrypt(encrypted)
        } catch (_: Exception) {
            prefs.edit().remove(KEY_TOKEN).apply() // key lost (restore to new device): ask again
            return null
        }
        return ServerConfig(url, token)
    }

    fun save(config: ServerConfig) {
        prefs.edit().putString(KEY_URL, config.baseUrl).putString(KEY_TOKEN, cipher.encrypt(config.token)).apply()
        _config.value = config
    }

    fun clear() {
        prefs.edit().remove(KEY_TOKEN).apply()
        _config.value = null
    }

    private companion object {
        const val KEY_URL = "server_url"
        const val KEY_TOKEN = "server_token_ciphertext"
    }
}
