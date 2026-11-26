"""FCM registration and press delivery (protocol v1.1 §4.10, S6)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from proto_helpers import (
    APP_ID,
    DEVICE_ID,
    ack_frame,
    expect_close,
    press_frame,
    recv_until,
    send_hello,
    send_json,
)
from websockets.asyncio.client import connect

from app.push import FcmPushSender, PushResult

TOKEN = "fcm-token-1"


class FakePush:
    """Records sends; returns a fixed result (ok by default)."""

    def __init__(self, result: PushResult | None = None) -> None:
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.result = result or PushResult(ok=True)
        self.closed = False

    async def send(self, token: str, data: dict[str, str]) -> PushResult:
        self.sent.append((token, data))
        return self.result

    async def close(self) -> None:
        self.closed = True


def register_frame(token: str = TOKEN) -> dict[str, Any]:
    return {"v": 1, "t": "register_push", "token": token, "t_epoch": 1_793_001_700}


async def wait_for(predicate, timeout: float = 3.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.02)


def stored_push(server) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    data = json.loads(Path(server.settings.state_file).read_text(encoding="utf-8"))
    return data.get("push")


async def register(ws) -> None:  # type: ignore[no-untyped-def]
    await send_json(ws, register_frame())


async def test_register_push_is_persisted(server_factory) -> None:  # type: ignore[no-untyped-def]
    fake = FakePush()
    server = server_factory(push=fake)
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")

        await register(app_ws)
        await wait_for(lambda: stored_push(server) is not None)

    assert stored_push(server) == {"app_id": APP_ID, "token": TOKEN}
    assert fake.sent == []


async def test_press_is_pushed_when_app_is_offline(server_factory) -> None:  # type: ignore[no-untyped-def]
    fake = FakePush()
    server = server_factory(push=fake)

    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")
        await register(app_ws)
        await wait_for(lambda: stored_push(server) is not None)

    # The app is gone: the call must still reach it through FCM.
    async with connect(server.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await recv_until(dev_ws, "status")
        await send_json(dev_ws, press_frame(5))

        ack = await recv_until(dev_ws, "ack")
        assert ack == {"v": 1, "t": "ack", "seq": 5, "ok": True, "stage": "server"}
        await wait_for(lambda: len(fake.sent) == 1)

        token, data = fake.sent[0]
        assert token == TOKEN
        assert data["v"] == "1"
        assert data["t"] == "press"
        assert data["seq"] == "5"
        assert data["device_id"] == DEVICE_ID
        assert int(data["t_epoch"]) > 0

        # No error(no_peer): the server treats the call as delivered to FCM.
        status = await recv_until(dev_ws, "status")
        assert status["device"]["state"] == "SENT"

        # The app comes back and acks while the button is still online: the
        # pending press is remembered, so "delivered" reaches the button.
        async with connect(server.ws_url, ping_interval=None) as app_ws:
            await send_hello(app_ws, role="app", id=APP_ID, fw=None)
            await recv_until(app_ws, "hello_ok")
            await recv_until(app_ws, "status")
            await send_json(app_ws, ack_frame(5))
            ack_app = await recv_until(app_ws, "ack")
            assert ack_app["stage"] == "delivered"

            ack_dev = await recv_until(dev_ws, "ack")
            assert ack_dev["stage"] == "delivered"
            assert ack_dev["seq"] == 5


async def test_press_without_push_and_offline_app_is_no_peer(server_factory) -> None:  # type: ignore[no-untyped-def]
    fake = FakePush()
    server = server_factory(push=fake)
    async with connect(server.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await recv_until(dev_ws, "status")
        await send_json(dev_ws, press_frame(1))
        await recv_until(dev_ws, "ack")
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "no_peer"
        await asyncio.sleep(0.1)
        assert fake.sent == []


async def test_press_is_pushed_even_when_app_is_online(server_factory) -> None:  # type: ignore[no-untyped-def]
    """At-least-once: WS and FCM in parallel, the app dedups (§4.10)."""
    fake = FakePush()
    server = server_factory(push=fake)
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")
        await register(app_ws)

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            await recv_until(dev_ws, "status")
            app_status = await recv_until(app_ws, "status")
            assert app_status["device"]["online"] is True

            await send_json(dev_ws, press_frame(9))
            await recv_until(dev_ws, "ack")
            forwarded = await recv_until(app_ws, "press")
            assert forwarded["seq"] == 9
            await wait_for(lambda: len(fake.sent) == 1)
            assert fake.sent[0][1]["seq"] == "9"


async def test_unregistered_token_is_dropped(server_factory) -> None:  # type: ignore[no-untyped-def]
    fake = FakePush(PushResult(ok=False, unregistered=True, error="UNREGISTERED"))
    server = server_factory(push=fake)
    async with connect(server.ws_url, ping_interval=None) as app_ws:
        await send_hello(app_ws, role="app", id=APP_ID, fw=None)
        await recv_until(app_ws, "hello_ok")
        await recv_until(app_ws, "status")
        await register(app_ws)
        await wait_for(lambda: stored_push(server) is not None)

        async with connect(server.ws_url, ping_interval=None) as dev_ws:
            await send_hello(dev_ws)
            await recv_until(dev_ws, "hello_ok")
            await recv_until(dev_ws, "status")
            await send_json(dev_ws, press_frame(3))
            await recv_until(dev_ws, "ack")
            await wait_for(lambda: stored_push(server) is None)

    # The press itself was still accepted: the button saw ack(server).
    assert len(fake.sent) == 1


async def test_register_push_from_device_is_rejected(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory(push=FakePush())
    async with connect(server.ws_url, ping_interval=None) as dev_ws:
        await send_hello(dev_ws)
        await recv_until(dev_ws, "hello_ok")
        await send_json(dev_ws, register_frame())
        error = await recv_until(dev_ws, "error")
        assert error["code"] == "bad_request"
        code, _ = await expect_close(dev_ws)
        assert code == 4400


# -- FcmPushSender against a mock HTTP transport ----------------------------


@pytest.fixture
def credentials(tmp_path: Path) -> Path:
    path = tmp_path / "service-account.json"
    path.write_text(json.dumps({"project_id": "demo-project"}), encoding="utf-8")
    return path


def _sender(credentials: Path, handler) -> FcmPushSender:  # type: ignore[no-untyped-def]
    sender = FcmPushSender(credentials, transport=httpx.MockTransport(handler))

    async def fake_access_token() -> str:
        return "access-token"

    sender._access_token = fake_access_token  # type: ignore[method-assign]
    return sender


async def test_fcm_send_builds_data_message(credentials: Path) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"name": "projects/demo/messages/1"})

    sender = _sender(credentials, handler)
    result = await sender.send(TOKEN, {"t": "press", "seq": 1, "v": 1})
    await sender.close()

    assert result.ok is True
    request = captured[0]
    assert str(request.url) == "https://fcm.googleapis.com/v1/projects/demo-project/messages:send"
    assert request.headers["Authorization"] == "Bearer access-token"
    body = json.loads(request.content)
    assert body["message"]["token"] == TOKEN
    assert body["message"]["data"] == {"t": "press", "seq": "1", "v": "1"}
    assert body["message"]["android"]["priority"] == "high"


async def test_fcm_404_means_unregistered(credentials: Path) -> None:
    sender = _sender(credentials, lambda request: httpx.Response(404, json={"error": {}}))
    result = await sender.send(TOKEN, {"t": "press"})
    await sender.close()
    assert result.ok is False
    assert result.unregistered is True


async def test_fcm_error_is_reported_without_token(credentials: Path) -> None:
    sender = _sender(
        credentials,
        lambda request: httpx.Response(500, text="internal"),
    )
    result = await sender.send(TOKEN, {"t": "press"})
    await sender.close()
    assert result.ok is False
    assert result.unregistered is False
    assert TOKEN not in (result.error or "")
    assert "500" in (result.error or "")
