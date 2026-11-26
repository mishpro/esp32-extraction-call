package io.nightcall.app.push

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * Token rotations happen while the socket is connected: `onNewToken` cannot
 * touch [io.nightcall.app.service.SignalService] directly, so it publishes here
 * and the service re-registers (§4.10). Without a subscriber the emission is
 * dropped on purpose — the service re-reads the stored token on every `hello_ok`.
 */
object PushTokenBus {

    private val _tokens = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val tokens: SharedFlow<String> = _tokens

    fun publish(token: String) {
        _tokens.tryEmit(token)
    }
}
