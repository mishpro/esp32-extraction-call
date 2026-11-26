package io.nightcall.app.ui

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Time rendering for the press list (S3.7).
 *
 * Server and device timestamps arrive as epoch seconds; the list shows both as
 * wall-clock `HH:mm:ss` in the device time zone. `minSdk` 26 means `java.time`
 * is available without desugaring, and the function stays pure for unit tests.
 */
object TimeFormat {

    private val HH_MM_SS = DateTimeFormatter.ofPattern("HH:mm:ss")

    /** [epochSeconds] formatted as `HH:mm:ss` in [zone] (device zone by default). */
    fun hhmmss(epochSeconds: Long, zone: ZoneId = ZoneId.systemDefault()): String =
        HH_MM_SS.format(Instant.ofEpochSecond(epochSeconds).atZone(zone))

    /** S4 countdown: a duration as `HH:mm:ss` (`24:00:00` max, never negative). */
    fun duration(seconds: Long): String {
        val total = seconds.coerceAtLeast(0)
        return "%02d:%02d:%02d".format(total / 3600, (total % 3600) / 60, total % 60)
    }
}
