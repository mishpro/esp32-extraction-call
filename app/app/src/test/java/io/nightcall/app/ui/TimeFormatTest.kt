package io.nightcall.app.ui

import java.time.ZoneId
import java.time.ZoneOffset
import org.junit.Assert.assertEquals
import org.junit.Test

class TimeFormatTest {

    private val utc = ZoneOffset.UTC
    private val berlin = ZoneId.of("Europe/Berlin")

    @Test
    fun `epoch zero is midnight`() {
        assertEquals("00:00:00", TimeFormat.hhmmss(0L, utc))
    }

    @Test
    fun `known instant in UTC`() {
        // 2023-11-14T22:13:20Z
        assertEquals("22:13:20", TimeFormat.hhmmss(1_700_000_000L, utc))
    }

    @Test
    fun `same instant in Berlin is one hour ahead in winter`() {
        assertEquals("23:13:20", TimeFormat.hhmmss(1_700_000_000L, berlin))
    }

    @Test
    fun `single digit components are zero padded`() {
        // 2001-09-09T01:46:40Z
        assertEquals("01:46:40", TimeFormat.hhmmss(1_000_000_000L, utc))
    }

    @Test
    fun `end of day in UTC`() {
        // 2023-11-14T23:59:59Z
        assertEquals("23:59:59", TimeFormat.hhmmss(1_700_006_399L, utc))
    }

    @Test
    fun `summer time shift is applied`() {
        // 2023-07-01T12:00:00Z, Berlin is UTC+2 in summer
        assertEquals("14:00:00", TimeFormat.hhmmss(1_688_212_800L, berlin))
    }

    @Test
    fun `default zone is the device zone`() {
        assertEquals(
            TimeFormat.hhmmss(1_700_000_000L, ZoneId.systemDefault()),
            TimeFormat.hhmmss(1_700_000_000L),
        )
    }

    @Test
    fun `duration renders as hours minutes seconds`() {
        assertEquals("00:00:00", TimeFormat.duration(0L))
        assertEquals("00:00:59", TimeFormat.duration(59L))
        assertEquals("00:15:00", TimeFormat.duration(900L))
        assertEquals("02:59:58", TimeFormat.duration(10_798L))
        assertEquals("24:00:00", TimeFormat.duration(86_400L))
    }

    @Test
    fun `negative duration is clamped to zero`() {
        assertEquals("00:00:00", TimeFormat.duration(-5L))
    }
}
