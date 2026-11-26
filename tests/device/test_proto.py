"""Сообщения протокола (device/proto.py) на CPython.

Покрытие: round-trip всех сборщиков, ручная валидация обязательных полей,
лимит 4096 байт на сборке и разборе, все 13 JSON-примеров из
drafts/Протокол.md §4, композиция с frames.py.
"""

import ast
import json
import pathlib

import frames
import proto
import pytest
from proto import ProtocolError

# --- 13 примеров из drafts/Протокол.md §4 (в порядке документа) -----------
# §4.1 hello
# §4.1 error(auth_failed) — ответ на плохой токен
# §4.2 hello_ok
# §4.3 press от кнопки
# §4.3 press после пересылки (from)
# §4.4 timer от приложения
# §4.4 timer после пересылки (deadline_t)
# §4.5 cancel
# §4.6 ack
# §4.7 status
# §4.8 ping
# §4.8 pong
# §4.9 error(bad_duration)

EXAMPLES = [
    {
        "v": 1,
        "t": "hello",
        "role": "device",
        "token": "9f2c…",
        "id": "a1b2c3d4",
        "fw": "1.0.0",
    },
    {"v": 1, "t": "error", "code": "auth_failed", "msg": "bad token"},
    {
        "v": 1,
        "t": "hello_ok",
        "session": "s-7f3a91",
        "t_epoch": 1793001234,
        "peer": {"id": "a1b2c3d4", "online": True, "state": "ARMED", "remaining_s": 10800},
    },
    {"v": 1, "t": "press", "seq": 7, "t_epoch": 1793001234},
    {"v": 1, "t": "press", "seq": 7, "t_epoch": 1793001234, "from": "a1b2c3d4"},
    {"v": 1, "t": "timer", "seq": 3, "duration_s": 10800, "t_epoch": 1793001300},
    {
        "v": 1,
        "t": "timer",
        "seq": 3,
        "duration_s": 10800,
        "deadline_t": 1793012100,
        "t_epoch": 1793001300,
    },
    {"v": 1, "t": "cancel", "seq": 4, "t_epoch": 1793001400},
    {"v": 1, "t": "ack", "seq": 7, "ok": True, "stage": "server"},
    {
        "v": 1,
        "t": "status",
        "t_epoch": 1793001500,
        "device": {"online": True, "state": "COUNTDOWN", "remaining_s": 10412, "fw": "1.0.0"},
        "app": {"online": True},
    },
    {"v": 1, "t": "ping", "t_epoch": 1793001600},
    {"v": 1, "t": "pong", "t_epoch": 1793001600},
    {"v": 1, "t": "error", "code": "bad_duration", "msg": "duration_s out of range", "seq": 3},
]

EXAMPLE_IDS = [f"{i + 1:02d}-{m['t']}" for i, m in enumerate(EXAMPLES)]


# --- сборка ---------------------------------------------------------------

def test_build_hello_device():
    raw = proto.build_hello("device", "9f2c", "a1b2c3d4", fw="1.0.0")
    assert json.loads(raw) == {
        "v": 1,
        "t": "hello",
        "role": "device",
        "token": "9f2c",
        "id": "a1b2c3d4",
        "fw": "1.0.0",
    }


def test_build_hello_without_fw_has_no_fw_key():
    msg = json.loads(proto.build_hello("device", "", "a1b2c3d4"))
    assert "fw" not in msg
    assert msg["token"] == ""  # MVP: авторизация выключена, токен пустой


def test_build_hello_app():
    msg = json.loads(proto.build_hello("app", "tok", "0f0e0d0c"))
    assert msg["role"] == "app"


def test_build_press():
    assert json.loads(proto.build_press(7, 1793001234)) == {
        "v": 1,
        "t": "press",
        "seq": 7,
        "t_epoch": 1793001234,
    }


def test_build_ping_and_pong():
    assert json.loads(proto.build_ping(1793001600)) == {"v": 1, "t": "ping", "t_epoch": 1793001600}
    assert json.loads(proto.build_pong(1793001600)) == {"v": 1, "t": "pong", "t_epoch": 1793001600}


@pytest.mark.parametrize("stage", ["server", "delivered"])
@pytest.mark.parametrize("ok", [True, False])
def test_build_ack(stage, ok):
    msg = json.loads(proto.build_ack(7, ok, stage))
    assert msg == {"v": 1, "t": "ack", "seq": 7, "ok": ok, "stage": stage}


