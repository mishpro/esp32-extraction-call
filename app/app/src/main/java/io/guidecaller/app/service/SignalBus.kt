package io.guidecaller.app.service

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Process-wide bridge between [SignalService] and the UI.
 *
 * A foreground service lives longer than any activity, so the activity must be
 * able to observe it without binding: the service writes here, the UI collects
 * [state]. The service resets it on stop.
 */
object SignalBus {

    private val _state = MutableStateFlow(SignalState())
    val state: StateFlow<SignalState> = _state.asStateFlow()

    fun update(transform: (SignalState) -> SignalState) {
        _state.update(transform)
    }

    fun reset() {
        _state.value = SignalState()
    }
}
