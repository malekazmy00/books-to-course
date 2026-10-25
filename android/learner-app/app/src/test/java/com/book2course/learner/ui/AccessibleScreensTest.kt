package com.book2course.learner.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.unit.LayoutDirection
import com.book2course.learner.Fixtures
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.api.CourseList
import com.book2course.learner.data.download.DownloadState
import com.book2course.learner.data.local.EpisodeProgress
import com.book2course.learner.data.local.LearnerState
import com.book2course.learner.data.local.RevealedAnswer
import com.book2course.learner.data.local.ThemeMode
import com.book2course.learner.ui.course.EpisodeRow
import com.book2course.learner.ui.course.EpisodeRowActions
import com.book2course.learner.ui.library.LibraryScreen
import com.book2course.learner.ui.player.PlayerActions
import com.book2course.learner.ui.player.PlayerUi
import com.book2course.learner.ui.player.QuestionCard
import com.book2course.learner.ui.player.TranscriptTab
import com.book2course.learner.ui.theme.LearnerTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** Compose UI under Robolectric: what TalkBack reads and what is (not) revealed. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 25, 35]) // Android 7.0 / 7.1 (minSdk) and current
class AccessibleScreensTest {
    @get:Rule
    val rule = createComposeRule()

    private fun setRtl(content: @Composable () -> Unit) = rule.setContent {
        LearnerTheme(ThemeMode.LIGHT) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl, content = content)
        }
    }

    private fun noActions(open: () -> Unit = {}, download: () -> Unit = {}) = EpisodeRowActions(
        open = { open() }, download = { download() }, cancelDownload = {}, deleteDownload = {}, setCompleted = { _, _ -> },
    )

    @Test
    fun `episode row reads as one sentence with progress, duration and download state`() {
        var opened = false
        var downloaded = false
        val episode = Fixtures.course.episodes[1]
        setRtl {
            EpisodeRow("c1", episode, EpisodeProgress(40_000, 100_000), DownloadState.None,
                noActions(open = { opened = true }, download = { downloaded = true }))
        }
        val node = rule.onNode(hasContentDescription("الحلقة 01: الهيمنة", substring = true))
        node.assertExists()
        val description = node.fetchSemanticsNode().config[androidx.compose.ui.semantics.SemanticsProperties.ContentDescription].single()
        assertTrue(description, description.contains("استمعت إلى 40٪"))
        assertTrue(description, description.contains("دقيقة"))
        val actions = node.fetchSemanticsNode().config[SemanticsActions.CustomActions]
        assertEquals(listOf("تنزيل الحلقة للاستماع دون اتصال", "وضع علامة: مكتملة"), actions.map { it.label })
        actions.first().action()
        assertTrue(downloaded)
        node.performClick()
        assertTrue(opened)
    }

    @Test
    fun `pending audio is announced instead of offering a download`() {
        setRtl { EpisodeRow("c1", Fixtures.course.episodes[0], null, DownloadState.None, noActions()) }
        val node = rule.onNode(hasContentDescription("الصوت لم يُنشر بعد", substring = true))
        node.assertExists()
        assertEquals(listOf("وضع علامة: مكتملة"), node.fetchSemanticsNode().config[SemanticsActions.CustomActions].map { it.label })
    }

    @Test
    fun `question hides the answer until the learner asks`() {
        var asked = 0
        val question = Fixtures.episode.questions.single()
        val ui = PlayerUi(episode = Loadable(Fixtures.episode))
        setRtl { QuestionCard(1, question, ui, PlayerActions(reveal = { _, _ -> asked++ })) }
        rule.onNodeWithText("ما الهيمنة؟").assertIsDisplayed()
        assertEquals(0, rule.onAllNodes(androidx.compose.ui.test.hasText("الإجابة:", substring = true)).fetchSemanticsNodes().size)
        rule.onNodeWithText("أظهر الإجابة").performClick()
        assertEquals(1, asked)
    }

    @Test
    fun `revealed answer is shown with an option to hear it`() {
        val question = Fixtures.episode.questions.single()
        val ui = PlayerUi(episode = Loadable(Fixtures.episode), revealed = mapOf("q1" to RevealedAnswer("قيادة بالرضا")))
        setRtl { QuestionCard(1, question, ui, PlayerActions()) }
        rule.onNodeWithText("الإجابة: قيادة بالرضا").assertIsDisplayed()
    }

    @Test
    fun `transcript labels the three voices and blanks hidden answers`() {
        val ui = PlayerUi(episode = Loadable(Fixtures.episode))
        setRtl { TranscriptTab(ui, PlayerActions()) }
        rule.onNodeWithText("صوت المؤلف").assertExists()
        rule.onNodeWithText("مصدر خارجي: Britannica").assertExists()
        rule.onNodeWithText("نص الكتاب").assertExists()
        rule.onNodeWithText("نص b1").assertExists()
    }

    @Test
    fun `hidden answer in the transcript offers reveal, not the answer`() {
        val ui = PlayerUi(episode = Loadable(Fixtures.episode))
        setRtl { TranscriptTab(ui, PlayerActions()) }
        rule.onNode(androidx.compose.ui.test.hasScrollAction()).performSemanticsAction(SemanticsActions.ScrollToIndex) { it(9) }
        rule.onNodeWithText("ما الهيمنة؟").assertExists()
        rule.onNodeWithText("الإجابة مخفية حتى تجيب أو تطلب إظهارها.").assertExists()
        rule.onNodeWithText("أظهر الإجابة").assertExists()
    }

    @Test
    fun `empty library says so`() {
        setRtl {
            LibraryScreen(Loadable(CourseList(emptyList())), LearnerState(), null, {}, {}, {}, {})
        }
        rule.onNodeWithText("لا توجد كورسات منشورة بعد.").assertIsDisplayed()
    }
}
