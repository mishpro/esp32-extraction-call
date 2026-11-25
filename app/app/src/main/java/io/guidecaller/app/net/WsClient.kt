package io.guidecaller.app.net

import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume
import kotlin.random.Random
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/** Connection state exposed to the UI/service. */
sealed interface ConnectionState {

    /** [WsClient.connect] has not been called yet. */
    data object Idle : ConnectionState

    /** TCP/HTTP upgrade (and `hello`) in flight; [attempt] is 0 for the first try. */
    data class Connecting(val attempt: Int) : ConnectionState

    /** `hello_ok` received — authenticated, the session is alive. */
    data class Connected(val session: String) : ConnectionState

    /** Waiting out the backoff before the next try; [attempt] counts consecutive failures. */
    data class Reconnecting(val attempt: Int, val delayMs: Long) : ConnectionState

    /** Permanent failure, no reconnect (bad URL or protocol `auth_failed`). */
    data class Failed(val reason: String) : ConnectionState

    /** [WsClient.disconnect] was called. */
    data object Disconnected : ConnectionState
}

/**
 * OkHttp WebSocket client for protocol v1 (drafts/Протокол.md §4, §6, §9).
 *
 * Behaviour:
 *  - `hello(role=app)` is the very first frame of every connection;
 *  - `ping` from the server is answered with `pong` immediately;
 *  - no `hello_ok` within [Protocol.HELLO_TIMEOUT_MS] → the socket is closed and the
 *    client reconnects;
 *  - reconnect uses [Backoff] (max 60 s); the counter resets after `hello_ok`;
 *  - `error(code=auth_failed)` or close 4401 stops reconnecting and reports
 *    [ConnectionState.Failed] (`AUTH FAIL`);
 *  - malformed/non-JSON frames are ignored, binary frames are ignored (protocol is text JSON).
 *
 * It does not show notifications and does not ack `press` — that is service/UI work (S3.5–3.7).
 * Outgoing messages are sent through [send]; incoming typed messages go to [messages].
 */
