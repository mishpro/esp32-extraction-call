"""Error codes and close codes: bad_duration, no_peer, 4400/4401/1011 (§4.9, §5)."""

from __future__ import annotations

import json

from proto_helpers import (
    APP_ID,
    expect_close,
    hello_frame,
    press_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect


async def test_no_peer_keeps_connection(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")

        await send_json(ws, press_frame(1))
        ack = await recv_until(ws, "ack")
        assert ack["stage"] == "server"
        error = await recv_until(ws, "error")
        assert error["code"] == "no_peer"
        assert error["seq"] == 1

        # Session is still usable: a second press is accepted.
        await send_json(ws, press_frame(2))
        ack = await recv_until(ws, "ack")
        assert ack["seq"] == 2


async def test_bad_duration_keeps_connection(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws, role="app", id=APP_ID, fw=None)
        await recv_until(ws, "hello_ok")

        await send_json(
            ws,
            {"v": 1, "t": "timer", "seq": 3, "duration_s": 10, "t_epoch": 1},
        )
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_duration"
        assert error["seq"] == 3

        await ws.send(json.dumps({"v": 1, "t": "ping", "t_epoch": 5}))
        pong = await recv_until(ws, "pong")
        assert pong["t_epoch"] == 5


async def test_cancel_from_device_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")

        await send_json(ws, {"v": 1, "t": "cancel", "seq": 4, "t_epoch": 1})
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_binary_frame_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await ws.send(b"\x00\x01\x02")
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_press_from_app_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws, role="app", id=APP_ID, fw=None)
        await recv_until(ws, "hello_ok")
        await send_json(ws, press_frame(1))
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_internal_error_closes_1011(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()

    async def boom(session, message) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    server.app.state.router.dispatch = boom
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await send_json(ws, press_frame(1))
        error = await recv_until(ws, "error")
        assert error["code"] == "internal"
        code, _ = await expect_close(ws)
        assert code == 1011


async def test_hello_error_frames_do_not_echo_token(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    secret = "do-not-echo-this-token"
    frame = hello_frame(role="tablet", token=secret)
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_json(ws, frame)
        error = await recv_until(ws, "error")
        assert secret not in json.dumps(error)
