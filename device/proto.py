"""Nightcall protocol messages (JSON) — assembly and parsing.

Mirrors plan/schema/messages.schema.json (which mirrors drafts/Протокол.md
§4).  Pure Python: only `json` is imported, so the file runs both on CPython
3.12 (pytest) and on MicroPython 1.29 on the ESP32.

Design decisions:
  * builders return *str* (JSON text); ws_client encodes it to UTF-8 and puts
    it into a text frame.  The 4096-byte transport limit (Протокол §1) is
    checked on the encoded form, both on build and on parse;
  * the parser validates required fields and their types/enums by hand —
    pydantic is not available on the board and the full JSON Schema is not
    shipped to it (plan/schema/README §"Как использовать в коде");
  * unknown extra fields are *tolerated* on parse (the device must not break
    when the server grows a field), while the builders only emit schema
    fields.  On the server side `additionalProperties: false` still applies;
  * `json.dumps` is called without keyword arguments on purpose: MicroPython
    accepts only the single positional argument.

Type hints are absent for the same reason as in frames.py (PEP 604 unions
are evaluated at import time).
"""

import json

VERSION = 1
MAX_MESSAGE_BYTES = 4096  # Протокол §1 / FR-S-12

ROLES = ("device", "app")
STAGES = ("server", "delivered")
STATES = (
    "BOOT",
    "OFFLINE",
    "ARMED",
    "SENT",
    "WAIT_ETA",
    "COUNTDOWN",
    "ARRIVED",
    "NO_ACK",
)
ERROR_CODES = (
    "auth_failed",
    "bad_request",
    "bad_duration",
    "no_peer",
    "duplicate",
    "internal",
)
TYPES = (
    "hello",
    "hello_ok",
    "press",
    "timer",
    "cancel",
    "ack",
    "status",
    "ping",
    "pong",
    "error",
)

HEX_DIGITS = "0123456789abcdef"
DURATION_MIN = 60
DURATION_MAX = 86400


class ProtocolError(ValueError):
    """Malformed or out-of-contract message.

    `code` is the protocol error code the peer should be told about
    (`bad_request` by default -> WS close 4400).  Kept as a class attribute:
    overriding __init__ of a built-in exception is not portable to
    MicroPython, and the device only ever needs the default anyway.
    """

    code = "bad_request"


# --- small validators ------------------------------------------------------

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _need_str(value, path):
    if not isinstance(value, str):
        raise ProtocolError(
            f"{path}: expected string, got {type(value).__name__}"
        )
    return value


def _need_int(value, path, minimum=None, maximum=None):
    if not _is_int(value):
        raise ProtocolError(
            f"{path}: expected integer, got {type(value).__name__}"
        )
    if minimum is not None and value < minimum:
        raise ProtocolError(f"{path}: {value} < {minimum}")
    if maximum is not None and value > maximum:
        raise ProtocolError(f"{path}: {value} > {maximum}")
    return value


def _need_bool(value, path):
    if not isinstance(value, bool):
        raise ProtocolError(
            f"{path}: expected boolean, got {type(value).__name__}"
        )
    return value


def _need_hexid(value, path):
    _need_str(value, path)
    if len(value) != 8:
        raise ProtocolError(f"{path}: id must be 8 hex chars, got {value!r}")
    for ch in value:
        if ch not in HEX_DIGITS:
            raise ProtocolError(f"{path}: id must be lowercase hex, got {value!r}")
    return value


def _need_enum(value, path, allowed):
    _need_str(value, path)
    if value not in allowed:
        raise ProtocolError(f"{path}: {value!r} not in {allowed!r}")
    return value


# --- building --------------------------------------------------------------

def _dump(msg):
    """Serialize to JSON text and enforce the 4096-byte limit (UTF-8)."""
    raw = json.dumps(msg)
    size = len(raw.encode("utf-8"))
    if size > MAX_MESSAGE_BYTES:
        raise ValueError(f"message is {size} bytes, limit is {MAX_MESSAGE_BYTES}")
    return raw


