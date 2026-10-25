package com.book2course.learner.compat

import android.app.NotificationManager
import android.content.Context
import android.graphics.drawable.BitmapDrawable
import android.os.Build
import android.os.Bundle
import android.os.Looper
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.session.MediaSession
import androidx.test.core.app.ApplicationProvider
import androidx.work.Configuration
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.testing.SynchronousExecutor
import androidx.work.testing.WorkManagerTestInitHelper
import com.book2course.learner.MainActivity
import com.book2course.learner.R
import com.book2course.learner.data.api.AuthInterceptor
import com.book2course.learner.data.api.httpClient
import com.book2course.learner.data.download.DownloadController
import com.book2course.learner.data.download.DownloadState
import com.book2course.learner.data.download.DownloadStates
import com.book2course.learner.data.download.EpisodeDownloader
import com.book2course.learner.data.download.EpisodeFiles
import com.book2course.learner.playback.PlaybackService
import com.book2course.learner.ui.theme.LearnerTypography
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okio.Buffer
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.xmlpull.v1.XmlPullParser
import java.io.File

/**
 * The oldest supported devices: Android 7.0 / 7.1 (API 24 / 25), often cheap 32-bit phones.
 * Every test also runs on API 35 so that a fix for old devices does not break new ones.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 25, 35])
class OldAndroidTest {
    private val context: Context get() = ApplicationProvider.getApplicationContext()

    @Test
    fun `network security config keeps system roots and adds both ISRG roots`() {
        val parser = context.resources.getXml(R.xml.network_security_config)
        val sources = mutableListOf<String>()
        var inBase = false
        var cleartext: String? = null
        while (parser.next() != XmlPullParser.END_DOCUMENT) {
            if (parser.eventType == XmlPullParser.END_TAG && parser.name == "base-config") inBase = false
            if (parser.eventType != XmlPullParser.START_TAG) continue
            when (parser.name) {
                "base-config" -> {
                    inBase = true
                    cleartext = parser.getAttributeValue("http://schemas.android.com/apk/res/android", "cleartextTrafficPermitted")
                        ?: parser.getAttributeValue(null, "cleartextTrafficPermitted")
                }
                "certificates" -> if (inBase) {
                    val res = parser.getAttributeResourceValue(null, "src", 0)
                    sources += if (res != 0) context.resources.getResourceEntryName(res) else parser.getAttributeValue(null, "src")
                }
            }
        }
        assertEquals("false", cleartext)
        assertEquals(setOf("system", "isrg_root_x1", "isrg_root_x2"), sources.toSet())
        // The raw files are real certificates (fingerprints are checked in TrustAnchorsTest).
        val factory = java.security.cert.CertificateFactory.getInstance("X.509")
        for (raw in listOf(R.raw.isrg_root_x1, R.raw.isrg_root_x2)) {
            context.resources.openRawResource(raw).use { assertNotNull(factory.generateCertificate(it)) }
        }
    }

    @Test
    fun `launcher icon is a bitmap before Android 8 and adaptive after`() {
        val icon = ContextCompat.getDrawable(context, R.mipmap.ic_launcher)!!
        if (Build.VERSION.SDK_INT < 26) {
            assertTrue(icon.javaClass.name, icon is BitmapDrawable)
        } else {
            assertTrue(icon.javaClass.name, icon is android.graphics.drawable.AdaptiveIconDrawable)
        }
        assertEquals(R.mipmap.ic_launcher, context.applicationInfo.icon)
    }

    @Test
    fun `no letter spacing, which breaks Arabic joining on old Android`() {
        val t = LearnerTypography
        listOf(
            t.displayLarge, t.displayMedium, t.displaySmall, t.headlineLarge, t.headlineMedium, t.headlineSmall,
            t.titleLarge, t.titleMedium, t.titleSmall, t.bodyLarge, t.bodyMedium, t.bodySmall,
            t.labelLarge, t.labelMedium, t.labelSmall,
        ).forEach { assertEquals(0f, it.letterSpacing.value, 0f) }
        assertEquals(0.sp.value, t.bodyLarge.letterSpacing.value, 0f)
    }

    @Test
    fun `playback service builds its session and posts the media notification`() {
        val controller = Robolectric.buildService(PlaybackService::class.java).create()
        val service = controller.get()
        val session = service.onGetSession(
            MediaSession.ControllerInfo.createTestOnlyControllerInfo("com.example", 0, 0, 0, 0, false, Bundle.EMPTY),
        )!!
        service.addSession(session)
        session.player.setMediaItem(
            MediaItem.Builder().setMediaId("c1/1").setUri("file:///none.mp3")
                .setMediaMetadata(MediaMetadata.Builder().setTitle("الهيمنة").build()).build(),
        )
        service.onUpdateNotification(session, /* startInForegroundRequired = */ true)
        shadowOf(Looper.getMainLooper()).idle()
        val manager = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notifications = shadowOf(manager).allNotifications
        assertTrue("media notification posted", notifications.isNotEmpty())
        if (Build.VERSION.SDK_INT >= 26) assertFalse(shadowOf(manager).notificationChannels.isEmpty())
        controller.destroy()
    }

    @Test
    fun `episode download lands in app-private storage and is scheduled by WorkManager`() {
        val server = MockWebServer().apply { start() }
        try {
            val bytes = ByteArray(150_000) { (it % 253).toByte() }
            server.enqueue(MockResponse().setBody(Buffer().write(bytes)))
            val files = EpisodeFiles(File(context.filesDir, "episodes-test"))
            val host = server.hostName
            val downloader = EpisodeDownloader(httpClient(AuthInterceptor({ host }, { "tkn" })), files)
            val file = runBlocking { downloader.download("c1", "01", server.url("/audio").toString()) }
            assertTrue(file.absolutePath.startsWith(context.filesDir.absolutePath))
            assertArrayEquals(bytes, file.readBytes())

            WorkManagerTestInitHelper.initializeTestWorkManager(
                context, Configuration.Builder().setExecutor(SynchronousExecutor()).build(),
            )
            val states = DownloadStates(files)
            DownloadController(context, states, files).enqueue("c1", 2, "02")
            assertEquals(DownloadState.Queued, states.stateOf("c1", "02"))
            val work = WorkManager.getInstance(context).getWorkInfosByTag(DownloadController.TAG).get().single()
            assertEquals(WorkInfo.State.ENQUEUED, work.state) // waits for the network constraint
        } finally {
            server.shutdown()
        }
    }
}

/** The whole app (activity, theme, edge-to-edge, Compose) starts on the oldest supported Android. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 25, 35])
class OldAndroidStartTest {
    @get:Rule
    val rule = createAndroidComposeRule<MainActivity>()

    @Test
    fun `first start shows the Arabic setup screen`() {
        val title = rule.onNodeWithText("الاتصال بالخادم").fetchSemanticsNode()
        val width = rule.onRoot().fetchSemanticsNode().boundsInRoot.width
        // Right to left whatever the system language: the short title hugs the right edge.
        assertTrue("title at ${title.boundsInRoot}", title.boundsInRoot.left > width / 3 && title.boundsInRoot.right > width * 0.85f)
        rule.onNodeWithText("اختبار الاتصال والحفظ").assertExists()
    }
}
