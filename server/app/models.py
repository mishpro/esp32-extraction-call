"""Pydantic models for every message type (Протокол §4, plan/schema/messages.schema.json).

The server is the only place where incoming frames are validated. All models
forbid extra fields, mirroring ``additionalProperties: false`` in the schema.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import CLOSE_PROTOCOL, ErrorCode, ProtocolError

HEX_ID_PATTERN = r"^[0-9a-f]{8}$"
NO_PEER_ID = "00000000"


class Role(StrEnum):
    DEVICE = "device"
    APP = "app"


class ButtonState(StrEnum):
    BOOT = "BOOT"
    OFFLINE = "OFFLINE"
    ARMED = "ARMED"
    SENT = "SENT"
    WAIT_ETA = "WAIT_ETA"
    COUNTDOWN = "COUNTDOWN"
    ARRIVED = "ARRIVED"
    NO_ACK = "NO_ACK"


class AckStage(StrEnum):
    SERVER = "server"
    DELIVERED = "delivered"


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Hello(Message):
    v: Literal[1]
    t: Literal["hello"]
    role: Role
    token: str = Field(min_length=1)
    id: str = Field(pattern=HEX_ID_PATTERN)
    fw: str | None = None


class PeerState(Message):
    id: str = Field(pattern=HEX_ID_PATTERN)
    online: bool
    state: ButtonState
    remaining_s: int = Field(default=0, ge=0)


class HelloOk(Message):
    v: Literal[1]
    t: Literal["hello_ok"]
    session: str
    t_epoch: int = Field(ge=0)
    peer: PeerState


class Press(Message):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    v: Literal[1]
    t: Literal["press"]
    seq: int = Field(ge=1)
    t_epoch: int = Field(ge=0)
    sender: str | None = Field(default=None, alias="from")


class Timer(Message):
    v: Literal[1]
    t: Literal["timer"]
    seq: int = Field(ge=1)
    duration_s: int = Field(ge=60, le=86400)
    t_epoch: int = Field(ge=0)
    deadline_t: int | None = Field(default=None, ge=0)


class Cancel(Message):
    v: Literal[1]
    t: Literal["cancel"]
    seq: int = Field(ge=1)
    t_epoch: int = Field(ge=0)


class RegisterPush(Message):
    """FCM token of an app installation (protocol v1.1 §4.10).

    Fire-and-forget: no ``seq`` and no ``ack``. The message is idempotent, so
    the app re-sends it on every connect and after every token rotation.
    """

    v: Literal[1]
    t: Literal["register_push"]
    token: str = Field(min_length=1, max_length=4096, pattern=r"^\S+$")
    t_epoch: int = Field(ge=0)


class Ack(Message):
    v: Literal[1]
    t: Literal["ack"]
    seq: int = Field(ge=1)
    ok: bool
    stage: AckStage


class DeviceStatus(Message):
    online: bool
    state: ButtonState
    remaining_s: int = Field(default=0, ge=0)
    fw: str | None = None


class AppStatus(Message):
    online: bool


class Status(Message):
    v: Literal[1]
    t: Literal["status"]
    t_epoch: int = Field(ge=0)
    device: DeviceStatus
    app: AppStatus


class Ping(Message):
    v: Literal[1]
    t: Literal["ping"]
    t_epoch: int = Field(ge=0)


class Pong(Message):
    v: Literal[1]
    t: Literal["pong"]
    t_epoch: int = Field(ge=0)


class ErrorMessage(Message):
    v: Literal[1]
    t: Literal["error"]
    code: ErrorCode
    msg: str
    seq: int | None = Field(default=None, ge=1)


INBOUND: dict[str, type[Message]] = {
    "hello": Hello,
    "press": Press,
    "timer": Timer,
    "cancel": Cancel,
    "register_push": RegisterPush,
    "ack": Ack,
    "ping": Ping,
    "pong": Pong,
}


def dump(model: Message) -> dict[str, Any]:
    """Serialise a model into a protocol frame (no nulls, aliases applied)."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def json_frame(frame: dict[str, Any]) -> str:
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def _safe_validation_message(exc: ValidationError) -> str:
    """Describe a validation failure without echoing input values.

    Pydantic includes ``input_value`` in its default string form; for `hello`
    that would print the token into the error frame and the log.
    """
    parts: list[str] = []
    for error in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        parts.append(f"{location}: {error['msg']}" if location else error["msg"])
    return "; ".join(parts) or "invalid message"


def _decode(raw: str | bytes) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST, "invalid json", close_code=CLOSE_PROTOCOL
        ) from exc
    if not isinstance(data, dict):
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "message must be a JSON object",
            close_code=CLOSE_PROTOCOL,
        )
    return data


def _check_version(data: dict[str, Any]) -> None:
    version = data.get("v")
    if type(version) is not int or version != 1:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "unsupported protocol version (expected v=1)",
            close_code=CLOSE_PROTOCOL,
        )


def _validate(model: type[Message], data: dict[str, Any]) -> Message:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            _safe_validation_message(exc),
            close_code=CLOSE_PROTOCOL,
        ) from exc


def _check_timer_duration(data: dict[str, Any]) -> None:
    """Out-of-range ``duration_s`` is a recoverable ``bad_duration`` (§4.4)."""
    if "duration_s" not in data:
        return
    duration = data["duration_s"]
    raw_seq = data.get("seq")
    seq = raw_seq if type(raw_seq) is int and raw_seq >= 1 else None
    if type(duration) is not int or not 60 <= duration <= 86400:
        raise ProtocolError(
            ErrorCode.BAD_DURATION,
            "duration_s out of range",
            seq=seq,
        )


def parse_client_message(raw: str | bytes) -> Message:
    """Parse a frame sent by a client (hello included)."""
    data = _decode(raw)
    _check_version(data)
    message_type = data.get("t")
    if not isinstance(message_type, str):
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "missing message type",
            close_code=CLOSE_PROTOCOL,
        )
    model = INBOUND.get(message_type)
    if model is None:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            f"unknown message type {message_type!r}",
            close_code=CLOSE_PROTOCOL,
        )
    if model is Timer:
        _check_timer_duration(data)
    return _validate(model, data)


def parse_hello(raw: str | bytes) -> Hello:
    data = _decode(raw)
    if data.get("t") != "hello":
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "hello must be the first message",
            close_code=CLOSE_PROTOCOL,
        )
    _check_version(data)
    return _validate(Hello, data)  # type: ignore[return-value]
