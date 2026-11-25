"""Heartbeat: server ping every N s, 3 missed pongs → close + status (§4.8)."""

from __future__ import annotations

import asyncio
import json

from proto_helpers import (
    APP_ID,
    pong_frame,
    recv_frame,
    recv_until,
    send_hello,
)
from proto_helpers import (
    close_code as ws_close_code,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


async def test_three_missed_pongs_close_session(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(
        ping_interval_s=0.05,
        pong_timeout_s=0.05,
        max_ping_misses=3,
    )
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await recv_until(ws, "status")

        pings = 0
        close_value = None
        try:
            while True:
                frame = await recv_frame(ws, timeout=2.0)
                if frame["t"] == "ping":
                    pings += 1
        except ConnectionClosed as exc:
            close_value = ws_close_code(exc)
        assert pings >= 3
        assert close_value == 1000


async def _wait_device_offline(ws, timeout: float = 5.0) -> dict:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        assert remaining > 0, "device never went offline"
        frame = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        if frame["t"] == "ping":
            await ws.send(json.dumps(pong_frame(frame["t_epoch"])))
        elif frame["t"] == "status" and frame["device"]["online"] is False:
            return frame


async def test_dead_peer_reported_offline_to_app(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(ping_interval_s=0.05, pong_timeout_s=0.05)
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            # The device never answers protocol pings.

            status = await _wait_device_offline(app_ws)
            assert status["device"] == {
                "online": False,
                "state": "OFFLINE",
                "remaining_s": 0,
            }
            assert status["app"] == {"online": True}
            # The app itself must still be alive (it answered pings).
            await app_ws.send(json.dumps({"v": 1, "t": "ping", "t_epoch": 1_793_001_600}))
            pong = await recv_until(app_ws, "pong")
            assert pong["t_epoch"] == 1_793_001_600
