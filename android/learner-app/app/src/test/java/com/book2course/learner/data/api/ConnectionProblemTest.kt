package com.book2course.learner.data.api

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.IOException
import java.net.UnknownHostException
import java.security.cert.CertPathValidatorException
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

/**
 * The setup check (`GET /v3/learner/meta`) against a real OkHttp/Retrofit client and MockWebServer:
 * every way it can fail maps to its own problem, so the learner gets a message that names the cause.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 35]) // Android 7.0 (minSdk) and current
class ConnectionProblemTest {
    private lateinit var server: MockWebServer
    private val token = "s3cr3t-T0KEN"

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() = server.shutdown()

    private fun api(baseUrl: String = server.url("/").toString(), tweak: (okhttp3.OkHttpClient.Builder) -> Unit = {}): LearnerApi {
        val host = server.hostName
        val client = httpClient(AuthInterceptor({ host }, { token })).newBuilder().apply(tweak).build()
        return LearnerApi.create(baseUrl, client)
    }

    private suspend fun failureOf(api: LearnerApi): Throwable {
        try {
            api.meta()
        } catch (e: Exception) {
            return e
        }
        fail("meta() should have failed")
        throw AssertionError()
    }

    private fun status(code: Int) = MockResponse().setResponseCode(code).setBody("{}")

    @Test
    fun `unknown host means Tailscale is off on this device`() = runTest {
        val noDns = object : okhttp3.Dns {
            override fun lookup(hostname: String): List<java.net.InetAddress> =
                throw UnknownHostException("Unable to resolve host \"$hostname\": No address associated with hostname")
        }
        val e = failureOf(api { it.dns(noDns) })
        assertTrue(e.toString(), e is UnknownHostException)
        assertEquals(ConnectionProblem.NO_TAILNET, e.connectionProblem())
    }

    @Test
    fun `refused connection and timeout are unreachable`() = runTest {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
        val slow = failureOf(api { it.readTimeout(300, TimeUnit.MILLISECONDS) })
        assertEquals(ConnectionProblem.UNREACHABLE, slow.connectionProblem())

        val url = server.url("/").toString()
        server.shutdown()
        val refused = failureOf(api(url))
        assertEquals(ConnectionProblem.UNREACHABLE, refused.connectionProblem())
    }

    @Test
    fun `a TLS failure is a certificate problem`() = runTest {
        // HTTPS against a server that answers in plain text fails during the TLS handshake.
        val plain = java.net.ServerSocket(0, 1, java.net.InetAddress.getByName("127.0.0.1"))
        val answer = Thread {
            runCatching {
                plain.accept().use { it.getOutputStream().write("HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n".toByteArray()) }
            }
        }.apply { start() }
        val e = try {
            failureOf(api("https://127.0.0.1:${plain.localPort}/"))
        } finally {
            plain.close()
            answer.join(2_000)
        }
        assertEquals(e.toString(), ConnectionProblem.CERTIFICATE, e.connectionProblem())
        // The shapes Android reports for an untrusted chain (Android 7.0 without ISRG roots) or a wrong host.
        val untrusted = SSLHandshakeException("java.security.cert.CertPathValidatorException: Trust anchor for certification path not found.")
            .apply { initCause(CertPathValidatorException("Trust anchor for certification path not found.")) }
        assertEquals(ConnectionProblem.CERTIFICATE, untrusted.connectionProblem())
        assertEquals(ConnectionProblem.CERTIFICATE, IOException("wrapped", CertPathValidatorException("x")).connectionProblem())
        assertEquals(ConnectionProblem.CERTIFICATE, SSLPeerUnverifiedException("Hostname not verified").connectionProblem())
    }

    @Test
    fun `401 and 403 are a wrong token, 5xx a server error, 426 an old client`() = runTest {
        for ((code, expected) in listOf(
            401 to ConnectionProblem.BAD_TOKEN, 403 to ConnectionProblem.BAD_TOKEN,
            500 to ConnectionProblem.SERVER_ERROR, 502 to ConnectionProblem.SERVER_ERROR,
            503 to ConnectionProblem.SERVER_ERROR, 504 to ConnectionProblem.SERVER_ERROR,
            426 to ConnectionProblem.CLIENT_TOO_OLD, 422 to ConnectionProblem.INCOMPATIBLE,
        )) {
            server.enqueue(status(code))
            assertEquals("HTTP $code", expected, failureOf(api()).connectionProblem())
        }
        assertEquals(ConnectionProblem.CLIENT_TOO_OLD, ClientTooOldException(required = 2, supported = 1).connectionProblem())
    }

    @Test
    fun `error details carry the exception type and message but never the token`() {
        val e = IOException("failed for https://host.ts.net/?token=$token with Authorization: Bearer $token",
            SSLHandshakeException("handshake $token"))
        val details = e.errorDetails(secrets = listOf(token))
        assertTrue(details, details.contains("java.io.IOException"))
        assertTrue(details, details.contains("javax.net.ssl.SSLHandshakeException"))
        assertTrue(details, details.contains("failed for https://host.ts.net/"))
        assertFalse(details, details.contains(token))
        assertFalse(details, details.contains("s3cr3t"))
        // A bearer value is removed even when the caller did not name it as a secret.
        val other = IOException("Authorization: Bearer abc.def-123").errorDetails()
        assertFalse(other, other.contains("abc.def-123"))
    }
}
