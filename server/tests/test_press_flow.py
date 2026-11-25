"""press → ack(server) → deliver → ack(delivered) (§4.3, §4.6, step 1.7)."""

from __future__ import annotations

from proto_helpers import (
    APP_ID,
    DEVICE_ID,
    ack_frame,
    press_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect


async def test_press_delivered_flow(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        app_ok = await recv_until(app_ws, "hello_ok")
        assert app_ok["peer"]["online"] is False
        await recv_until(app_ws, "status")

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            dev_ok = await recv_until(dev_ws, "hello_ok")
            assert dev_ok["peer"] == {
                "id": APP_ID,
                "online": True,
                "state": "ARMED",
                "remaining_s": 0,
            }
            dev_status = await recv_until(dev_ws, "status")
            assert dev_status["device"]["online"] is True
            app_status = await recv_until(app_ws, "status")
            assert app_status["device"]["online"] is True

            await send_json(dev_ws, press_frame(7))

            server_ack = await recv_until(dev_ws, "ack")
            assert server_ack == {
                "v": 1,
                "t": "ack",
                "seq": 7,
                "ok": True,
                "stage": "server",
            }

            forwarded = await recv_until(app_ws, "press")
            assert forwarded["seq"] == 7
            assert forwarded["from"] == DEVICE_ID
            assert forwarded["t_epoch"] > 0

            sent_dev = await recv_until(dev_ws, "status")
            assert sent_dev["device"]["state"] == "SENT"
            sent_app = await recv_until(app_ws, "status")
            assert sent_app["device"]["state"] == "SENT"

            await send_json(app_ws, ack_frame(7))

            delivered_dev = await recv_until(dev_ws, "ack")
            assert delivered_dev["seq"] == 7
            assert delivered_dev["stage"] == "delivered"
            assert delivered_dev["ok"] is True
            delivered_app = await recv_until(app_ws, "ack")
            assert delivered_app == delivered_dev

            wait_dev = await recv_until(dev_ws, "status")
            assert wait_dev["device"]["state"] == "WAIT_ETA"
            wait_app = await recv_until(app_ws, "status")
            assert wait_app["device"]["state"] == "WAIT_ETA"


async def test_rejected_delivery_returns_to_armed(server_factory) -> None:  # type: ignore[no-untyped-def]
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

            await send_json(dev_ws, press_frame(9))
            await recv_until(dev_ws, "ack")
            await recv_until(app_ws, "press")
            await recv_until(dev_ws, "status")
            await recv_until(app_ws, "status")

            await send_json(app_ws, ack_frame(9, ok=False))
            delivered = await recv_until(dev_ws, "ack")
            assert delivered["ok"] is False
            status = await recv_until(dev_ws, "status")
            assert status["device"]["state"] == "ARMED"


async def test_press_marks_last_press_on_disk(server_factory, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    state_file = tmp_path / "state.json"
    server = server_factory(state_file=state_file)
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")
        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            await send_json(dev_ws, press_frame(11))
            await recv_until(dev_ws, "ack")
            await recv_until(app_ws, "press")

    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["last_press"]["seq"] == 11
    assert persisted["last_press"]["from"] == DEVICE_ID
    assert persisted["last_status"]["t"] == "status"
