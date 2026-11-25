"""Protocol helpers shared by integration tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from websockets.exceptions import ConnectionClosed

DEVICE_ID = "a1b2c3d4"
APP_ID = "deadbeef"
TOKEN = "test-token"


def hello_frame(
    *,
    role: str = "device",
    id: str = DEVICE_ID,
    token: str = TOKEN,
    fw: str | None = "1.0.0",
    v: int = 1,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "v": v,
        "t": "hello",
        "role": role,
        "token": token,
        "id": id,
    }
    if fw is not None:
        frame["fw"] = fw
    return frame


async def send_hello(ws: Any, **kwargs: Any) -> None:
    await ws.send(json.dumps(hello_frame(**kwargs)))


async def send_json(ws: Any, frame: dict[str, Any]) -> None:
    await ws.send(json.dumps(frame))


async def recv_frame(ws: Any, timeout: float = 3.0) -> dict[str, Any]:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def recv_until(
    ws: Any,
    message_type: str,
    *,
    timeout: float = 3.0,
    limit: int = 50,
) -> dict[str, Any]:
    for _ in range(limit):
        frame = await recv_frame(ws, timeout)
        if frame.get("t") == message_type:
            return frame
    raise AssertionError(f"no {message_type!r} frame in {limit} frames")


async def expect_close(ws: Any, timeout: float = 3.0) -> tuple[int | None, str]:
    """Drain frames until the connection closes; return (code, reason)."""
    try:
        while True:
            await asyncio.wait_for(ws.recv(), timeout)
    except ConnectionClosed as exc:
        return close_code(exc), close_reason(exc)
    except TimeoutError:
        raise AssertionError("connection was not closed") from None


def close_code(exc: ConnectionClosed) -> int | None:
    frame = exc.rcvd or exc.sent
    return frame.code if frame is not None else None


def close_reason(exc: ConnectionClosed) -> str:
    frame = exc.rcvd or exc.sent
    return frame.reason if frame is not None else ""


async def expect_no_frame(ws: Any, timeout: float = 0.3) -> None:
    try:
        frame = await asyncio.wait_for(ws.recv(), timeout)
    except TimeoutError:
        return
    raise AssertionError(f"unexpected frame: {frame}")


def press_frame(seq: int, t_epoch: int = 1_793_001_234) -> dict[str, Any]:
    return {"v": 1, "t": "press", "seq": seq, "t_epoch": t_epoch}


def ack_frame(seq: int, *, ok: bool = True, stage: str = "delivered") -> dict[str, Any]:
    return {"v": 1, "t": "ack", "seq": seq, "ok": ok, "stage": stage}


def pong_frame(t_epoch: int) -> dict[str, Any]:
    return {"v": 1, "t": "pong", "t_epoch": t_epoch}
