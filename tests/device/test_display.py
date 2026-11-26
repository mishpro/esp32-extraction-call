"""Pure text layout for the 128x64 screen."""

import display


def test_every_line_fits_16_columns():
    model = {
        "ws": True,
        "app": True,
        "state": "AUTH FAIL",
        "time": "23:59:59",
        "last": "23:59:59",
        "seq": 12345,
        "notice": None,
    }
    for line in display.screen_lines(model):
        assert len(line) <= display.COLS


def test_status_bar_and_wait_eta_spelling():
    lines = display.screen_lines(
        {
            "ws": True,
            "app": False,
            "state": "WAIT_ETA",
            "time": "10:42:56",
            "last": "10:41:02",
            "seq": 7,
        }
    )
    assert lines[0] == "WS:OK APP:OFF"
    assert lines[1] == "TIME 10:42:56"
    assert lines[2] == "STATE: WAIT ETA"
    assert lines[3] == "LAST 10:41:02"
    assert lines[4] == "SEQ 7"


def test_notice_replaces_state_line():
    lines = display.screen_lines(
        {"ws": True, "app": False, "state": "ARMED", "notice": "NO APP"}
    )
    assert lines[2] == "NO APP"


def test_defaults_before_sync():
    lines = display.screen_lines({})
    assert lines[0] == "WS:ERR APP:OFF"
    assert lines[1] == "TIME --:--:--"
    assert lines[2] == "STATE: BOOT"
    assert lines[3] == "LAST --:--:--"
    assert lines[4] == "SEQ 0"


def test_long_state_is_truncated_not_wrapped():
    lines = display.screen_lines({"state": "SOMETHING VERY LONG"})
    assert lines[2] == "STATE: SOMETHING"


def test_countdown_shows_time_left():
    lines = display.screen_lines(
        {
            "ws": True,
            "app": True,
            "state": "COUNTDOWN",
            "time": "10:42:56",
            "last": "10:41:02",
            "seq": 7,
            "left": "02:59:58",
        }
    )
    assert lines[2] == "STATE: COUNTDOWN"
    assert lines[3] == "LEFT 02:59:58"
    # 16 columns: "STATE: COUNTDOWN" is exactly the limit.
    assert all(len(line) <= display.COLS for line in lines)


def test_arrived_is_a_plain_state_line():
    lines = display.screen_lines(
        {"ws": True, "app": True, "state": "ARRIVED", "left": "00:00:00"}
    )
    assert lines[2] == "STATE: ARRIVED"
    assert lines[3] == "LAST --:--:--"
