"""Duplicate seq handling: in-memory, persisted, and client-restart epoch (§4.6)."""

from __future__ import annotations

from proto_helpers import (
    APP_ID,
    expect_no_frame,
    press_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect


async def test_duplicate_seq_is_ignored(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")

            await send_json(dev_ws, press_frame(7))
            await recv_until(dev_ws, "ack")
            forwarded = await recv_until(app_ws, "press")
            assert forwarded["seq"] == 7
            # Status(SENT) broadcast for the first press.
            await recv_until(app_ws, "status")

            await send_json(dev_ws, press_frame(7))
            error = await recv_until(dev_ws, "error")
            assert error["code"] == "duplicate"
            assert error["seq"] == 7
            await expect_no_frame(app_ws)


async def test_seq_one_restarts_client_epoch(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            # Use a high watermark first.
            await send_json(dev_ws, press_frame(5))
            await recv_until(dev_ws, "ack")
            await recv_until(app_ws, "press")

        # A rebooted button keeps no seq and starts at 1 again.
        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            await send_json(dev_ws, press_frame(1))
            server_ack = await recv_until(dev_ws, "ack")
            assert server_ack["seq"] == 1
            forwarded = await recv_until(app_ws, "press")
            assert forwarded["seq"] == 1


async def test_watermark_survives_server_restart(  # type: ignore[no-untyped-def]
    server_factory, tmp_path
) -> None:
    state_file = tmp_path / "restart-state.json"
    first = server_factory(state_file=state_file)
    async with connect(first.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await send_json(dev_ws, press_frame(5))
        await recv_until(dev_ws, "ack")
        # No app connected: the seq is still marked as processed.
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "no_peer"
    first.stop()

    second = server_factory(state_file=state_file)
    async with connect(second.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await send_json(dev_ws, press_frame(5))
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "duplicate"

        # seq=1 still works as a fresh client epoch after the restart.
        await send_json(dev_ws, press_frame(1))
        ack = await recv_until(dev_ws, "ack")
        assert ack["seq"] == 1
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "no_peer"
