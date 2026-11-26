package io.nightcall.app.service

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Process-wide channel for S4 commands (`timer`/`cancel`) from the activity to
 * [SignalService].
 *
 * Intents would work too, but `onStartCommand`'s return value is per service
 * instance: returning `START_NOT_STICKY` for a one-shot command would downgrade
 * the socket service and stop it from being restarted after a kill. A flow
 * keeps the service's `START_REDELIVER_INTENT` contract untouched.
 *
 * If the service is not running, an emitted command is simply not collected;
 * the UI only enables the buttons while the session is connected.
 */
object CommandBus {

    sealed interface Command {
        /** §4.4: `timer(duration_s)`. */
        data class SetTimer(val durationS: Int) : Command

        /** §4.5: `cancel`. */
        data object Cancel : Command
    }

    private val _commands = MutableSharedFlow<Command>(
        replay = 0,
        extraBufferCapacity = 16,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val commands: SharedFlow<Command> = _commands.asSharedFlow()

    fun send(command: Command) {
        _commands.tryEmit(command)
    }
}
