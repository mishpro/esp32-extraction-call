package io.nightcall.app.service

import io.nightcall.app.net.ConnectionState

/** One delivered press, kept for the UI list. */
data class PressItem(
    val seq: Long,
    val serverEpoch: Long?,
    val localEpoch: Long,
    val from: String?,
)

/** Which command is in flight (`timer` sets a countdown, `cancel` stops it). */
enum class CommandKind { TIMER, CANCEL }

/** Delivery state of the last command (§4.6: the UI must not say "sent" early). */
enum class CommandPhase {
    /** Nothing in flight; the countdown (if any) is driven by `status`. */
    IDLE,

    /** Sent, waiting for `ack(stage=delivered)` within [io.nightcall.app.net.Protocol.ACK_TIMEOUT_MS]. */
    PENDING,

    /** No ack in time or the button rejected it; [CommandUi.error] explains. */
    FAILED,
}

data class CommandUi(
    val phase: CommandPhase = CommandPhase.IDLE,
    val kind: CommandKind? = null,
    val seq: Long? = null,
    val error: String? = null,
)

/**
 * Everything the UI needs. Owned by [SignalBus], written by [SignalService],
 * read by the activity. `serverOffsetSec` = server epoch - device epoch, taken
 * from `hello_ok`/`status`; it is used to show server time next to local time.
 *
 * `countdownEndsAtLocalEpochSec` is the device clock converted to local seconds
 * (`now + remaining_s` at the moment a `status` arrived), so the UI can tick a
 * smooth countdown without waiting for the next status frame.
 */
data class SignalState(
    val connection: ConnectionState = ConnectionState.Idle,
    val deviceOnline: Boolean = false,
    val deviceState: String = "OFFLINE",
    val appOnline: Boolean = false,
    val serverOffsetSec: Long? = null,
    val presses: List<PressItem> = emptyList(),
    val authFailed: Boolean = false,
    val lastError: String? = null,
    val command: CommandUi = CommandUi(),
    val countdownEndsAtLocalEpochSec: Long? = null,
) {
    val isConnected: Boolean get() = connection is ConnectionState.Connected

    /** S4: a countdown is running (or has just finished) on the button. */
    val countdownActive: Boolean
        get() = deviceOnline && (deviceState == "COUNTDOWN" || deviceState == "ARRIVED")
}
