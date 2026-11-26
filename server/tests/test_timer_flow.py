"""timer/cancel relay, deadline and remaining_s (§4.4–4.6, §8)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from proto_helpers import (
    APP_ID,
    ack_frame,
    expect_close,
    expect_no_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect


def timer_frame(seq: int, duration_s: int = 120, t_epoch: int = 1_793_001_300) -> dict:  # type: ignore[type-arg]
    return {
        "v": 1,
        "t": "timer",
        "seq": seq,
        "duration_s": duration_s,
        "t_epoch": t_epoch,
    }


def cancel_frame(seq: int, t_epoch: int = 1_793_001_400) -> dict:  # type: ignore[type-arg]
    return {"v": 1, "t": "cancel", "seq": seq, "t_epoch": t_epoch}


@asynccontextmanager
async def _pair(server):  # type: ignore[no-untyped-def]
    """App and device sockets, both past hello (one server, cross-session sends)."""
    async with (
        connect(server.ws_url, ping_interval=None) as app_ws,
        connect(server.ws_url, ping_interval=None) as dev_ws,
    ):
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await recv_until(dev_ws, "status")
        await recv_until(app_ws, "status")
        yield app_ws, dev_ws


async def test_timer_forwarded_with_deadline_and_acked(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with _pair(server) as (app_ws, dev_ws):
        await send_json(app_ws, timer_frame(3, duration_s=120))

        ack = await recv_until(app_ws, "ack")
        assert ack == {"v": 1, "t": "ack", "seq": 3, "ok": True, "stage": "server"}

        forwarded = await recv_until(dev_ws, "timer")
        assert forwarded["seq"] == 3
        assert forwarded["duration_s"] == 120
        assert forwarded["deadline_t"] == forwarded["t_epoch"] + 120

        # The countdown only becomes real after the button confirms receipt.
        await send_json(dev_ws, ack_frame(3))

        delivered = await recv_until(app_ws, "ack")
        assert delivered["seq"] == 3
        assert delivered["stage"] == "delivered"
        assert delivered["ok"] is True

        status = await recv_until(app_ws, "status")
        assert status["device"]["state"] == "COUNTDOWN"
        assert status["device"]["remaining_s"] in (119, 120)
        await recv_until(dev_ws, "status")


async def test_cancel_returns_to_armed_and_clears_timer(server_factory, tmp_path) -> None:  # type: ignore[no-untyped-def]
    state_file = tmp_path / "state.json"
    server = server_factory(state_file=state_file)
    async with _pair(server) as (app_ws, dev_ws):
        await send_json(app_ws, timer_frame(3))
        await recv_until(app_ws, "ack")
        await recv_until(dev_ws, "timer")
        await send_json(dev_ws, ack_frame(3))
        await recv_until(app_ws, "ack")
        await recv_until(app_ws, "status")
        assert json.loads(state_file.read_text())["last_timer"]["seq"] == 3

        await send_json(app_ws, cancel_frame(4))
        ack = await recv_until(app_ws, "ack")
        assert ack == {"v": 1, "t": "ack", "seq": 4, "ok": True, "stage": "server"}

        cancel = await recv_until(dev_ws, "cancel")
        assert cancel["seq"] == 4
        await send_json(dev_ws, ack_frame(4))

        delivered = await recv_until(app_ws, "ack")
        assert delivered["seq"] == 4
        assert delivered["stage"] == "delivered"

        status = await recv_until(app_ws, "status")
        assert status["device"]["state"] == "ARMED"
        assert status["device"]["remaining_s"] == 0
        assert json.loads(state_file.read_text())["last_timer"] is None


async def test_timer_without_device_reports_no_peer(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        await send_json(app_ws, timer_frame(5))
        ack = await recv_until(app_ws, "ack")
        assert ack["stage"] == "server"
        error = await recv_until(app_ws, "error")
        assert error["code"] == "no_peer"
        assert error["seq"] == 5

        # The session stays usable; the retry needs a new seq (§4.6).
        await send_json(app_ws, timer_frame(6))
        ack = await recv_until(app_ws, "ack")
        assert ack["seq"] == 6


async def test_duplicate_timer_seq_is_dropped(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with _pair(server) as (app_ws, dev_ws):
        await send_json(app_ws, timer_frame(3))
        await recv_until(app_ws, "ack")
        await recv_until(dev_ws, "timer")

        await send_json(app_ws, timer_frame(3))
        error = await recv_until(app_ws, "error")
        assert error["code"] == "duplicate"
        assert error["seq"] == 3
        await expect_no_frame(dev_ws)


async def test_countdown_restored_when_device_reconnects(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            await recv_until(dev_ws, "status")
            await recv_until(app_ws, "status")

            await send_json(app_ws, timer_frame(3, duration_s=600))
            await recv_until(app_ws, "ack")
            await recv_until(dev_ws, "timer")
            await send_json(dev_ws, ack_frame(3))
            await recv_until(app_ws, "ack")
            await recv_until(app_ws, "status")

        # The device is gone but the countdown continues; a reconnect must show it.
        status = await recv_until(app_ws, "status")
        assert status["device"]["online"] is False

        async with connect(server.ws_url, ping_interval=None) as again:
            await send_hello(again)
            await recv_until(again, "hello_ok")
            status = await recv_until(app_ws, "status")
            assert status["device"]["online"] is True
            assert status["device"]["state"] == "COUNTDOWN"
            assert status["device"]["remaining_s"] > 590


async def test_timer_from_device_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await send_json(dev_ws, timer_frame(1))
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(dev_ws)
        assert code == 4400


async def test_deadline_moves_status_to_arrived(server_factory) -> None:  # type: ignore[no-untyped-def]
    """The button flips locally; the server must broadcast ARRIVED itself (§4.7)."""
    server = server_factory()
    router = server.app.state.router
    async with _pair(server) as (app_ws, dev_ws):
        await send_json(app_ws, timer_frame(3, duration_s=60))
        await recv_until(app_ws, "ack")
        await recv_until(dev_ws, "timer")
        await send_json(dev_ws, ack_frame(3))
        await recv_until(app_ws, "ack")
        await recv_until(app_ws, "status")

        device = router.registry.latest_device()
        assert device is not None
        router._cancel_timer_task(device.id)
        device.deadline_t = 1  # already in the past
        await router._expire_timer(device, 1)

        status = await recv_until(app_ws, "status")
        assert status["device"]["state"] == "ARRIVED"
        assert status["device"]["remaining_s"] == 0
        # Arrival is not a cancel: §8 keeps last_timer until the next press.
        assert router.state.last_timer is not None


async def test_timer_and_cancel_manage_the_expiry_task(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    router = server.app.state.router
    async with _pair(server) as (app_ws, dev_ws):
        await send_json(app_ws, timer_frame(3, duration_s=60))
        await recv_until(app_ws, "ack")
        await recv_until(dev_ws, "timer")
        await send_json(dev_ws, ack_frame(3))
        await recv_until(app_ws, "ack")
        await recv_until(app_ws, "status")
        assert router._timer_tasks, "timer must schedule an ARRIVED broadcast"

        await send_json(app_ws, cancel_frame(4))
        await recv_until(app_ws, "ack")
        await recv_until(dev_ws, "cancel")
        await send_json(dev_ws, ack_frame(4))
        await recv_until(app_ws, "ack")
        await recv_until(app_ws, "status")
        assert router._timer_tasks == {}, "cancel must drop the expiry task"