def build_hello(role, token, id, fw=None):
    """hello — client -> server, first frame after Upgrade (Протокол §4.1).

    role: "device" | "app"; token: secret string (empty is tolerated while
    AUTH_REQUIRED is off in the MVP); id: 8 lowercase hex; fw: firmware
    version, device only.
    """
    _need_enum(role, "hello.role", ROLES)
    _need_str(token, "hello.token")
    _need_hexid(id, "hello.id")

    msg = {"v": VERSION, "t": "hello", "role": role, "token": token, "id": id}
    if fw is not None:
        if role != "device":
            raise ValueError("hello.fw is only sent by role=device")
        _need_str(fw, "hello.fw")
        msg["fw"] = fw
    return _dump(msg)


def build_press(seq, t_epoch):
    """press — button -> server (Протокол §4.3).

    seq: monotonic counter from 1; t_epoch: send time on the server scale.
    """
    _need_int(seq, "press.seq", 1)
    _need_int(t_epoch, "press.t_epoch", 0)
    return _dump({"v": VERSION, "t": "press", "seq": seq, "t_epoch": t_epoch})


def build_ping(t_epoch):
    """ping — device heartbeat every 20 s (Протокол §4.8)."""
    _need_int(t_epoch, "ping.t_epoch", 0)
    return _dump({"v": VERSION, "t": "ping", "t_epoch": t_epoch})


def build_pong(t_epoch):
    """pong — answer to the server ping (Протокол §4.8)."""
    _need_int(t_epoch, "pong.t_epoch", 0)
    return _dump({"v": VERSION, "t": "pong", "t_epoch": t_epoch})


def build_ack(seq, ok, stage):
    """ack — both directions (Протокол §4.6).

    seq: acknowledged seq; ok: False means rejected; stage: "server" |
    "delivered".
    """
    _need_int(seq, "ack.seq", 1)
    _need_bool(ok, "ack.ok")
    _need_enum(stage, "ack.stage", STAGES)
    return _dump({"v": VERSION, "t": "ack", "seq": seq, "ok": ok, "stage": stage})


def build_error(code, msg, seq=None):
    """error — server -> client (Протокол §4.9).

    code: one of ERROR_CODES; msg: human-readable text; seq: related seq,
    omitted when None.
    """
    _need_enum(code, "error.code", ERROR_CODES)
    _need_str(msg, "error.msg")
    out = {"v": VERSION, "t": "error", "code": code, "msg": msg}
    if seq is not None:
        _need_int(seq, "error.seq", 1)
        out["seq"] = seq
    return _dump(out)


# --- parsing ---------------------------------------------------------------

def _check_peer(value, path):
    if not isinstance(value, dict):
        raise ProtocolError(f"{path}: expected object")
    for key in ("id", "online", "state"):
        if key not in value:
            raise ProtocolError(f"{path}: missing {key}")
    _need_hexid(value["id"], path + ".id")
    _need_bool(value["online"], path + ".online")
    _need_enum(value["state"], path + ".state", STATES)
    if "remaining_s" in value:
        _need_int(value["remaining_s"], path + ".remaining_s", 0)


def _check_device(value, path):
    if not isinstance(value, dict):
        raise ProtocolError(f"{path}: expected object")
    for key in ("online", "state"):
        if key not in value:
            raise ProtocolError(f"{path}: missing {key}")
    _need_bool(value["online"], path + ".online")
    _need_enum(value["state"], path + ".state", STATES)
    if "remaining_s" in value:
        _need_int(value["remaining_s"], path + ".remaining_s", 0)
    if "fw" in value:
        _need_str(value["fw"], path + ".fw")


def _check_app(value, path):
    if not isinstance(value, dict):
        raise ProtocolError(f"{path}: expected object")
    if "online" not in value:
        raise ProtocolError(f"{path}: missing online")
    _need_bool(value["online"], path + ".online")