def test_build_error_without_seq():
    msg = json.loads(proto.build_error("auth_failed", "bad token"))
    assert msg == {"v": 1, "t": "error", "code": "auth_failed", "msg": "bad token"}
    assert "seq" not in msg


def test_build_error_with_seq():
    msg = json.loads(proto.build_error("bad_duration", "duration_s out of range", seq=3))
    assert msg["seq"] == 3


@pytest.mark.parametrize(
    "raw",
    [
        proto.build_hello("device", "tok", "a1b2c3d4", fw="1.0.0"),
        proto.build_hello("device", "", "a1b2c3d4"),
        proto.build_press(1, 0),
        proto.build_press(2**31, 1793001234),
        proto.build_ping(1793001600),
        proto.build_pong(1793001600),
        proto.build_ack(1, True, "server"),
        proto.build_ack(1, False, "delivered"),
        proto.build_error("internal", "boom"),
        proto.build_error("no_peer", "no app", seq=5),
    ],
)
def test_builders_roundtrip(raw):
    msg = proto.parse_message(raw)
    assert json.loads(raw) == msg
    assert proto.parse_message(raw.encode("utf-8")) == msg


# --- валидация входных данных сборщиков -----------------------------------

@pytest.mark.parametrize(
    "kwargs",
    [
        {"role": "button", "token": "t", "id": "a1b2c3d4"},
        {"role": "device", "token": 1, "id": "a1b2c3d4"},
        {"role": "device", "token": "t", "id": "A1B2C3D4"},
        {"role": "device", "token": "t", "id": "a1b2c3d"},
        {"role": "device", "token": "t", "id": "a1b2c3dz"},
        {"role": "device", "token": "t", "id": None},
    ],
)
def test_build_hello_rejects_bad_args(kwargs):
    with pytest.raises(ValueError):
        proto.build_hello(**kwargs)


def test_build_hello_rejects_fw_for_app():
    with pytest.raises(ValueError):
        proto.build_hello("app", "tok", "a1b2c3d4", fw="1.0.0")


@pytest.mark.parametrize("seq,t_epoch", [(0, 1), (-1, 1), (1, -1), (True, 1), ("1", 1), (1, None)])
def test_build_press_rejects_bad_numbers(seq, t_epoch):
    with pytest.raises(ValueError):
        proto.build_press(seq, t_epoch)


def test_build_pong_rejects_float_epoch():
    with pytest.raises(ValueError):
        proto.build_pong(1793001600.0)


@pytest.mark.parametrize(
    "ok,stage",
    [(1, "server"), (True, "client"), (True, None), ("yes", "server")],
)
def test_build_ack_rejects_bad_args(ok, stage):
    with pytest.raises(ValueError):
        proto.build_ack(1, ok, stage)


def test_build_error_rejects_unknown_code():
    with pytest.raises(ValueError):
        proto.build_error("nope", "x")


def test_build_error_rejects_zero_seq():
    with pytest.raises(ValueError):
        proto.build_error("internal", "x", seq=0)


def test_build_error_rejects_non_str_msg():
    with pytest.raises(ValueError):
        proto.build_error("internal", 42)


# --- лимит 4096 байт ------------------------------------------------------

def test_size_limit_exact_boundary():
    base = len(proto.build_error("internal", "").encode("utf-8"))
    room = proto.MAX_MESSAGE_BYTES - base
    raw = proto.build_error("internal", "x" * room)
    assert len(raw.encode("utf-8")) == proto.MAX_MESSAGE_BYTES
    assert proto.parse_message(raw)["msg"] == "x" * room

    with pytest.raises(ValueError):
        proto.build_error("internal", "x" * (room + 1))


def test_size_limit_counts_bytes_not_chars():
    # 600 символов «я» — явно меньше 4096 символов, и всё же лимит считается
    # по байтам UTF-8 (json.dumps на CPython ещё и экранирует: 6 байт на символ).
    fits = proto.build_error("internal", "я" * 600)
    assert len(fits.encode("utf-8")) < proto.MAX_MESSAGE_BYTES

    with pytest.raises(ValueError):
        proto.build_error("internal", "я" * 2100)


