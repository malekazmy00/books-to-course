package com.book2course.learner.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.semantics.getOrNull
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasScrollToNodeAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.unit.LayoutDirection
import com.book2course.learner.OperatorFixtures
import com.book2course.learner.data.Loadable
import com.book2course.learner.data.api.AppError
import com.book2course.learner.data.local.ThemeMode
import com.book2course.learner.ui.operator.BookStagesScreen
import com.book2course.learner.ui.operator.OperatorLibraryScreen
import com.book2course.learner.ui.operator.OperatorUi
import com.book2course.learner.ui.operator.StageDetailScreen
import com.book2course.learner.ui.theme.LearnerTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/** The «المراحل والتقدم» screens under Robolectric: what TalkBack reads, navigation, and no blocking spinner. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [24, 35]) // Android 7.0 (minSdk) and current
class OperatorScreensTest {
    @get:Rule
    val rule = createComposeRule()
    private val now = OperatorFixtures.NOW_MS

    private fun setRtl(content: @Composable () -> Unit) = rule.setContent {
        LearnerTheme(ThemeMode.LIGHT) {
            CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl, content = content)
        }
    }

    private fun descriptionContaining(vararg parts: String) = SemanticsMatcher("description has ${parts.toList()}") { node ->
        val text = node.config.getOrNull(SemanticsProperties.ContentDescription)?.joinToString(" ").orEmpty()
        parts.all { it in text }
    }

    @Test
    fun `a book card reads as one sentence and opens the stages`() {
        var opened = ""
        setRtl {
            OperatorLibraryScreen(OperatorUi(Loadable(OperatorFixtures.books)), onOpenBook = { opened = it.bookId },
                onRefresh = {}, onSettings = {}, nowMs = now)
        }
        val card = rule.onNode(descriptionContaining("Structured Analytic Techniques", "المرحلة الحالية: الترجمة", "جارية",
            "اكتمل 2 من 9", "29٪", "تاريخ الرفع غير مسجّل"))
        card.assertIsDisplayed()
        card.performSemanticsAction(SemanticsActions.OnClick)
        assertEquals("book-aaea5c4b55e6a7519bdea580", opened)
    }

    @Test
    fun `cached data stays visible during a background refresh, with no spinner over it`() {
        setRtl {
            OperatorLibraryScreen(OperatorUi(Loadable(OperatorFixtures.books, refreshing = true), userRefreshing = false),
                onOpenBook = {}, onRefresh = {}, onSettings = {}, nowMs = now)
        }
        rule.onNode(descriptionContaining("Structured Analytic Techniques")).assertIsDisplayed()
        rule.onAllNodes(hasContentDescription("جارٍ التحديث")).fetchSemanticsNodes().let { assertTrue(it.isEmpty()) }
    }

    @Test
    fun `a server without the operator API says so instead of failing`() {
        setRtl {
            OperatorLibraryScreen(OperatorUi(Loadable(error = AppError.NotFound)), onOpenBook = {}, onRefresh = {},
                onSettings = {}, nowMs = now)
        }
        rule.onNodeWithText("/v3/operator", substring = true).assertIsDisplayed()
    }

    @Test
    fun `each stage is one TalkBack node with numbers, remaining time, gate and workflow`() {
        var opened = ""
        setRtl {
            BookStagesScreen(OperatorUi(Loadable(OperatorFixtures.stages)), titleFallback = "x", onBack = {},
                onOpenStage = { opened = it.stageId }, onRefresh = {}, nowMs = now)
        }
        val translation = descriptionContaining("4. الترجمة: جارية", "2,763 من 4,560", "61٪", "مقبول 2,457", "حاشية 204",
            "مُسقط 98", "مرفوض 4", "متبقٍ 1,797", "المتبقي: نحو 14 ساعة و58 دقيقة", "البوابة: لا توجد نتيجة مسجّلة",
            "Temporal: جارٍ", "translate_unit_batch", "قبل 3 دقائق")
        rule.onNode(hasScrollToNodeAction()).performScrollToNode(translation)
        rule.onNode(translation).performSemanticsAction(SemanticsActions.OnClick)
        assertEquals("translation", opened)
        rule.onNode(hasScrollToNodeAction()).performScrollToNode(descriptionContaining("9. MP3: بانتظار مرحلة سابقة"))
    }

    @Test
    fun `stage detail shows runs, translated units, notes and rejection reasons`() {
        setRtl {
            StageDetailScreen(OperatorUi(Loadable(OperatorFixtures.detail)), titleFallback = "x", onBack = {}, onRefresh = {}, nowMs = now)
        }
        val list = rule.onNode(hasScrollToNodeAction())
        list.performScrollToNode(descriptionContaining("الوحدة 2851", "مقبول", "When analysts are too cautious", "عندما يكون المحللون"))
        list.performScrollToNode(descriptionContaining("الوحدة 3", "حاشية", "RANDOLPH H. PHERSON", "ملاحظات الفحص: إضافة"))
        list.performScrollToNode(androidx.compose.ui.test.hasText("إنجليزية غير مترجمة · حرِج"))
        list.performScrollToNode(androidx.compose.ui.test.hasText("lang.latin — English prose left in place"))
        // 1 note listed of the 208 the stage counts (note 204 + rejected 4)
        list.performScrollToNode(androidx.compose.ui.test.hasText("حواشي المترجم (أول 1 من 208)"))
    }

    @Test
    fun `the bottom tabs name both sections and mark the current one`() {
        var stages = 0
        setRtl { SectionTabs(learnSelected = true, onLearn = {}, onStages = { stages++ }) }
        rule.onNodeWithText("تعلّم").assertIsSelected()
        rule.onNodeWithText("المراحل").performClick()
        assertEquals(1, stages)
    }
}
