package io.nightcall.app.push

import io.nightcall.app.net.Press
import io.nightcall.app.net.Protocol

/**
 * FCM data messages carry every value as a string (protocol v1.1 §4.10), so the
 * press from a push arrives in the same shape as the WS frame but typed as text.
 *
 * A malformed message is dropped instead of crashing the messaging service: the
 * WS copy of the same press is still on its way when the app can reach the server.
 */
object PushPayload {

    fun toPress(data: Map<String, String>): Press? {
        if (data["t"] != "press") return null
        val v = data["v"]?.toIntOrNull() ?: return null
        val seq = data["seq"]?.toLongOrNull() ?: return null
        val tEpoch = data["t_epoch"]?.toLongOrNull() ?: return null
        // FCM reserves the key `from`, so the server sends `device_id` (§4.10).
        val from = data["device_id"]?.takeIf(Protocol::isHexId)
        return runCatching { Press(v = v, seq = seq, tEpoch = tEpoch, from = from) }.getOrNull()
    }
}