def test_parse_rejects_oversized_text():
    raw = json.dumps({"v": 1, "t": "ping", "t_epoch": 1, "pad": "x" * 4096})
    with pytest.raises(ProtocolError):
        proto.parse_message(raw)


def test_parse_rejects_oversized_bytes():
    with pytest.raises(ProtocolError):
        proto.parse_message(b"x" * (proto.MAX_MESSAGE_BYTES + 1))


# --- примеры из Протокола §4 ---------------------------------------------

@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_protocol_examples(example):
    raw = json.dumps(example, ensure_ascii=False)
    assert len(raw.encode("utf-8")) <= proto.MAX_MESSAGE_BYTES

    assert proto.parse_message(raw) == example
    assert proto.parse_message(raw.encode("utf-8")) == example
    assert proto.parse_message(example) == example
    assert proto.parse_message(raw)["t"] == example["t"]


# --- разбор: отказы -------------------------------------------------------

def test_parse_rejects_bad_json():
    with pytest.raises(ProtocolError):
        proto.parse_message("{not json")


def test_parse_rejects_non_object():
    with pytest.raises(ProtocolError):
        proto.parse_message("[1, 2, 3]")


def test_parse_rejects_bad_utf8():
    with pytest.raises(ProtocolError):
        proto.parse_message(b'{"v": 1, "t": "\xff\xfe"}')


def test_parse_rejects_unsupported_input_type():
    with pytest.raises(ProtocolError):
        proto.parse_message(42)


@pytest.mark.parametrize("version", [2, 0, "1", None, True])
def test_parse_rejects_bad_version(version):
    with pytest.raises(ProtocolError):
        proto.parse_message({"v": version, "t": "ping", "t_epoch": 1})


def test_parse_rejects_missing_type():
    with pytest.raises(ProtocolError):
        proto.parse_message({"v": 1, "t_epoch": 1})


def test_parse_rejects_unknown_type():
    with pytest.raises(ProtocolError):
        proto.parse_message({"v": 1, "t": "timer_update", "t_epoch": 1})


@pytest.mark.parametrize(
    "msg",
    [
        {"v": 1, "t": "ack", "seq": 1, "ok": True},  # нет stage
        {"v": 1, "t": "ack", "seq": 1, "stage": "server"},  # нет ok
        {"v": 1, "t": "ack", "seq": 1, "ok": "true", "stage": "server"},
        {"v": 1, "t": "press", "seq": 0, "t_epoch": 1},
        {"v": 1, "t": "press", "seq": 1},
        {"v": 1, "t": "ping"},
        {"v": 1, "t": "ping", "t_epoch": -5},
        {"v": 1, "t": "error", "code": "auth_failed"},
        {"v": 1, "t": "hello", "role": "admin", "token": "x", "id": "a1b2c3d4"},
        {"v": 1, "t": "hello", "role": "device", "token": "", "id": "a1b2c3d4", "fw": 1},
    ],
)
def test_parse_rejects_bad_fields(msg):
    with pytest.raises(ProtocolError):
        proto.parse_message(msg)


@pytest.mark.parametrize(
    "peer",
    [
        None,
        {"id": "a1b2c3d4", "online": True},  # нет state
        {"id": "a1b2c3d4", "online": True, "state": "IDLE"},
        {"id": "A1B2C3D4", "online": True, "state": "ARMED"},
        {"id": "a1b2c3d4", "online": 1, "state": "ARMED"},
        {"id": "a1b2c3d4", "online": True, "state": "ARMED", "remaining_s": -1},
    ],
)
def test_parse_rejects_bad_peer(peer):
    msg = {"v": 1, "t": "hello_ok", "session": "s-1", "t_epoch": 1, "peer": peer}
    with pytest.raises(ProtocolError):
        proto.parse_message(msg)


@pytest.mark.parametrize(
    "status",
    [
        {"t_epoch": 1, "device": {"online": True}, "app": {"online": True}},
        {"t_epoch": 1, "device": {"online": True, "state": "ARMED"}},
        {"t_epoch": 1, "device": {"online": True, "state": "ARMED"}, "app": {}},
        {
            "t_epoch": 1,
            "device": {"online": True, "state": "ARMED", "fw": 1},
            "app": {"online": True},
        },
        {"t_epoch": "1", "device": {"online": True, "state": "ARMED"}, "app": {"online": True}},
    ],
)
def test_parse_rejects_bad_status(status):
    msg = dict(status)
    msg.update({"v": 1, "t": "status"})
    with pytest.raises(ProtocolError):
        proto.parse_message(msg)


