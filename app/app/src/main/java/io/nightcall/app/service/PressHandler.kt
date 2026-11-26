package io.nightcall.app.service

import android.content.Context
import io.nightcall.app.net.Press
import io.nightcall.app.notify.Notifications

/**
 * The single place that turns a `press` into UI state plus a notification, no
 * matter where it came from: the WebSocket (S3) or an FCM data message (S6).
 *
 * [SignalReducer.applyPress] is the dedup point (`from`, `seq`, `t_epoch`), so a
 * press delivered over both channels still produces one entry and one
 * notification — which is exactly why the server may send both (§4.10).
 */
object PressHandler {

    /** Returns true when the press was new (not a duplicate). */
    fun accept(context: Context, press: Press, nowEpochSec: Long): Boolean {
        var added = false
        SignalBus.update { state ->
            val result = SignalReducer.applyPress(state, press, nowEpochSec)
            added = result.added
            result.state
        }
        if (added) {
            Notifications.showPress(
                context.applicationContext,
                PressItem(
                    seq = press.seq,
                    serverEpoch = press.tEpoch,
                    localEpoch = nowEpochSec,
                    from = press.from,
                ),
            )
        }
        return added
    }
}
