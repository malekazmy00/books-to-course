package com.book2course.learner.domain

import com.book2course.learner.OperatorFixtures
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class OperatorFormatTest {
    @Test
    fun `server timestamps parse with fractions and offsets`() {
        assertEquals(OperatorFixtures.NOW_MS, parseIsoMillis("2026-10-26T12:00:00+00:00"))
        assertEquals(OperatorFixtures.NOW_MS, parseIsoMillis("2026-10-26T12:00:00Z"))
        assertEquals(OperatorFixtures.NOW_MS + 123, parseIsoMillis("2026-10-26T12:00:00.123456+00:00"))
        assertEquals(OperatorFixtures.NOW_MS, parseIsoMillis("2026-10-26T15:00:00+03:00"))
        assertNull(parseIsoMillis(null))
        assertNull(parseIsoMillis("yesterday"))
    }

    @Test
    fun `relative times read naturally in Arabic`() {
        val now = OperatorFixtures.NOW_MS
        assertEquals("الآن", agoText(now, now - 20_000))
        assertEquals("قبل دقيقة", agoText(now, now - 60_000))
        assertEquals("قبل دقيقتين", agoText(now, now - 120_000))
        assertEquals("قبل 5 دقائق", agoText(now, now - 300_000))
        assertEquals("قبل 3 ساعات", agoText(now, now - 3 * 3_600_000))
        assertEquals("قبل 11 يوم", agoText(now, now - 11 * 86_400_000L))
        assertNull(agoText(now, null))
    }

    @Test
    fun `remaining time is short on screen and spoken for TalkBack`() {
        assertEquals("14 س 58 د", etaText(53_910))
        assertEquals("45 د", etaText(2_700))
        assertEquals("أقل من دقيقة", etaText(30))
        assertEquals("2 يوم 3 س", etaText(2 * 86_400 + 3 * 3600))
        assertNull(etaText(null))
        assertEquals("نحو 14 ساعة و58 دقيقة", etaSpoken(53_910))
        assertEquals("نحو يومين و3 ساعات", etaSpoken(2 * 86_400 + 3 * 3600))
    }

    @Test
    fun `numbers and percents`() {
        assertEquals("4,560", grouped(4560))
        assertEquals("61٪", percentText(60.6))
        assertNull(percentText(null))
    }
}
