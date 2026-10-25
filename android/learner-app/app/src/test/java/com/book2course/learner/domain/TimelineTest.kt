package com.book2course.learner.domain

import com.book2course.learner.Fixtures
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TimelineTest {
    private val e = Fixtures.episode

    @Test
    fun `current section and segment follow the position`() {
        assertEquals("intro", Timeline.sectionAt(e, 2.0)!!.sectionId)
        assertEquals("book", Timeline.sectionAt(e, 45.0)!!.sectionId)
        assertEquals("b2", Timeline.segmentAt(e, 45.0)!!.segmentId)
        assertEquals("external", Timeline.segmentAt(e, 45.0)!!.voice)
    }

    @Test
    fun `next and previous section, DAISY style`() {
        assertEquals(10.0, Timeline.nextSectionStart(e, 2.0)!!, 0.0)
        assertEquals(60.0, Timeline.nextSectionStart(e, 45.0)!!, 0.0)
        assertNull(Timeline.nextSectionStart(e, 95.0))
        assertEquals(10.0, Timeline.previousSectionStart(e, 45.0), 0.0) // restart current section
        assertEquals(0.0, Timeline.previousSectionStart(e, 11.0), 0.0) // just started: previous section
    }

    @Test
    fun `answer gate fires only when playing into an unrevealed answer`() {
        assertEquals(80.0, Timeline.answerGateBetween(e, 79.0, 80.25, emptySet())!!, 0.0)
        assertNull(Timeline.answerGateBetween(e, 79.0, 80.25, setOf("q1")))
        assertNull(Timeline.answerGateBetween(e, 80.0, 81.0, emptySet())) // already at the gate: continue
        assertNull(Timeline.answerGateBetween(e, 10.0, 11.0, emptySet()))
    }

    @Test
    fun `durations for eyes and ears`() {
        assertEquals("1:05", clock(65_000))
        assertEquals("1:01:05", clock(3_665_000))
        assertEquals("12 دقيقة", spokenDuration(12 * 60_000L))
        assertEquals("5 دقائق", spokenDuration(5 * 60_000L))
        assertEquals("ساعة و5 دقائق", spokenDuration(65 * 60_000L))
        assertEquals("40 ثانية", spokenDuration(40_000))
    }
}
