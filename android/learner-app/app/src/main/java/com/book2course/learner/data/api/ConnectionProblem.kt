package com.book2course.learner.data.api

import kotlinx.serialization.SerializationException
import retrofit2.HttpException
import java.io.IOException
import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.PortUnreachableException
import java.net.SocketException
import java.net.UnknownHostException
import java.security.cert.CertPathValidatorException
import java.security.cert.CertificateException
import javax.net.ssl.SSLException

/**
 * Why the setup check (`GET /v3/learner/meta`) failed, so the setup screen can name the cause and
 * what to do about it instead of one "no connection" message.
 */
enum class ConnectionProblem {
    /** The server name does not resolve: on a tailnet-only API (D-030) this means Tailscale is off. */
    NO_TAILNET,
    /** Timeout, refused connection, no route. */
    UNREACHABLE,
    /** TLS handshake failed: untrusted chain, wrong host name, or not a TLS server. */
    CERTIFICATE,
    /** 401 / 403. */
    BAD_TOKEN,
    /** The server's `min_client_version` is above this app's, or HTTP 426. */
    CLIENT_TOO_OLD,
    /** 5xx. */
    SERVER_ERROR,
    /** The response does not match this app's contract (422, unparsable body). */
    INCOMPATIBLE,
    UNKNOWN,
}

/** The server needs a newer app (`LearnerMeta.minClientVersion` > `LearnerApi.CLIENT_VERSION`). */
class ClientTooOldException(val required: Int, val supported: Int) :
    Exception("server requires client version $required; this app supports $supported")

private fun Throwable.causes(): Sequence<Throwable> = generateSequence(this) { it.cause.takeIf { c -> c !== it } }.take(8)

fun Throwable.connectionProblem(): ConnectionProblem {
    val chain = causes().toList()
    // Certificate problems first: SSLHandshakeException is also an IOException.
    if (chain.any { it is SSLException || it is CertPathValidatorException || it is CertificateException }) {
        return ConnectionProblem.CERTIFICATE
    }
    for (e in chain) {
        when (e) {
            is ClientTooOldException -> return ConnectionProblem.CLIENT_TOO_OLD
            is HttpException -> return when (val code = e.code()) {
                401, 403 -> ConnectionProblem.BAD_TOKEN
                426 -> ConnectionProblem.CLIENT_TOO_OLD
                422 -> ConnectionProblem.INCOMPATIBLE
                in 500..599 -> ConnectionProblem.SERVER_ERROR
                else -> if (code == 404) ConnectionProblem.INCOMPATIBLE else ConnectionProblem.UNKNOWN
            }
            is AppErrorException -> return when (e.error) {
                AppError.Unauthorized -> ConnectionProblem.BAD_TOKEN
                AppError.ServerUnavailable -> ConnectionProblem.SERVER_ERROR
                AppError.Incompatible -> ConnectionProblem.INCOMPATIBLE
                AppError.Offline -> ConnectionProblem.UNREACHABLE
                else -> ConnectionProblem.UNKNOWN
            }
            is UnknownHostException -> return ConnectionProblem.NO_TAILNET
            is InterruptedIOException, is ConnectException, is NoRouteToHostException, is PortUnreachableException,
            is SocketException -> return ConnectionProblem.UNREACHABLE
            is SerializationException, is IllegalArgumentException -> return ConnectionProblem.INCOMPATIBLE
        }
    }
    return if (chain.any { it is IOException }) ConnectionProblem.UNREACHABLE else ConnectionProblem.UNKNOWN
}

private val BEARER = Regex("""(?i)(bearer\s+)[^\s"',;]+""")

/**
 * Exception type and message of the failure and its causes, for "copy error details". The token is
 * never included: each [secrets] value and any bearer credential are replaced by `[redacted]`.
 */
fun Throwable.errorDetails(secrets: Collection<String> = emptyList()): String {
    val text = causes().joinToString("\nCaused by: ") { e -> e.javaClass.name + (e.message?.let { ": $it" } ?: "") }
    var out = text
    for (s in secrets.filter { it.isNotBlank() }.sortedByDescending { it.length }) out = out.replace(s, "[redacted]")
    return BEARER.replace(out) { it.groupValues[1] + "[redacted]" }
}
