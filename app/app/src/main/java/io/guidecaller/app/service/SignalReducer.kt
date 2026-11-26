package io.guidecaller.app.service

import io.guidecaller.app.net.ErrorCode
import io.guidecaller.app.net.ErrorMessage
import io.guidecaller.app.net.HelloOk
import io.guidecaller.app.net.Press
import io.guidecaller.app.net.StatusMessage
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/**
 * Pure state transitions for [SignalState] (no Android, no I/O) — the part of
 * [SignalService] that can be unit-tested on a plain JVM.
 *
 * Epoch arguments are **seconds** (`t_epoch` on the wire, `System.currentTimeMillis() / 1000`),
 * because the protocol carries whole seconds. [SignalState.serverOffsetSec] is the difference
 * `server - device` taken at the moment a message arrived.
 */
object SignalReducer {

    /** The UI list only needs recent presses; keep a bounded window (§9: "последних нажатий"). */
    const val MAX_PRESSES = 100

    /** Result of [applyPress]; [added] is false when `seq` was already seen. */
    data class PressResult(val state: SignalState, val added: Boolean)

    private val TIME_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")

    /** §4.2: `hello_ok` proves the token, gives the server clock and the peer snapshot. */
    fun applyHelloOk(state: SignalState, message: HelloOk, nowEpochSec: Long): SignalState =
        state.copy(
            serverOffsetSec = message.tEpoch - nowEpochSec,
            deviceOnline = message.peer.online,
            deviceState = message.peer.state.name,
            appOnline = true,
            // A successful hello_ok means the token was accepted; a previous
            // auth_failed belongs to an older session.
            authFailed = false,
            lastError = if (message.peer.online) null else state.lastError,
        )

    /** §4.7: `status` refreshes the clock offset and both liveness flags. */
    fun applyStatus(state: SignalState, message: StatusMessage, nowEpochSec: Long): SignalState =
        state.copy(
            serverOffsetSec = message.tEpoch - nowEpochSec,
            deviceOnline = message.device.online,
            deviceState = message.device.state.name,
            appOnline = message.app.online,
            // `no_peer` is only relevant while the button is away; drop it once
            // the button is back so the UI banner disappears.
            lastError = if (message.device.online) null else state.lastError,
        )

    /**
     * §4.3/§4.6: a press is appended to the front of [SignalState.presses].
     *
     * Duplicates are dropped by `seq` (the server may re-forward a press when an
     * `ack(delivered)` was lost); the oldest entry falls off past [MAX_PRESSES].
     */
    fun applyPress(state: SignalState, message: Press, nowEpochSec: Long): PressResult {
        // `seq` is per-device and resets to 1 when the button reboots, so the
        // duplicate key must include the sender and the server timestamp.
        val duplicate = state.presses.any {
            it.seq == message.seq &&
                it.from == message.from &&
                it.serverEpoch == message.tEpoch
        }
        if (duplicate) return PressResult(state, added = false)
        val item = PressItem(
            seq = message.seq,
            serverEpoch = message.tEpoch,
            localEpoch = nowEpochSec,
            from = message.from,
        )
        val presses = (listOf(item) + state.presses).take(MAX_PRESSES)
        return PressResult(state.copy(presses = presses), added = true)
    }

    /** §4.9: map the two errors that change what the UI shows. */
    fun applyError(state: SignalState, message: ErrorMessage): SignalState = when (message.code) {
        ErrorCode.AUTH_FAILED -> state.copy(authFailed = true)
        ErrorCode.NO_PEER -> state.copy(lastError = CODE_NO_PEER)
        else -> state
    }

    /** `HH:mm:ss` in the device time zone. */
    fun formatTime(epochSec: Long): String = formatTime(epochSec, ZoneId.systemDefault())

    /** `HH:mm:ss` in [zone]; the zone is a parameter so tests do not depend on the machine. */
    fun formatTime(epochSec: Long, zone: ZoneId): String =
        Instant.ofEpochSecond(epochSec).atZone(zone).format(TIME_FORMAT)

    /** Wire name of `error.code=no_peer`; the UI renders it as `NO BUTTON` (§4.9). */
    const val CODE_NO_PEER = "no_peer"
}
