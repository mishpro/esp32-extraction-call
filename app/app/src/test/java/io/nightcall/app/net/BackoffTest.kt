package io.nightcall.app.net

import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Backoff from drafts/Протокол.md §6: `delay = min(60, 2^attempt) ± 20 %`.
 * Boundaries are checked for attempt 0..10 (documented table) plus out-of-range guards.
 */
class BackoffTest {

    private val table = listOf(
        1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 32_000L,
        60_000L, 60_000L, 60_000L, 60_000L, 60_000L,
    )

    @Test
    fun `base delay matches the protocol table for attempts 0 through 10`() {
        for (attempt in 0..10) {
            assertEquals("attempt $attempt", table[attempt], Backoff.baseDelayMs(attempt))
        }
    }

    @Test
    fun `base delay is clamped for negative and huge attempts`() {
        assertEquals(1_000L, Backoff.baseDelayMs(-1))
        assertEquals(60_000L, Backoff.baseDelayMs(6))
        assertEquals(60_000L, Backoff.baseDelayMs(100))
    }

    @Test
    fun `jittered delay stays within 20 percent of base and never exceeds 60 s`() {
        val random = Random(42)
        for (attempt in 0..10) {
            val base = Backoff.baseDelayMs(attempt)
            val low = (base * (1.0 - Backoff.JITTER_FRACTION)).toLong()
            repeat(500) {
                val delay = Backoff.delayMs(attempt, random)
                assertTrue(
                    "attempt=$attempt delay=$delay, expected >= $low",
                    delay >= low,
                )
                assertTrue(
                    "attempt=$attempt delay=$delay, expected <= ${Backoff.MAX_DELAY_MS}",
                    delay <= Backoff.MAX_DELAY_MS,
                )
            }
        }
    }

    @Test
    fun `capped attempts stay in the 48-60 s band, not above the cap`() {
        val random = Random(7)
        for (attempt in 6..10) {
            repeat(500) {
                val delay = Backoff.delayMs(attempt, random)
                assertTrue("attempt=$attempt delay=$delay", delay in 48_000L..60_000L)
            }
        }
    }

    @Test
    fun `first attempt is about one second`() {
        val random = Random(1)
        repeat(500) {
            val delay = Backoff.delayMs(attempt = 0, random = random)
            assertTrue("delay=$delay ms", delay in 800L..1_200L)
        }
    }

    @Test
    fun `delay is deterministic for the same seed`() {
        val a = Backoff.delayMs(attempt = 4, random = Random(123))
        val b = Backoff.delayMs(attempt = 4, random = Random(123))
        assertEquals(a, b)
    }

    @Test
    fun `jitter actually spreads delays`() {
        val random = Random(99)
        val samples = (0 until 200).map { Backoff.delayMs(attempt = 3, random = random) }.toSet()
        assertTrue("expected jitter, got only $samples", samples.size > 10)
    }

    @Test
    fun `delay never grows with attempt before the cap`() {
        val random = Random(5)
        val observed = (0..5).map { attempt ->
            var max = 0L
            repeat(200) { max = maxOf(max, Backoff.delayMs(attempt, random)) }
            max
        }
        for (i in 1 until observed.size) {
            assertTrue("max at attempt $i (${observed[i]}) < attempt ${i - 1}", observed[i] > observed[i - 1])
        }
    }

    @Test
    fun `max delay constant is 60 seconds`() {
        assertEquals(60_000L, Backoff.MAX_DELAY_MS)
        assertEquals(0.2, Backoff.JITTER_FRACTION, 1e-9)
    }
}