# name, kind, optional
_SPECS = {
    "hello": (
        ("role", "role", False),
        ("token", "str", False),
        ("id", "hexid", False),
        ("fw", "str", True),
    ),
    "hello_ok": (
        ("session", "str", False),
        ("t_epoch", "epoch", False),
        ("peer", "peer", False),
    ),
    "press": (
        ("seq", "seq", False),
        ("t_epoch", "epoch", False),
        ("from", "hexid", True),
    ),
    "timer": (
        ("seq", "seq", False),
        ("duration_s", "duration", False),
        ("t_epoch", "epoch", False),
        ("deadline_t", "epoch", True),
    ),
    "cancel": (
        ("seq", "seq", False),
        ("t_epoch", "epoch", False),
    ),
    "ack": (
        ("seq", "seq", False),
        ("ok", "bool", False),
        ("stage", "stage", False),
    ),
    "status": (
        ("t_epoch", "epoch", False),
        ("device", "device", False),
        ("app", "app", False),
    ),
    "ping": (("t_epoch", "epoch", False),),
    "pong": (("t_epoch", "epoch", False),),
    "error": (
        ("code", "code", False),
        ("msg", "str", False),
        ("seq", "seq", True),
    ),
}


def _check_field(value, kind, path):
    if kind == "str":
        _need_str(value, path)
    elif kind == "bool":
        _need_bool(value, path)
    elif kind == "hexid":
        _need_hexid(value, path)
    elif kind == "epoch":
        _need_int(value, path, 0)
    elif kind == "seq":
        _need_int(value, path, 1)
    elif kind == "duration":
        _need_int(value, path, DURATION_MIN, DURATION_MAX)
    elif kind == "role":
        _need_enum(value, path, ROLES)
    elif kind == "stage":
        _need_enum(value, path, STAGES)
    elif kind == "code":
        _need_enum(value, path, ERROR_CODES)
    elif kind == "peer":
        _check_peer(value, path)
    elif kind == "device":
        _check_device(value, path)
    elif kind == "app":
        _check_app(value, path)
    else:  # pragma: no cover - guarded by _SPECS
        raise ProtocolError(f"{path}: unknown kind {kind!r}")


def parse_message(raw):
    """Parse and validate one protocol message.

    raw: JSON text (str), UTF-8 bytes, or an already decoded dict.

    Returns the message dict; `msg["t"]` is always a known type.  Extra
    fields are kept as they came.  Raises ProtocolError (a ValueError) for
    bad JSON, too large input, a missing/unknown `t`, v != 1, or any missing
    or mistyped field of that type.
    """
    if isinstance(raw, (bytes, bytearray)):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                f"message is {len(raw)} bytes, limit is {MAX_MESSAGE_BYTES}"
            )
        try:
            raw = raw.decode("utf-8")
        except UnicodeError:
            raise ProtocolError("payload is not valid UTF-8")

    if isinstance(raw, str):
        try:
            size = len(raw.encode("utf-8"))
        except UnicodeError:
            raise ProtocolError("payload is not valid UTF-8")
        if size > MAX_MESSAGE_BYTES:
            raise ProtocolError(
                f"message is {size} bytes, limit is {MAX_MESSAGE_BYTES}"
            )
        try:
            msg = json.loads(raw)
        except ValueError:
            # CPython: JSONDecodeError; MicroPython: ValueError.
            raise ProtocolError("payload is not valid JSON")
    elif isinstance(raw, dict):
        msg = raw
    else:
        raise ProtocolError(
            f"expected str, bytes or dict, got {type(raw).__name__}"
        )

    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")

    v = msg.get("v")
    if not _is_int(v) or v != VERSION:
        raise ProtocolError(f"unsupported protocol version: {v!r}")

    t = msg.get("t")
    if not isinstance(t, str) or t not in TYPES:
        raise ProtocolError(f"unknown message type: {t!r}")

    for name, kind, optional in _SPECS[t]:
        if name not in msg:
            if optional:
                continue
            raise ProtocolError(f"{t}: missing field {name!r}")
        _check_field(msg[name], kind, t + "." + name)

    return msg
