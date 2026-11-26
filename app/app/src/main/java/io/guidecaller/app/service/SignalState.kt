package io.guidecaller.app.service

import io.guidecaller.app.net.ConnectionState

/** One delivered press, kept for the UI list. */
data class PressItem(
    val seq: Long,
    val serverEpoch: Long?,
    val localEpoch: Long,
    val from: String?,
)

/**
 * Everything the UI needs. Owned by [SignalBus], written by [SignalService],
 * read by the activity. `serverOffsetSec` = server epoch - device epoch, taken
 * from `hello_ok`/`status`; it is used to show server time next to local time.
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
) {
    val isConnected: Boolean get() = connection is ConnectionState.Connected
}
