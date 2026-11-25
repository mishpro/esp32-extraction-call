"""Protocol error codes and WebSocket close codes (Протокол §4.9, §5)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

CLOSE_NORMAL = 1000
CLOSE_PROTOCOL = 4400
CLOSE_AUTH = 4401
CLOSE_INTERNAL = 1011


class ErrorCode(StrEnum):
    AUTH_FAILED = "auth_failed"
    BAD_REQUEST = "bad_request"
    BAD_DURATION = "bad_duration"
    NO_PEER = "no_peer"
    DUPLICATE = "duplicate"
    INTERNAL = "internal"


class ProtocolError(Exception):
    """A client error that is answered with an ``error`` frame.

    ``close_code`` is ``None`` for recoverable errors: the frame is sent and the
    session stays open (``no_peer``, ``bad_duration``, ``duplicate``).
    """

    def __init__(
        self,
        code: ErrorCode,
        msg: str,
        *,
        seq: int | None = None,
        close_code: int | None = None,
    ) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.seq = seq
        self.close_code = close_code

    def frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "v": 1,
            "t": "error",
            "code": self.code.value,
            "msg": self.msg,
        }
        if self.seq is not None:
            frame["seq"] = self.seq
        return frame
