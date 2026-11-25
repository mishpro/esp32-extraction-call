"""Message models mirror plan/schema/messages.schema.json (§4)."""

from __future__ import annotations

import json

import pytest
from proto_helpers import ack_frame, hello_frame, press_frame

from app.errors import ErrorCode, ProtocolError
from app.models import Hello, Press, Timer, dump, parse_client_message, parse_hello


def test_hello_roundtrip() -> None:
    hello = parse_hello(json.dumps(hello_frame()))
    assert isinstance(hello, Hello)
    assert hello.role.value == "device"
    assert hello.id == "a1b2c3d4"
    assert hello.fw == "1.0.0"


def test_hello_requires_token() -> None:
    frame = hello_frame()
    del frame["token"]
    with pytest.raises(ProtocolError) as info:
        parse_hello(json.dumps(frame))
    assert info.value.code is ErrorCode.BAD_REQUEST
    assert info.value.close_code == 4400


def test_hello_rejects_extra_fields() -> None:
    frame = hello_frame()
    frame["payload"] = {}
    with pytest.raises(ProtocolError) as info:
        parse_hello(json.dumps(frame))
    assert info.value.code is ErrorCode.BAD_REQUEST


def test_hello_rejects_short_or_uppercase_id() -> None:
    for bad_id in ("a1b2c3", "A1B2C3D4", "zzzzzzzz"):
        with pytest.raises(ProtocolError) as info:
            parse_hello(json.dumps(hello_frame(id=bad_id)))
        assert info.value.code is ErrorCode.BAD_REQUEST


def test_validation_message_does_not_echo_token() -> None:
    frame = hello_frame()
    frame["token"] = 12345678901234567890  # not a string
    with pytest.raises(ProtocolError) as info:
        parse_hello(json.dumps(frame))
    assert "12345678901234567890" not in str(info.value)


def test_press_from_alias_roundtrip() -> None:
    frame = press_frame(7)
    frame["from"] = "a1b2c3d4"
    press = parse_client_message(json.dumps(frame))
    assert isinstance(press, Press)
    assert press.sender == "a1b2c3d4"
    assert dump(press)["from"] == "a1b2c3d4"


def test_press_rejects_extra_field() -> None:
    frame = press_frame(7)
    frame["duration_s"] = 60
    with pytest.raises(ProtocolError) as info:
        parse_client_message(json.dumps(frame))
    assert info.value.code is ErrorCode.BAD_REQUEST


def test_timer_bounds_are_enforced() -> None:
    frame = {"v": 1, "t": "timer", "seq": 3, "duration_s": 59, "t_epoch": 1}
    with pytest.raises(ProtocolError) as info:
        parse_client_message(json.dumps(frame))
    assert info.value.code is ErrorCode.BAD_DURATION
    assert info.value.close_code is None


def test_timer_valid_is_parsed() -> None:
    frame = {"v": 1, "t": "timer", "seq": 3, "duration_s": 60, "t_epoch": 1}
    message = parse_client_message(json.dumps(frame))
    assert isinstance(message, Timer)
    assert message.duration_s == 60


def test_ack_requires_stage() -> None:
    frame = ack_frame(7)
    del frame["stage"]
    with pytest.raises(ProtocolError) as info:
        parse_client_message(json.dumps(frame))
    assert info.value.code is ErrorCode.BAD_REQUEST


def test_version_must_be_one() -> None:
    with pytest.raises(ProtocolError) as info:
        parse_client_message(json.dumps({**press_frame(1), "v": 2}))
    assert info.value.close_code == 4400


def test_unknown_type_rejected() -> None:
    with pytest.raises(ProtocolError) as info:
        parse_client_message(json.dumps({"v": 1, "t": "register_push"}))
    assert info.value.code is ErrorCode.BAD_REQUEST