class WsClient(
    private val scope: CoroutineScope,
    private val client: OkHttpClient = defaultOkHttpClient(),
    private val random: Random = Random.Default,
) {

    private data class Params(val url: String, val token: String, val appId: String)

    private sealed interface Outcome {
        /** Socket closed; [helloSeen] tells whether the backoff counter must reset. */
        data class Closed(val code: Int, val helloSeen: Boolean) : Outcome
        data class Failure(val cause: Throwable, val helloSeen: Boolean) : Outcome
        data class AuthFailed(val code: ErrorCode) : Outcome
        data class Fatal(val reason: String) : Outcome
    }

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Idle)
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val _messages = MutableSharedFlow<Message>(
        replay = 0,
        extraBufferCapacity = 64,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val messages: SharedFlow<Message> = _messages.asSharedFlow()

    /** Bumped on every [connect]/[disconnect]; stale callbacks compare against it. */
    private val generation = AtomicInteger(0)
    private var loopJob: Job? = null

    @Volatile
    private var socket: WebSocket? = null

    /**
     * Starts a session: connects to [url] and authenticates as an app with [appId]/[token].
     * Replaces any previous session. Returns immediately; observe [state] and [messages].
     */
    fun connect(url: String, token: String, appId: String) {
        val gen = generation.incrementAndGet()
        loopJob?.cancel()
        socket?.cancel()
        socket = null
        val params = Params(url = url, token = token, appId = appId)
        loopJob = scope.launch(Dispatchers.IO) { reconnectLoop(gen, params) }
    }

    /** Stops reconnecting and closes the socket with code 1000. */
    fun disconnect() {
        generation.incrementAndGet()
        loopJob?.cancel()
        loopJob = null
        socket?.close(Protocol.CLOSE_NORMAL, "client closed")
        socket = null
        _state.value = ConnectionState.Disconnected
    }

    /** Sends [message] if a socket is open. False when not connected / frame not queued. */
    fun send(message: Message): Boolean = socket?.send(Protocol.encode(message)) == true

    private suspend fun reconnectLoop(gen: Int, params: Params) {
        val currentGeneration = { gen == generation.get() }
        var attempt = 0

        while (currentCoroutineContext().isActive && currentGeneration()) {
            publish(gen, ConnectionState.Connecting(attempt))

            val helloSeen: Boolean
            when (val outcome = runConnection(gen, params)) {
                is Outcome.AuthFailed -> {
                    publish(gen, ConnectionState.Failed("auth_failed"))
                    return
                }

                is Outcome.Fatal -> {
                    socket = null
                    publish(gen, ConnectionState.Failed(outcome.reason))
                    return
                }

                is Outcome.Closed -> {
                    helloSeen = outcome.helloSeen
                }

                is Outcome.Failure -> {
                    helloSeen = outcome.helloSeen
                }
            }

            if (!currentGeneration()) return
            // §6: successful hello_ok resets the counter; otherwise it grows.
            if (helloSeen) attempt = 0
            // attempt = 0 → ~1 s, then 2, 4, 8, 16, 32, 60 s (jittered).
            val delayMs = Backoff.delayMs(attempt, random)
            attempt = (attempt + 1).coerceAtMost(10)
            publish(gen, ConnectionState.Reconnecting(attempt = attempt, delayMs = delayMs))
            delay(delayMs)
        }
    }

    @Suppress("LongMethod")
    private suspend fun runConnection(gen: Int, params: Params): Outcome {
        val request = try {
            Request.Builder().url(params.url).build()
        } catch (_: IllegalArgumentException) {
            return Outcome.Fatal("bad url")
        }

        return suspendCancellableCoroutine { cont ->
            val finished = AtomicBoolean(false)
            val helloSeen = AtomicBoolean(false)
            var watchdog: Job? = null

            fun finish(outcome: Outcome) {
                if (!finished.compareAndSet(false, true)) return
                watchdog?.cancel()
                if (cont.isActive) cont.resume(outcome)
            }

            val listener = object : WebSocketListener() {

                override fun onOpen(webSocket: WebSocket, response: Response) {
                    if (gen != generation.get()) {
                        webSocket.cancel()
                        finish(Outcome.Closed(Protocol.CLOSE_NORMAL, helloSeen = false))
                        return
                    }
                    socket = webSocket
                    // §4.1: hello must be the first frame of every new session.
                    val queued = webSocket.send(
                        Protocol.encode(Protocol.helloApp(appId = params.appId, token = params.token)),
                    )
                    if (!queued) {
                        webSocket.cancel()
                        finish(Outcome.Failure(IOException("hello frame not queued"), helloSeen = false))
                        return
                    }
                    watchdog = scope.launch {
                        delay(Protocol.HELLO_TIMEOUT_MS)
                        if (!helloSeen.get() && !finished.get()) {
                            webSocket.close(Protocol.CLOSE_PROTOCOL_ERROR, "hello timeout")
                        }
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    val message = Protocol.parseOrNull(text) ?: return
                    when (message) {
                        is Ping -> {
                            // §4.8: pong echoes the server time and goes out immediately.
                            webSocket.send(Protocol.encode(Protocol.pong(message.tEpoch)))
                            emitMessage(gen, message)
                        }

                        is HelloOk -> {
                            helloSeen.set(true)
                            watchdog?.cancel()
                            publish(gen, ConnectionState.Connected(message.session))
                            emitMessage(gen, message)
                        }

                        is ErrorMessage -> {
                            emitMessage(gen, message)
                            when (message.code) {
                                // §4.9: fatal for auth, close without reconnect.
                                ErrorCode.AUTH_FAILED -> {
                                    webSocket.close(Protocol.CLOSE_AUTH_FAILED, "auth_failed")
                                    finish(Outcome.AuthFailed(message.code))
                                }
                                // §4.9: protocol violation → close, reconnect with backoff.
                                ErrorCode.BAD_REQUEST -> webSocket.close(
                                    Protocol.CLOSE_PROTOCOL_ERROR,
                                    "bad_request",
                                )
                                ErrorCode.BAD_DURATION,
                                ErrorCode.NO_PEER,
                                ErrorCode.DUPLICATE,
                                ErrorCode.INTERNAL,
                                -> Unit
                            }
                        }

                        else -> emitMessage(gen, message)
                    }
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(Protocol.CLOSE_NORMAL, null)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    clearSocket(webSocket)
                    if (code == Protocol.CLOSE_AUTH_FAILED) {
                        finish(Outcome.AuthFailed(ErrorCode.AUTH_FAILED))
                    } else {
                        finish(Outcome.Closed(code, helloSeen.get()))
                    }
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    clearSocket(webSocket)
                    finish(Outcome.Failure(t, helloSeen.get()))
                }
            }

            val webSocket = client.newWebSocket(request, listener)
            if (gen != generation.get()) {
                webSocket.cancel()
                finish(Outcome.Closed(Protocol.CLOSE_NORMAL, helloSeen.get()))
            } else {
                socket = webSocket
            }
            cont.invokeOnCancellation {
                watchdog?.cancel()
                webSocket.cancel()
            }
        }
    }

    private fun publish(gen: Int, newState: ConnectionState) {
        if (gen == generation.get()) _state.value = newState
    }

    private fun emitMessage(gen: Int, message: Message) {
        if (gen == generation.get()) _messages.tryEmit(message)
    }

    private fun clearSocket(webSocket: WebSocket) {
        if (socket === webSocket) socket = null
    }
}

private fun defaultOkHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(10, TimeUnit.SECONDS)
    .build()