@pytest.mark.parametrize("duration", [59, 86401, 10800.0, "10800"])
def test_parse_rejects_bad_duration(duration):
    msg = {"v": 1, "t": "timer", "seq": 3, "duration_s": duration, "t_epoch": 1}
    with pytest.raises(ProtocolError):
        proto.parse_message(msg)


@pytest.mark.parametrize("bad_from", ["a1b2c3d", "A1B2C3D4", 1234])
def test_parse_rejects_bad_from(bad_from):
    msg = {"v": 1, "t": "press", "seq": 7, "t_epoch": 1, "from": bad_from}
    with pytest.raises(ProtocolError):
        proto.parse_message(msg)


def test_error_code_enum_is_enforced():
    with pytest.raises(ProtocolError):
        proto.parse_message({"v": 1, "t": "error", "code": "oops", "msg": "x"})


def test_protocol_error_maps_to_close_4400():
    # ws_client на любую ProtocolError отвечает close 4400 (Протокол §5)
    assert issubclass(ProtocolError, ValueError)
    assert ProtocolError.code == "bad_request"
    try:
        proto.parse_message("{bad")
    except ProtocolError as exc:
        assert exc.code == "bad_request"
    else:  # pragma: no cover
        raise AssertionError("parse_message must raise on bad JSON")


# --- разбор: допустимое ---------------------------------------------------

def test_parse_tolerates_extra_fields():
    msg = {"v": 1, "t": "ping", "t_epoch": 1, "future_field": {"a": 1}}
    assert proto.parse_message(msg) == msg


def test_parse_accepts_minimal_status():
    msg = {"v": 1, "t": "status", "t_epoch": 1,
           "device": {"online": False, "state": "OFFLINE"}, "app": {"online": False}}
    assert proto.parse_message(msg)["device"]["state"] == "OFFLINE"


def test_parse_accepts_all_states():
    for state in proto.STATES:
        msg = {"v": 1, "t": "status", "t_epoch": 1,
               "device": {"online": True, "state": state}, "app": {"online": True}}
        assert proto.parse_message(msg)["device"]["state"] == state


def test_error_code_lists_match_protocol():
    assert proto.ERROR_CODES == (
        "auth_failed",
        "bad_request",
        "bad_duration",
        "no_peer",
        "duplicate",
        "internal",
    )
    assert proto.STATES == (
        "BOOT",
        "OFFLINE",
        "ARMED",
        "SENT",
        "WAIT_ETA",
        "COUNTDOWN",
        "ARRIVED",
        "NO_ACK",
    )


# --- композиция с frames.py ----------------------------------------------

def test_hello_travels_through_a_text_frame():
    raw = proto.build_hello("device", "tok", "a1b2c3d4", fw="1.0.0")
    wire = frames.build_frame(frames.OP_TEXT, raw.encode("utf-8"), b"\x01\x02\x03\x04")

    fin, opcode, payload, consumed = frames.parse_frame(bytearray(wire))
    assert (fin, opcode, consumed) == (True, frames.OP_TEXT, len(wire))

    kind, msg_opcode, assembled = frames.feed(frames.Fragments(), (fin, opcode, payload, consumed))
    assert (kind, msg_opcode) == (frames.KIND_MESSAGE, frames.OP_TEXT)
    assert proto.parse_message(assembled)["t"] == "hello"


def test_fragmented_server_message_reaches_parser():
    text = proto.build_pong(1793001600)
    half = len(text) // 2
    frags = frames.Fragments()
    # сервер кадры не маскирует -> mask_key=None
    assert frames.feed(frags, frames.parse_frame(
        bytearray(frames.build_frame(frames.OP_TEXT, text[:half].encode(), None, fin=False))
    )) is None
    out = frames.feed(frags, frames.parse_frame(
        bytearray(frames.build_frame(frames.OP_CONT, text[half:].encode(), None, fin=True))
    ))
    assert proto.parse_message(out[2])["t"] == "pong"


# --- требование §7: на плате нет pydantic ---------------------------------

def test_proto_module_imports_only_json():
    src = pathlib.Path(proto.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"json"}, f"proto.py должен импортировать только json, найдено: {imported!r}"
