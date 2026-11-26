package io.nightcall.app.service

import java.util.concurrent.ConcurrentLinkedQueue
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * `ack(delivered)` for presses that arrived while the socket was down (S6).
 *
 * A push can wake the process without a WebSocket: the notification is shown at
 * once, and the seq waits here until [SignalService] finishes `hello` and sends
 * the ack. [wake] lets a running service flush immediately instead of waiting
 * for a reconnect; without subscribers the queue is still drained on connect.
 */
object AckQueue {

    private val queue = ConcurrentLinkedQueue<Long>()
    private val _wake = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val wake: SharedFlow<Unit> = _wake

    fun offer(seq: Long) {
        queue.add(seq)
        _wake.tryEmit(Unit)
    }

    /** The WS copy of the same press already answered for this seq. */
    fun remove(seq: Long) {
        queue.remove(seq)
    }

    fun drain(): List<Long> {
        val out = mutableListOf<Long>()
        while (true) {
            out += queue.poll() ?: break
        }
        return out
    }
}
