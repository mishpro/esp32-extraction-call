"""State machine and server clock (pure, CPython)."""

import state


def test_fmt_hms():
    assert state.fmt_hms(0) == "00:00:00"
    assert state.fmt_hms(3661) == "01:01:01"
    assert state.fmt_hms(86399) == "23:59:59"
    assert state.fmt_hms(None) == "--:--:--"


def test_clock_sync_offset_and_drift():
    clock = state.Clock(offset_h=3)
    assert clock.hms(0) == "--:--:--"
    clock.sync(1793001234, 1000)
    assert clock.now(1000) == 1793001234
    assert clock.now(3000) == 1793001236
    assert clock.hms(1000) == state.fmt_hms(1793001234 + 3 * 3600)


def test_clock_is_wrap_safe():
    clock = state.Clock()
    base = state.TICKS_MASK - 1000
    clock.sync(1000, base)
    now = (base + 5000) & state.TICKS_MASK
    assert clock.now(now) == 1005


def test_boot_to_armed():
    sm = state.StateMachine()
    assert sm.state == state.BOOT
    sm.ws_connected(100, True)
    assert sm.state == state.ARMED
    assert sm.app_online is True


def test_press_is_ignored_while_sent():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    assert sm.press(100) == 1
    assert sm.state == state.SENT
    assert sm.press(200) is None
    assert sm.seq == 1


def test_ack_server_keeps_sent_then_delivered_moves_to_wait_eta():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.press(100)
    sm.on_ack("server", True, 150)
    assert sm.state == state.SENT
    sm.on_ack("delivered", True, 200)
    assert sm.state == state.WAIT_ETA
    assert sm.press(300) == 2


def test_negative_delivery_returns_to_armed():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.press(100)
    sm.on_ack("delivered", False, 200)
    assert sm.state == state.ARMED


def test_ack_timeout_becomes_no_ack():
    sm = state.StateMachine(ack_timeout_ms=10000)
    sm.ws_connected(0, True)
    sm.press(1000)
    sm.tick(10999)
    assert sm.state == state.SENT
    sm.tick(11000)
    assert sm.state == state.NO_ACK
    assert sm.press(12000) == 2


def test_no_peer_notice_expires():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.press(100)
    sm.on_error("no_peer", 100)
    assert sm.state == state.ARMED
    assert sm.notice == state.NO_APP
    sm.tick(5099)
    assert sm.notice == state.NO_APP
    sm.tick(5100)
    assert sm.notice is None


def test_auth_failed_is_terminal_for_ws_lost():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.auth_failed()
    assert sm.state == state.AUTH_FAIL
    sm.ws_lost()
    assert sm.state == state.AUTH_FAIL


def test_ws_lost_goes_offline_and_clears_app():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.ws_lost()
    assert sm.state == state.OFFLINE
    assert sm.app_online is False


def test_status_updates_app_presence():
    sm = state.StateMachine()
    sm.ws_connected(0, True)
    sm.on_status(False)
    assert sm.app_online is False
    sm.on_status(True)
    assert sm.app_online is True
