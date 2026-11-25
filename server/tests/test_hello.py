"""Hello handshake, timeouts, protocol envelope (§2, §4.1, §5)."""

from __future__ import annotations

import hashlib
import json

import httpx
from proto_helpers import (
    DEVICE_ID,
    expect_close,
    hello_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect


async def test_hello_ok_and_status(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        hello_ok = await recv_until(ws, "hello_ok")
        assert hello_ok["v"] == 1
        assert hello_ok["session"].startswith("s-")
        assert isinstance(hello_ok["t_epoch"], int)
        assert hello_ok["peer"] == {
            "id": "00000000",
            "online": False,
            "state": "OFFLINE",
            "remaining_s": 0,
        }

        status = await recv_until(ws, "status")
        assert status["device"] == {
            "online": True,
            "state": "ARMED",
            "remaining_s": 0,
            "fw": "1.0.0",
        }
        assert status["app"] == {"online": False}


async def test_hello_timeout_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(hello_timeout_s=0.15)
    async with connect(server.ws_url, ping_interval=None) as ws:
        code, reason = await expect_close(ws, timeout=3.0)
        assert code == 4400
        assert "hello" in reason


async def test_premature_press_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_json(ws, {"v": 1, "t": "press", "seq": 1, "t_epoch": 1})
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_repeated_hello_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await recv_until(ws, "status")
        await send_hello(ws)
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_bad_role_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_json(ws, hello_frame(role="tablet"))
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_missing_id_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        frame = hello_frame()
        del frame["id"]
        await send_json(ws, frame)
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_version_2_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_json(ws, hello_frame(v=2))
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_oversized_frame_closes_4400(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await ws.send("x" * 5000)
        error = await recv_until(ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(ws)
        assert code == 4400


async def test_auth_required_accepts_known_token(server_factory) -> None:  # type: ignore[no-untyped-def]
    digest = hashlib.sha256(b"dev-secret").hexdigest()
    server = server_factory(auth_required=True, device_token_sha256=digest)
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws, token="dev-secret")
        hello_ok = await recv_until(ws, "hello_ok")
        assert hello_ok["peer"]["online"] is False


async def test_auth_required_rejects_bad_token_4401(server_factory) -> None:  # type: ignore[no-untyped-def]
    digest = hashlib.sha256(b"dev-secret").hexdigest()
    server = server_factory(auth_required=True, device_token_sha256=digest)
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws, token="wrong")
        error = await recv_until(ws, "error")
        assert error["code"] == "auth_failed"
        code, _ = await expect_close(ws)
        assert code == 4401


async def test_auth_fails_closed_without_configured_hash(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(auth_required=True)
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        error = await recv_until(ws, "error")
        assert error["code"] == "auth_failed"
        code, _ = await expect_close(ws)
        assert code == 4401


async def test_reconnect_with_same_id_replaces_old_session(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as first:
        await send_hello(first)
        await recv_until(first, "hello_ok")
        await recv_until(first, "status")

        async with connect(server.ws_url, ping_interval=None) as second:
            await send_hello(second)
            second_ok = await recv_until(second, "hello_ok")
            assert second_ok["peer"]["online"] is False
            await recv_until(second, "status")

            code, reason = await expect_close(first)
            assert code == 1000
            assert "replaced" in reason

            # The old session's cleanup must not evict the replacement.
            api = await _api_status(server)
            assert api["device"]["online"] is True
            assert api["device"]["id"] == DEVICE_ID


async def _api_status(server) -> dict:  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{server.http_url}/api/status")
        response.raise_for_status()
        return json.loads(response.text)


async def test_hello_ok_peer_reports_connected_app(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id="deadbeef", fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        async with connect(server.ws_url, ping_interval=None) as device_ws:
            await send_hello(device_ws)
            hello_ok = await recv_until(device_ws, "hello_ok")
            assert hello_ok["peer"] == {
                "id": "deadbeef",
                "online": True,
                "state": "ARMED",
                "remaining_s": 0,
            }
