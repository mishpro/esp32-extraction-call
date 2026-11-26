package io.guidecaller.app.service

import io.guidecaller.app.net.Ack
import io.guidecaller.app.net.AckStage
import io.guidecaller.app.net.AppStatus
import io.guidecaller.app.net.ButtonState
import io.guidecaller.app.net.DeviceStatus
import io.guidecaller.app.net.ErrorCode
import io.guidecaller.app.net.ErrorMessage
import io.guidecaller.app.net.HelloOk
import io.guidecaller.app.net.Peer
import io.guidecaller.app.net.Press
import io.guidecaller.app.net.Protocol
import io.guidecaller.app.net.StatusMessage
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Plain-JVM checks for [SignalReducer]: the service only wires flows, so all the
 * decisions that can be wrong (clock offset, seq dedup, list bound, time text) live here.
 */
class SignalReducerTest {

    @Test
    fun helloOkStoresOffsetAndPeer() {
        val state = SignalReducer.applyHelloOk(
            SignalState(),
            helloOk(tEpoch = 1_700_000_000, online = true, state = ButtonState.ARMED),
            nowEpochSec = 1_699_999_950,
        )

        assertEquals(50L, state.serverOffsetSec)
        assertTrue(state.deviceOnline)
        assertEquals("ARMED", state.deviceState)
        assertTrue(state.appOnline)
        assertFalse(state.authFailed)
    }

    @Test
    fun helloOkClearsPreviousAuthFailure() {
        val state = SignalReducer.applyHelloOk(
            SignalState(authFailed = true),
            helloOk(tEpoch = 100, online = false, state = ButtonState.OFFLINE),
            nowEpochSec = 100,
        )

        assertFalse(state.authFailed)
        assertFalse(state.deviceOnline)
    }

    @Test
    fun statusUpdatesOffsetAndBothPeers() {
        val state = SignalReducer.applyStatus(
            SignalState(),
            status(tEpoch = 1_700_000_100, deviceOnline = true, state = ButtonState.COUNTDOWN, appOnline = false),
            nowEpochSec = 1_700_000_099,
        )

        assertEquals(1L, state.serverOffsetSec)
        assertTrue(state.deviceOnline)
        assertEquals("COUNTDOWN", state.deviceState)
        assertFalse(state.appOnline)
    }

    @Test
    fun pressIsPrependedWithBothTimestamps() {
        val result = SignalReducer.applyPress(
            SignalState(),
            press(seq = 7, tEpoch = 1_700_000_500, from = "a1b2c3d4"),
            nowEpochSec = 1_700_000_499,
        )

        assertTrue(result.added)
        val item = result.state.presses.single()
        assertEquals(7L, item.seq)
        assertEquals(1_700_000_500L, item.serverEpoch)
        assertEquals(1_700_000_499L, item.localEpoch)
        assertEquals("a1b2c3d4", item.from)
    }

    @Test
    fun newerPressGoesFirst() {
        val first = SignalReducer.applyPress(SignalState(), press(seq = 1), nowEpochSec = 10).state
        val second = SignalReducer.applyPress(first, press(seq = 2), nowEpochSec = 11).state

        assertEquals(listOf(2L, 1L), second.presses.map { it.seq })
    }

    @Test
    fun duplicateSeqIsDropped() {
        val once = SignalReducer.applyPress(SignalState(), press(seq = 7), nowEpochSec = 10).state
        val again = SignalReducer.applyPress(once, press(seq = 7), nowEpochSec = 20)

        assertFalse("seq 7 must be deduplicated", again.added)
        assertEquals(1, again.state.presses.size)
        // The original item is untouched: no second notification, no reordering.
        assertEquals(once.presses, again.state.presses)
    }

    @Test
    fun sameSeqAfterRebootIsNotDropped() {
        val once = SignalReducer.applyPress(
            SignalState(),
            press(seq = 1, tEpoch = 1_700_000_000, from = "a1b2c3d4"),
            nowEpochSec = 10,
        ).state
        val rebooted = SignalReducer.applyPress(
            once,
            press(seq = 1, tEpoch = 1_700_003_600, from = "a1b2c3d4"),
            nowEpochSec = 20,
        )

        assertTrue("a new press epoch starts seq from 1 again", rebooted.added)
        assertEquals(2, rebooted.state.presses.size)
    }

    @Test
    fun sameSeqFromAnotherDeviceIsNotDropped() {
        val once = SignalReducer.applyPress(
            SignalState(),
            press(seq = 1, from = "a1b2c3d4"),
            nowEpochSec = 10,
        ).state
        val other = SignalReducer.applyPress(
            once,
            press(seq = 1, from = "deadbeef"),
            nowEpochSec = 20,
        )

        assertTrue(other.added)
        assertEquals(listOf("deadbeef", "a1b2c3d4"), other.state.presses.map { it.from })
    }

