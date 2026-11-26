package io.nightcall.app.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.google.firebase.messaging.FirebaseMessaging
import io.nightcall.app.data.SecureStore
import io.nightcall.app.net.Ack
import io.nightcall.app.net.AckStage
import io.nightcall.app.net.ErrorCode
import io.nightcall.app.net.ErrorMessage
import io.nightcall.app.net.HelloOk
import io.nightcall.app.net.Message
import io.nightcall.app.net.Press
import io.nightcall.app.net.Protocol
import io.nightcall.app.net.StatusMessage
import io.nightcall.app.net.WsClient
import io.nightcall.app.notify.Notifications
import io.nightcall.app.push.PushTokenBus
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
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

    @Volatile
    private var client: WsClient? = null
    private var sessionJob: Job? = null

    /** Next `timer`/`cancel` seq; starts high to never collide with press seqs. */
    private val commandSeq = AtomicLong(COMMAND_SEQ_BASE)

    /** Seq of the command waiting for `ack(delivered)`; 0 means none. */
    private val pendingAck = AtomicLong(NO_PENDING_ACK)
    private var ackTimeoutJob: Job? = null

    /** Token already sent on this connection; reset by [startSession]. */
    @Volatile
    private var sentPushToken: String? = null

    /** Params of the running session; a duplicate start must not reconnect. */
    @Volatile
    private var sessionKey: String? = null

    override fun onCreate() {
        super.onCreate()
        alive = true
        // Channels must exist before the first notification (and before startForeground).
        Notifications.ensureChannels(this)
        scope.launch {
            CommandBus.commands.collect { command ->
                when (command) {
                    is CommandBus.Command.SetTimer -> sendTimer(command.durationS)
                    CommandBus.Command.Cancel -> sendCancel()
                }
            }
        }
        // S6: an ack queued by a push goes out as soon as a socket exists.
        scope.launch {
            AckQueue.wake.collect {
                client?.let { flushAcks(it) }
            }
        }
        scope.launch {
            PushTokenBus.tokens.collect { token ->
                client?.let { sendRegisterPush(it, token) }
            }
        }
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
        val key = "$url|$appId|$token"
        if (client != null && key == sessionKey) {
            // FCM wake-up or a redelivered intent on top of a live session.
            return START_REDELIVER_INTENT
        }
        sessionKey = key
        startSession(url, token, appId)
        // A system restart re-delivers the last intent, so the socket comes back by itself.
        return START_REDELIVER_INTENT
    }

    override fun onDestroy() {
        alive = false
        sessionKey = null
        sessionJob?.cancel()
        sessionJob = null
        ackTimeoutJob?.cancel()
        ackTimeoutJob = null
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
        sentPushToken = null

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
            is HelloOk -> {
                SignalBus.update { SignalReducer.applyHelloOk(it, message, now) }
                // A new server session may have lost the token (restart, state file
                // reset), so register again on every hello_ok.
                sentPushToken = null
                registerPush(ws)
                flushAcks(ws)
            }
            is StatusMessage -> SignalBus.update { SignalReducer.applyStatus(it, message, now) }
            is Press -> handlePress(ws, message, now)
            is Ack -> handleAck(message)
            is ErrorMessage -> handleError(message)
            else -> Unit
        }
    }

    /** §4.6: remember the command ack and stop its 10 s watchdog. */
    private fun handleAck(ack: Ack) {
        if (ack.stage != AckStage.DELIVERED) return
        if (pendingAck.compareAndSet(ack.seq, NO_PENDING_ACK)) {
            ackTimeoutJob?.cancel()
            ackTimeoutJob = null
        }
        SignalBus.update { SignalReducer.applyCommandAck(it, ack) }
    }

    private fun sendTimer(durationS: Int) {
        if (durationS !in Protocol.MIN_TIMER_DURATION_S..Protocol.MAX_TIMER_DURATION_S) {
            SignalBus.update { state ->
                state.copy(
                    command = CommandUi(
                        phase = CommandPhase.FAILED,
                        kind = CommandKind.TIMER,
                        error = "Duration must be 1 minute … 24 hours.",
                    ),
                )
            }
            return
        }
        sendCommand(CommandKind.TIMER) { seq, tEpoch ->
            Protocol.timer(seq = seq, durationS = durationS, tEpoch = tEpoch)
        }
    }

    private fun sendCancel() {
        sendCommand(CommandKind.CANCEL) { seq, tEpoch ->
            Protocol.cancel(seq = seq, tEpoch = tEpoch)
        }
    }

    /**
     * §4.4/§4.6: send a command, show "waiting for confirmation", arm the 10 s
     * watchdog and let [handleAck] clear it.
     */
    private inline fun sendCommand(
        kind: CommandKind,
        build: (seq: Long, tEpoch: Long) -> Message,
    ) {
        val ws = client
        if (ws == null) {
            Log.w(TAG, "${kind.name.lowercase()} without a session")
            SignalBus.update { state ->
                state.copy(
                    command = CommandUi(
                        phase = CommandPhase.FAILED,
                        kind = kind,
                        error = "Not connected to the server.",
                    ),
                )
            }
            return
        }
        val seq = commandSeq.incrementAndGet()
        val tEpoch = serverEpochSec()
        SignalBus.update { SignalReducer.commandSending(it, seq, kind) }
        pendingAck.set(seq)
        if (!ws.send(build(seq, tEpoch))) {
            pendingAck.set(NO_PENDING_ACK)
            SignalBus.update { SignalReducer.commandTimeout(it, seq) }
            return
        }
        ackTimeoutJob?.cancel()
        ackTimeoutJob = scope.launch {
            delay(Protocol.ACK_TIMEOUT_MS)
            if (pendingAck.compareAndSet(seq, NO_PENDING_ACK)) {
                SignalBus.update { SignalReducer.commandTimeout(it, seq) }
            }
        }
        Log.i(TAG, "${kind.name.lowercase()} seq=$seq")
    }

    /** Server-scale epoch: the app's clock plus the offset learned from `hello_ok`/`status`. */
    private fun serverEpochSec(): Long =
        nowEpochSec() + (SignalBus.state.value.serverOffsetSec ?: 0L)

    private fun handlePress(ws: WsClient, press: Press, nowEpochSec: Long) {
        PressHandler.accept(this, press, nowEpochSec)
        // §4.6: the button needs ack(delivered) to leave SENT for WAIT ETA. Sent for
        // duplicates too, which is exactly how a lost ack gets retried.
        ws.send(Protocol.ackDelivered(press.seq))
        // The WS copy arrived while a push of the same press was queued.
        AckQueue.remove(press.seq)
    }

    /** §4.10: the token is idempotent; re-send it after every successful hello. */
    private fun registerPush(ws: WsClient) {
        val stored = SecureStore(this).fcmToken
        if (stored != null) {
            sendRegisterPush(ws, stored)
            return
        }
        // First connect after install: the token may still be being generated.
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            val token = if (task.isSuccessful) task.result else null
            if (token.isNullOrEmpty()) return@addOnCompleteListener
            SecureStore(this).fcmToken = token
            sendRegisterPush(ws, token)
        }
    }

    private fun sendRegisterPush(ws: WsClient, token: String) {
        // Two paths can race on the first connect (stored token + onNewToken).
        if (token == sentPushToken) return
        if (ws.send(Protocol.registerPush(token, serverEpochSec()))) {
            sentPushToken = token
        }
    }

    /** Send the acks of presses that were delivered by FCM before the socket existed. */
    private fun flushAcks(ws: WsClient) {
        for (seq in AckQueue.drain()) {
            ws.send(Protocol.ackDelivered(seq))
        }
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

        /**
         * Command seqs start here so they can never equal a press `seq` — both
         * arrive as `ack(stage=delivered)` and the app answers press acks too.
         */
        const val COMMAND_SEQ_BASE = 1_000_000L

        /** Sentinel for [pendingAck]; real command seqs start at [COMMAND_SEQ_BASE]. */
        private const val NO_PENDING_ACK = 0L

        /** True between [onCreate] and [onDestroy]; used by [ensureStarted]. */
        @Volatile
        private var alive = false

        fun start(context: Context, url: String, token: String, appId: String) {
            // S6: a push wakes the service without the activity, so remember where
            // to connect; the URL is not a secret, but it lives with the token.
            SecureStore(context).serverUrl = url
            val intent = Intent(context, SignalService::class.java)
                .putExtra(EXTRA_URL, url)
                .putExtra(EXTRA_TOKEN, token)
                .putExtra(EXTRA_APP_ID, appId)
            ContextCompat.startForegroundService(context, intent)
        }

        /**
         * Start only if the service is not running. A push arrives for every press,
         * so a running service must not restart its session on each message.
         */
        fun ensureStarted(context: Context, url: String, token: String, appId: String) {
            if (alive) return
            start(context, url, token, appId)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, SignalService::class.java))
        }
    }
}
