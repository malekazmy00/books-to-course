package com.book2course.learner.domain

import java.util.Calendar
import java.util.Locale
import java.util.TimeZone

/*
 * Formatting for the «المراحل والتقدم» section. Plain Kotlin (no java.time: minSdk 24 has none
 * without desugaring), so it is unit-tested on the JVM.
 */

private val ISO = Regex("""(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?""")

/** Epoch milliseconds of an ISO-8601 timestamp as the server writes it (offset or Z; none = UTC). */
fun parseIsoMillis(value: String?): Long? {
    val m = ISO.matchEntire(value?.trim() ?: return null) ?: return null
    val (y, mo, d, h, mi, s) = m.destructured
    val calendar = Calendar.getInstance(TimeZone.getTimeZone("UTC"), Locale.ROOT).apply {
        clear()
        set(y.toInt(), mo.toInt() - 1, d.toInt(), h.toInt(), mi.toInt(), s.toInt())
    }
    val fraction = m.groupValues[7].drop(1).take(3).padEnd(3, '0').ifEmpty { "000" }.toInt()
    var millis = calendar.timeInMillis + fraction
    val zone = m.groupValues[8]
    if (zone.isNotEmpty() && zone != "Z") {
        val sign = if (zone[0] == '-') -1 else 1
        val digits = zone.drop(1).replace(":", "")
        val offset = (digits.take(2).toInt() * 60 + digits.drop(2).toInt()) * 60_000L
        millis -= sign * offset
    }
    return millis
}

/** "قبل 3 دقائق", "قبل ساعتين", "قبل 4 أيام"; null when unknown. */
fun agoText(nowMs: Long, thenMs: Long?): String? {
    if (thenMs == null) return null
    val s = ((nowMs - thenMs) / 1000).coerceAtLeast(0)
    return when {
        s < 60 -> "الآن"
        s < 3600 -> "قبل " + count(s / 60, "دقيقة", "دقيقتين", "دقائق")
        s < 86_400 -> "قبل " + count(s / 3600, "ساعة", "ساعتين", "ساعات")
        else -> "قبل " + count(s / 86_400, "يوم", "يومين", "أيام")
    }
}

/** Remaining time, short: "9 س 20 د", "45 د", "أقل من دقيقة", "2 يوم 3 س". */
fun etaText(seconds: Long?): String? {
    if (seconds == null || seconds < 0) return null
    if (seconds < 60) return "أقل من دقيقة"
    val days = seconds / 86_400
    val hours = (seconds % 86_400) / 3600
    val minutes = (seconds % 3600) / 60
    return when {
        days > 0 -> if (hours > 0) "$days يوم $hours س" else "$days يوم"
        hours > 0 -> if (minutes > 0) "$hours س $minutes د" else "$hours س"
        else -> "$minutes د"
    }
}

/** Spoken remaining time for TalkBack ("نحو 9 ساعات و20 دقيقة"). */
fun etaSpoken(seconds: Long?): String? {
    if (seconds == null || seconds < 0) return null
    if (seconds < 60) return "أقل من دقيقة"
    val days = seconds / 86_400
    val rest = seconds % 86_400
    val duration = spokenDuration(rest * 1000)
    return "نحو " + if (days > 0) count(days, "يوم", "يومين", "أيام") + (if (rest >= 60) " و$duration" else "") else duration
}

/**
 * Whole-number formatting with grouping ("4,560") that TalkBack reads as one number.
 * Locale.US, not Locale.ROOT: on Android 7.0 "%,d" with Locale.ROOT has grouping size 0 and throws
 * ArithmeticException (divide by zero) — found on the API 24 emulator, not reproducible on the JVM.
 */
fun grouped(n: Int): String = String.format(Locale.US, "%,d", n)

fun percentText(percent: Double?): String? = percent?.let { String.format(Locale.US, "%.0f٪", it) }

private fun count(n: Long, singular: String, dual: String, plural: String): String = when {
    n == 1L -> singular
    n == 2L -> dual
    n % 100 in 3..10 -> "$n $plural"
    else -> "$n $singular"
}