    @Test
    fun pressListIsBoundedTo100Newest() {
        var state = SignalState()
        repeat(SignalReducer.MAX_PRESSES + 5) { index ->
            state = SignalReducer.applyPress(state, press(seq = index + 1L), nowEpochSec = index.toLong()).state
        }

        assertEquals(SignalReducer.MAX_PRESSES, state.presses.size)
        assertEquals(SignalReducer.MAX_PRESSES + 5L, state.presses.first().seq)
        assertEquals(6L, state.presses.last().seq)
    }

    @Test
    fun noPeerSetsLastError() {
        val state = SignalReducer.applyError(SignalState(), error(ErrorCode.NO_PEER))

        assertEquals(SignalReducer.CODE_NO_PEER, state.lastError)
        assertFalse(state.authFailed)
    }

    @Test
    fun statusClearsNoPeerWhenButtonReturns() {
        val offline = SignalReducer.applyError(SignalState(), error(ErrorCode.NO_PEER))
        assertEquals(SignalReducer.CODE_NO_PEER, offline.lastError)

        val online = SignalReducer.applyStatus(
            offline,
            status(tEpoch = 100, deviceOnline = true, state = ButtonState.ARMED, appOnline = true),
            nowEpochSec = 100,
        )

        assertNull(online.lastError)
    }

    @Test
    fun authFailedSetsFlag() {
        val state = SignalReducer.applyError(SignalState(), error(ErrorCode.AUTH_FAILED))

        assertTrue(state.authFailed)
        assertNull(state.lastError)
    }

    @Test
    fun otherErrorsDoNotChangeState() {
        val original = SignalState(deviceState = "ARMED")

        assertSameState(original, SignalReducer.applyError(original, error(ErrorCode.DUPLICATE)))
        assertSameState(original, SignalReducer.applyError(original, error(ErrorCode.INTERNAL)))
    }

    // --- S4: countdown and command delivery --------------------------------

    @Test
    fun statusCountdownConvertsRemainingToALocalDeadline() {
        val state = SignalReducer.applyStatus(
            SignalState(),
            status(
                tEpoch = 1_000,
                deviceOnline = true,
                state = ButtonState.COUNTDOWN,
                appOnline = true,
                remainingSeconds = 120,
            ),
            nowEpochSec = 1_000,
        )

        assertEquals(1_120L, state.countdownEndsAtLocalEpochSec)
        assertTrue(state.countdownActive)
    }

    @Test
    fun statusArmedClearsTheCountdown() {
        val running = SignalState(
            deviceOnline = true,
            deviceState = "COUNTDOWN",
            countdownEndsAtLocalEpochSec = 1_120,
        )
        val state = SignalReducer.applyStatus(
            running,
            status(tEpoch = 1_010, deviceOnline = true, state = ButtonState.ARMED, appOnline = true),
            nowEpochSec = 1_010,
        )

        assertNull(state.countdownEndsAtLocalEpochSec)
        assertFalse(state.countdownActive)
    }

    @Test
    fun arrivedMeansZeroTimeLeft() {
        val state = SignalReducer.applyStatus(
            SignalState(),
            status(tEpoch = 2_000, deviceOnline = true, state = ButtonState.ARRIVED, appOnline = true),
            nowEpochSec = 2_000,
        )

        assertEquals(2_000L, state.countdownEndsAtLocalEpochSec)
        assertTrue(state.countdownActive)
    }

    @Test
    fun helloOkRestoresACountdownAfterAppRestart() {
        val state = SignalReducer.applyHelloOk(
            SignalState(),
            helloOk(
                tEpoch = 5_000,
                online = true,
                state = ButtonState.COUNTDOWN,
                remainingSeconds = 600,
            ),
            nowEpochSec = 5_000,
        )

        assertEquals(5_600L, state.countdownEndsAtLocalEpochSec)
    }

    @Test
    fun sendingATimerWaitsForConfirmation() {
        val state = SignalReducer.commandSending(SignalState(), seq = 1_000_001, kind = CommandKind.TIMER)

        assertEquals(CommandPhase.PENDING, state.command.phase)
        assertEquals(CommandKind.TIMER, state.command.kind)
        assertEquals(1_000_001L, state.command.seq)
    }

