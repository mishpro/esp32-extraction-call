package io.nightcall.app.net

import kotlin.math.roundToLong
import kotlin.random.Random

/**
 * Reconnect backoff from drafts/Протокол.md §6:
 *
 * ```
 * delay = min(60, 2^attempt) ± 20 %     attempt = 0, 1, 2, ...
 * ```
 *
 * Implementation detail: the jitter is applied to `2^attempt` and the result is
 * capped at 60 s, so the delay never exceeds the documented maximum (app: 60 s).
 * `attempt` = number of consecutive *failed* connection attempts; the counter is
 * reset by the caller after `hello_ok`.
 *
 * Pure functions, no Android/coroutine dependencies — unit-tested in BackoffTest.
 */
object Backoff {

    /** Maximum delay, seconds: drafts/Протокол.md §6 "Приложение: максимальная задержка 60 с". */
    const val MAX_DELAY_MS = 60_000L

    /** ±20 % from §6. */
    const val JITTER_FRACTION = 0.2

    private const val BASE_DELAY_MS = 1_000L

    /** Attempts above this are clamped: `1000 << 30` is ~34 years, far beyond the cap. */
    private const val MAX_EXPONENT = 30

    /** `min(60 s, 2^attempt s)` — the nominal (un-jittered) delay. */
    fun baseDelayMs(attempt: Int): Long {
        val exponent = attempt.coerceIn(0, MAX_EXPONENT)
        val raw = BASE_DELAY_MS shl exponent
        return raw.coerceAtMost(MAX_DELAY_MS)
    }

    /**
     * Jittered delay for [attempt]: `min(60 s, 2^attempt s × jitter)`, jitter ∈ [0.8, 1.2].
     *
     * For attempt ≥ 6 the exponential already exceeds the cap, so the result lands
     * in 48–60 s (the "30–60 с" band from §6).
     */
    fun delayMs(attempt: Int, random: Random = Random.Default): Long {
        val exponent = attempt.coerceIn(0, MAX_EXPONENT)
        val jitter = 1.0 - JITTER_FRACTION + random.nextDouble() * (2 * JITTER_FRACTION)
        val jittered = (BASE_DELAY_MS shl exponent) * jitter
        return jittered.roundToLong().coerceIn(0L, MAX_DELAY_MS)
    }
}
