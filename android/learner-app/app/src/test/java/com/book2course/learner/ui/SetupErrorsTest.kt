package com.book2course.learner.ui

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.LayoutDirection
import androidx.test.core.app.ApplicationProvider
import com.book2course.learner.R
import com.book2course.learner.data.api.AuthInterceptor
import com.book2course.learner.data.api.LearnerApi
import com.book2course.learner.data.api.LearnerMeta
import com.book2course.learner.data.api.httpClient
import com.book2course.learner.data.config.ServerConfig
import com.book2course.learner.data.local.ThemeMode
import com.book2course.learner.ui.setup.SetupScreen
import com.book2course.learner.ui.setup.SetupState
import com.book2course.learner.ui.setup.SetupViewModel
import com.book2course.learner.ui.theme.LearnerTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.net.UnknownHostException

/** The setup screen names the cause of a failed connection check, and can copy its details without the token. */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 35]) // Android 7.0 (minSdk) and current
class SetupErrorsTest {
    @get:Rule
    val rule = createComposeRule()
    private val context: Context get() = ApplicationProvider.getApplicationContext()
    private val token = "s3cr3t-T0KEN"
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
        Dispatchers.resetMain()
    }

    private fun vm(check: suspend (ServerConfig) -> LearnerMeta) = SetupViewModel("", allowCleartextLocal = true, check = check, save = {})
        .apply { onToken(token) }

    private fun realCheck(): suspend (ServerConfig) -> LearnerMeta = { config ->
        LearnerApi.create(config.baseUrl, httpClient(AuthInterceptor({ config.host }, { config.token }))).meta()
    }

    private fun local() = "http://127.0.0.1:${server.port}/"

    private fun SetupViewModel.connectTo(url: String): SetupState {
        onUrl(url)
        connect()
        val deadline = System.currentTimeMillis() + 10_000
        while (state.value.checking && System.currentTimeMillis() < deadline) Thread.sleep(20)
        return state.value
    }

    @Test
    fun `the Tailscale message is the agreed text`() {
        assertEquals("تطبيق Tailscale غير مفعّل أو غير متصل على هذا الجهاز. افتحه وفعّله ثم أعد المحاولة.",
            context.getString(R.string.setup_error_no_tailnet))
        assertTrue(context.getString(R.string.setup_error_bad_token).startsWith("الرمز غير صحيح"))
        assertEquals("نسخ تفاصيل الخطأ", context.getString(R.string.setup_copy_details))
    }

    @Test
    fun `each failure gets its own message, with details for a failed check only`() {
        val noTailnet = vm { throw UnknownHostException("Unable to resolve host \"host.ts.net\"") }.connectTo("https://host.ts.net")
        assertEquals(R.string.setup_error_no_tailnet, noTailnet.error)
        assertTrue(noTailnet.errorDetails!!, noTailnet.errorDetails!!.contains("java.net.UnknownHostException"))

        server.enqueue(MockResponse().setResponseCode(401))
        val badToken = vm(realCheck()).connectTo(local())
        assertEquals(R.string.setup_error_bad_token, badToken.error)
        assertEquals("Bearer $token", server.takeRequest().getHeader("Authorization"))
        assertFalse(badToken.errorDetails!!, badToken.errorDetails!!.contains(token))

        server.enqueue(MockResponse().setResponseCode(503))
        assertEquals(R.string.setup_error_server, vm(realCheck()).connectTo(local()).error)

        server.enqueue(MockResponse().setHeader("Content-Type", "application/json")
            .setBody("""{"api_version":"3.0.0","min_client_version":99,"model_revision":"m"}"""))
        val old = vm(realCheck()).connectTo(local())
        assertEquals(R.string.setup_client_too_old, old.error)
        assertNotNull(old.errorDetails)

        // A message containing the token never reaches the details.
        val leaky = vm { throw java.io.IOException("refused for token $token") }.connectTo("https://host.ts.net")
        assertEquals(R.string.setup_error_unreachable, leaky.error)
        assertFalse(leaky.errorDetails!!, leaky.errorDetails!!.contains(token))

        // Validation errors are not connection failures: no details to copy.
        val invalid = vm { error("not called") }.connectTo("")
        assertEquals(R.string.setup_url_empty, invalid.error)
        assertNull(invalid.errorDetails)
    }

    @Test
    fun `copy button puts the details on the clipboard`() {
        val details = "java.net.UnknownHostException: Unable to resolve host \"host.ts.net\""
        rule.setContent {
            LearnerTheme(ThemeMode.LIGHT) {
                CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
                    SetupScreen(SetupState(url = "https://host.ts.net", token = token, error = R.string.setup_error_no_tailnet,
                        errorDetails = details), {}, {}, {}, {})
                }
            }
        }
        rule.onNodeWithText(context.getString(R.string.setup_error_no_tailnet)).assertIsDisplayed()
        rule.onNodeWithText("نسخ تفاصيل الخطأ").assertIsDisplayed().performClick()
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val copied = clipboard.primaryClip!!.getItemAt(0).text.toString()
        assertEquals(details, copied)
        assertFalse(copied.contains(token))
    }

    @Test
    fun `no copy button without a failed check`() {
        rule.setContent {
            LearnerTheme(ThemeMode.LIGHT) {
                SetupScreen(SetupState(error = R.string.setup_url_empty), {}, {}, {}, {})
            }
        }
        rule.onNodeWithText("نسخ تفاصيل الخطأ").assertDoesNotExist()
    }
}