    @Test
    fun matchingDeliveredAckClearsThePendingCommand() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.TIMER)
        val state = SignalReducer.applyCommandAck(pending, ack(seq = 7))

        assertEquals(CommandPhase.IDLE, state.command.phase)
        assertNull(state.command.error)
    }

    @Test
    fun pressEchoAckDoesNotCompleteAPendingCommand() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 1_000_001, kind = CommandKind.TIMER)
        // Press acks reach the app too and carry the device seq, not ours.
        val state = SignalReducer.applyCommandAck(pending, ack(seq = 9))

        assertEquals(CommandPhase.PENDING, state.command.phase)
    }

    @Test
    fun rejectedAckFailsTheCommand() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.CANCEL)
        val state = SignalReducer.applyCommandAck(pending, ack(seq = 7, ok = false))

        assertEquals(CommandPhase.FAILED, state.command.phase)
        assertEquals("The button rejected the command.", state.command.error)
    }

    @Test
    fun ackTimeoutFailsWithTheProtocolWording() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.TIMER)
        val state = SignalReducer.commandTimeout(pending, seq = 7)

        assertEquals(CommandPhase.FAILED, state.command.phase)
        assertEquals("Button did not answer — time not set.", state.command.error)
    }

    @Test
    fun cancelTimeoutSaysTheCancelWasNotApplied() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.CANCEL)
        val state = SignalReducer.commandTimeout(pending, seq = 7)

        assertEquals("Button did not answer — cancel not applied.", state.command.error)
    }

    @Test
    fun staleTimeoutIsIgnored() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.TIMER)
        val state = SignalReducer.commandTimeout(pending, seq = 6)

        assertEquals(CommandPhase.PENDING, state.command.phase)
    }

    @Test
    fun badDurationFailsTheTimerCommand() {
        val pending = SignalReducer.commandSending(SignalState(), seq = 7, kind = CommandKind.TIMER)
        val state = SignalReducer.applyError(pending, error(ErrorCode.BAD_DURATION))

        assertEquals(CommandPhase.FAILED, state.command.phase)
        assertEquals(CommandKind.TIMER, state.command.kind)
    }

    @Test
    fun formatTimeIsHhMmSsInTheGivenZone() {
        assertEquals("01:01:01", SignalReducer.formatTime(3_661, ZoneId.of("UTC")))
        assertEquals("00:00:00", SignalReducer.formatTime(0, ZoneId.of("UTC")))
    }

    @Test
    fun formatTimeUsesTepochFromPress() {
        // 1793000400 → 07:40:00 UTC — server time, not the receive time.
        assertEquals("07:40:00", SignalReducer.formatTime(1_793_000_400, ZoneId.of("UTC")))
    }

    @Test
    fun parsedPressReachesReducer() {
        // Wire → Protocol → reducer: the field names must line up end to end.
        val message = Protocol.parse(
            """{"v":1,"t":"press","seq":9,"t_epoch":1700000000,"from":"a1b2c3d4"}""",
        )
        val result = SignalReducer.applyPress(SignalState(), message as Press, nowEpochSec = 1_700_000_001)

        assertTrue(result.added)
        assertEquals(9L, result.state.presses.first().seq)
        assertEquals("a1b2c3d4", result.state.presses.first().from)
    }

    private fun helloOk(
        tEpoch: Long,
        online: Boolean,
        state: ButtonState,
        remainingSeconds: Long? = null,
    ): HelloOk = HelloOk(
        v = Protocol.VERSION,
        session = "s-test",
        tEpoch = tEpoch,
        peer = Peer(
            id = "a1b2c3d4",
            online = online,
            state = state,
            remainingSeconds = remainingSeconds,
        ),
    )

    private fun status(
        tEpoch: Long,
        deviceOnline: Boolean,
        state: ButtonState,
        appOnline: Boolean,
        remainingSeconds: Long? = null,
    ): StatusMessage = StatusMessage(
        v = Protocol.VERSION,
        tEpoch = tEpoch,
        device = DeviceStatus(
            online = deviceOnline,
            state = state,
            remainingSeconds = remainingSeconds,
        ),
        app = AppStatus(online = appOnline),
    )

    private fun ack(seq: Long, ok: Boolean = true): Ack =
        Ack(v = Protocol.VERSION, seq = seq, ok = ok, stage = AckStage.DELIVERED)

    private fun press(seq: Long, tEpoch: Long = 1_700_000_000, from: String? = null): Press =
        Press(v = Protocol.VERSION, seq = seq, tEpoch = tEpoch, from = from)

    private fun error(code: ErrorCode): ErrorMessage =
        ErrorMessage(v = Protocol.VERSION, code = code, msg = "test")

    private fun assertSameState(expected: SignalState, actual: SignalState) {
        assertEquals(expected, actual)
    }
}
