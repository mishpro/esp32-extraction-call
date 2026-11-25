"""Diagnostics: GET /healthz and GET /api/status (§8, step 1.8)."""

from __future__ import annotations

import httpx
from proto_helpers import recv_until, send_hello
from websockets.asyncio.client import connect


async def test_healthz(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{server.http_url}/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_api_status_empty(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{server.http_url}/api/status")
    assert response.status_code == 200
    assert response.json() == {
        "device": {"online": False, "state": "OFFLINE"},
        "app": {"online": False},
    }


async def test_api_status_with_device(server_factory) -> None:  # type: ignore[no-untyped-def]
    server = server_factory()
    async with connect(server.ws_url, ping_interval=None) as ws:
        await send_hello(ws)
        await recv_until(ws, "hello_ok")
        await recv_until(ws, "status")

        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server.http_url}/api/status")
        payload = response.json()
        assert payload["device"]["online"] is True
        assert payload["device"]["id"] == "a1b2c3d4"
        assert payload["device"]["state"] == "ARMED"
        assert payload["device"]["fw"] == "1.0.0"
        assert payload["device"]["session"].startswith("s-")
        assert payload["app"] == {"online": False}
