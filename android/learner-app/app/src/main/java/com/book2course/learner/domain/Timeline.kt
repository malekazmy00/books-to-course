package com.book2course.learner.domain

import com.book2course.learner.data.api.EpisodeDetail
import com.book2course.learner.data.api.Section
import com.book2course.learner.data.api.Segment

/** Pure navigation inside one episode (DAISY-style: by section, by segment). Seconds, not ms. */
object Timeline {
    /** Restart the current section if we are more than this far into it; else go to the previous one. */
    const val RESTART_THRESHOLD_S = 3.0

    fun timedSections(episode: EpisodeDetail): List<Section> = episode.sections.filter { it.startS != null }

    fun sectionAt(episode: EpisodeDetail, positionS: Double): Section? =
        timedSections(episode).lastOrNull { (it.startS ?: 0.0) <= positionS + 0.05 }

    fun segmentAt(episode: EpisodeDetail, positionS: Double): Segment? =
        episode.sections.asSequence().flatMap { it.segments.asSequence() }
            .filter { it.startS != null }
            .lastOrNull { (it.startS ?: 0.0) <= positionS + 0.05 }

    fun nextSectionStart(episode: EpisodeDetail, positionS: Double): Double? =
        timedSections(episode).firstOrNull { (it.startS ?: 0.0) > positionS + 0.5 }?.startS

    fun previousSectionStart(episode: EpisodeDetail, positionS: Double): Double {
        val sections = timedSections(episode)
        val current = sections.lastOrNull { (it.startS ?: 0.0) <= positionS + 0.05 } ?: return 0.0
        val start = current.startS ?: 0.0
        if (positionS - start > RESTART_THRESHOLD_S) return start
        val index = sections.indexOf(current)
        return if (index > 0) sections[index - 1].startS ?: 0.0 else 0.0
    }

    /**
     * Questions mode: the start of the first hidden answer that playback is about to enter
     * (between [fromS] exclusive and [toS] inclusive) and whose question is not revealed yet.
     */
    fun answerGateBetween(episode: EpisodeDetail, fromS: Double, toS: Double, revealed: Set<String>): Double? {
        val questionOf = episode.questions.filter { it.answerSegmentId != null }.associateBy { it.answerSegmentId }
        return episode.sections.asSequence().flatMap { it.segments.asSequence() }
            .filter { it.hidden && it.startS != null }
            .filter { seg -> questionOf[seg.segmentId]?.let { it.questionId !in revealed } ?: true }
            .mapNotNull { it.startS }
            .filter { it > fromS && it <= toS }
            .minOrNull()
    }

    fun segmentById(episode: EpisodeDetail, id: String): Segment? =
        episode.sections.asSequence().flatMap { it.segments.asSequence() }.firstOrNull { it.segmentId == id }

    fun sectionOfSegment(episode: EpisodeDetail, id: String): Section? =
        episode.sections.firstOrNull { s -> s.segments.any { it.segmentId == id } }
}

/** "12:05" / "1:02:03"; for screen readers see [spokenDuration]. */
fun clock(ms: Long): String {
    val total = (ms.coerceAtLeast(0) / 1000)
    val h = total / 3600
    val m = (total % 3600) / 60
    val s = total % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, s) else "%d:%02d".format(m, s)
}

/** Arabic duration words for TalkBack: "ساعة و5 دقائق", "12 دقيقة", "40 ثانية". */
fun spokenDuration(ms: Long): String {
    val totalS = (ms.coerceAtLeast(0) + 500) / 1000
    val h = totalS / 3600
    val m = (totalS % 3600) / 60
    if (h == 0L && m == 0L) return "$totalS ${arabicUnit(totalS, "ثانية", "ثانيتان", "ثوانٍ")}"
    val parts = mutableListOf<String>()
    if (h > 0) parts += if (h == 1L) "ساعة" else if (h == 2L) "ساعتان" else "$h ${arabicUnit(h, "ساعة", "ساعتان", "ساعات")}"
    if (m > 0) parts += if (m == 1L) "دقيقة" else if (m == 2L) "دقيقتان" else "$m ${arabicUnit(m, "دقيقة", "دقيقتان", "دقائق")}"
    return parts.joinToString(" و")
}

/** Arabic plural for counts: 3–10 take the plural, everything else the singular. */
private fun arabicUnit(n: Long, singular: String, dual: String, plural: String): String = when {
    n == 2L -> dual
    n % 100 in 3..10 -> plural
    else -> singular
}
