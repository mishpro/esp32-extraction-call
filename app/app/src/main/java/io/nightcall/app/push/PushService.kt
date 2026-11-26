package io.nightcall.app.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import io.nightcall.app.BuildConfig
import io.nightcall.app.data.SecureStore
import io.nightcall.app.service.AckQueue
import io.nightcall.app.service.PressHandler
import io.nightcall.app.service.SignalService

/**
 * S6: presses arrive here when the app process is gone (protocol v1.1 §4.10).
 *
 * `press` is sent by the server as a data-only, high-priority message, so this
 * service is the one that decides what to show. The order matters:
 *  1. show the notification through [PressHandler] — same reducer as the WS path,
 *     so a duplicate over both channels stays a single notification;
 *  2. queue `ack(delivered)` for the button;
 *  3. wake [SignalService] so the queued ack actually leaves — a high-priority
 *     FCM message is one of the few cases where Android 12+ allows starting a
 *     foreground service from the background.
 */
class PushService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        SecureStore(this).fcmToken = token
        PushTokenBus.publish(token)
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val press = PushPayload.toPress(message.data) ?: return
        val now = System.currentTimeMillis() / 1000
        PressHandler.accept(this, press, now)
        AckQueue.offer(press.seq)
        wakeSignalService()
    }

    private fun wakeSignalService() {
        val store = SecureStore(this)
        val url = store.serverUrl ?: BuildConfig.DEFAULT_SERVER_URL
        val token = store.token ?: BuildConfig.DEFAULT_TOKEN
        if (url.isBlank() || token.isBlank()) return
        runCatching { SignalService.ensureStarted(this, url, token, store.appId) }
            .onFailure {
                // Usually the FGS background-start restriction: the notification is
                // already shown, only the ack waits for the next manual connect.
                Log.w(TAG, "cannot wake the service: ${it.javaClass.simpleName}")
            }
    }

    private companion object {
        const val TAG = "PushService"
    }
}
