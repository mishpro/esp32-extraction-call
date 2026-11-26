package io.guidecaller.app.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import io.guidecaller.app.net.ErrorCode
import io.guidecaller.app.net.ErrorMessage
import io.guidecaller.app.net.HelloOk
import io.guidecaller.app.net.Message
import io.guidecaller.app.net.Press
import io.guidecaller.app.net.Protocol
import io.guidecaller.app.net.StatusMessage
import io.guidecaller.app.net.WsClient
import io.guidecaller.app.notify.Notifications
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch

/**
 * Foreground service that owns the WebSocket (S3.5–3.6).
 *
 * Responsibilities:
 *  - stay alive while the activity is gone, with a low-importance (channel `status`)
 *    ongoing notification and `specialUse` FGS type;
 *  - run [WsClient] (which does `hello`, `pong`, reconnect §6) in [scope];
 *  - translate incoming messages into [SignalBus] through [SignalReducer];
 *  - show a `signals`-channel notification for every new `press` and answer
 *    `ack(seq, stage=delivered)` (§4.6) — the ack is sent even for a duplicate
 *    `seq`, because a duplicate means our earlier ack was lost;
 *  - reset [SignalBus] on stop, so the UI never shows a dead session.
 *
 * It does not talk to the activity directly: [SignalBus] is the only channel.
 * Connection parameters come from the start intent built by [start].
 */
class SignalService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var client: WsClient? = null
    private var sessionJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        // Channels must exist before the first notification (and before startForeground).
        Notifications.ensureChannels(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val url = intent?.getStringExtra(EXTRA_URL)
        val token = intent?.getStringExtra(EXTRA_TOKEN)
        val appId = intent?.getStringExtra(EXTRA_APP_ID)
        if (url.isNullOrBlank() || token.isNullOrBlank() || appId.isNullOrBlank()) {
            Log.w(TAG, "start without url/token/app_id; stopping")
            stopSelf(startId)
            return START_NOT_STICKY
        }

        // Must happen within a few seconds of startForegroundService (§9, ANR otherwise).
        startAsForeground()
        startSession(url, token, appId)
        // A system restart re-delivers the last intent, so the socket comes back by itself.
        return START_REDELIVER_INTENT
    }

    override fun onDestroy() {
        sessionJob?.cancel()
        sessionJob = null
        client?.disconnect()
        client = null
        scope.cancel()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        SignalBus.reset()
        super.onDestroy()
    }

    private fun startAsForeground() {
        // API 34+ requires an explicit type; `specialUse` is declared in the manifest
        // with the PROPERTY_SPECIAL_USE_FGS_SUBTYPE explanation.
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        } else {
            0
        }
        ServiceCompat.startForeground(
            this,
            Notifications.SERVICE_NOTIFICATION_ID,
            Notifications.serviceNotification(this),
            type,
        )
    }

    private fun startSession(url: String, token: String, appId: String) {
        // A second start (redelivery or a new URL) replaces the previous session.
        sessionJob?.cancel()
        client?.disconnect()

        val ws = WsClient(scope)
        client = ws
        sessionJob = scope.launch {
            coroutineScope {
                // UNDISPATCHED: both collectors subscribe before connect() can emit.
                launch(start = CoroutineStart.UNDISPATCHED) {
                    ws.state.collect { connection ->
                        SignalBus.update { state -> state.copy(connection = connection) }
                    }
                }
                launch(start = CoroutineStart.UNDISPATCHED) {
                    ws.messages.collect { message -> handleMessage(ws, message) }
                }
                ws.connect(url, token, appId)
            }
        }
    }

    /**
     * Messages already handled by [WsClient] itself (`ping`→`pong`, close, hello
     * timeout) may still arrive here; only protocol state belongs to the service.
     */
    private suspend fun handleMessage(ws: WsClient, message: Message) {
        val now = nowEpochSec()
        when (message) {
            is HelloOk -> SignalBus.update { SignalReducer.applyHelloOk(it, message, now) }
            is StatusMessage -> SignalBus.update { SignalReducer.applyStatus(it, message, now) }
            is Press -> handlePress(ws, message, now)
            is ErrorMessage -> handleError(message)
            else -> Unit
        }
    }

    private fun handlePress(ws: WsClient, press: Press, nowEpochSec: Long) {
        var added = false
        SignalBus.update { state ->
            val result = SignalReducer.applyPress(state, press, nowEpochSec)
            added = result.added
            result.state
        }
        if (added) {
            val item = PressItem(
                seq = press.seq,
                serverEpoch = press.tEpoch,
                localEpoch = nowEpochSec,
                from = press.from,
            )
            Notifications.showPress(this, item)
        }
        // §4.6: the button needs ack(delivered) to leave SENT for WAIT ETA. Sent for
        // duplicates too, which is exactly how a lost ack gets retried.
        ws.send(Protocol.ackDelivered(press.seq))
    }

    private fun handleError(message: ErrorMessage) {
        if (message.code == ErrorCode.AUTH_FAILED) {
            // Never log the token; the code and the server text are enough.
            Log.w(TAG, "auth_failed: ${message.msg}")
        }
        SignalBus.update { SignalReducer.applyError(it, message) }
    }

    private fun nowEpochSec(): Long = System.currentTimeMillis() / 1000

    companion object {
        private const val TAG = "SignalService"

        const val EXTRA_URL = "url"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_APP_ID = "app_id"

        fun start(context: Context, url: String, token: String, appId: String) {
            val intent = Intent(context, SignalService::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_TOKEN, token)
                .putExtra(EXTRA_APP_ID, appId)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SignalService::class.java))
        }
    }
}
