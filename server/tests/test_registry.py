"""Registry: devices/apps dicts, last_seen, replacement (§8, step 1.3)."""

from __future__ import annotations

from typing import Any

from app.models import ButtonState
from app.registry import AppSession, DeviceSession, Registry


class DummyWS:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.close_calls: list[tuple[int, str]] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))


def make_device(device_id: str = "a1b2c3d4", fw: str | None = "1.0.0") -> DeviceSession:
    return DeviceSession(id=device_id, ws=DummyWS(), fw=fw)


def make_app(app_id: str = "deadbeef") -> AppSession:
    return AppSession(id=app_id, ws=DummyWS())


def test_two_roles_coexist() -> None:
    registry = Registry()
    device = make_device()
    app = make_app()
    registry.register(device)
    registry.register(app)
    assert registry.latest_device() is device
    assert registry.latest_app() is app
    assert len(registry.sessions()) == 2


def test_api_snapshot_without_sessions() -> None:
    registry = Registry()
    assert registry.device_status() == {"online": False, "state": ButtonState.OFFLINE.value}
    assert registry.app_status() == {"online": False}


def test_api_snapshot_with_sessions() -> None:
    registry = Registry()
    device = make_device()
    device.last_seen = 123
    registry.register(device)
    registry.register(make_app())

    device_status = registry.device_status()
    assert device_status["online"] is True
    assert device_status["id"] == "a1b2c3d4"
    assert device_status["state"] == "ARMED"
    assert device_status["fw"] == "1.0.0"
    assert device_status["last_seen"] == 123
    assert registry.app_status()["id"] == "deadbeef"


def test_replacement_returns_previous_and_old_cleanup_is_safe() -> None:
    registry = Registry()
    first = make_device()
    second = make_device()
    assert registry.register(first) is None
    assert registry.register(second) is first
    assert registry.latest_device() is second

    assert registry.unregister(first) is False
    assert registry.latest_device() is second
    assert registry.unregister(second) is True
    assert registry.latest_device() is None


def test_closed_sessions_are_not_online() -> None:
    registry = Registry()
    device = make_device()
    registry.register(device)
    device.closed = True
    assert registry.latest_device() is None
    assert registry.device_status()["online"] is False


async def test_send_and_close() -> None:
    ws = DummyWS()
    session = DeviceSession(id="a1b2c3d4", ws=ws)
    assert await session.send({"v": 1, "t": "ping", "t_epoch": 1}) is True
    assert ws.sent == ['{"v":1,"t":"ping","t_epoch":1}']
    await session.close(4400, "bad")
    assert session.closed is True
    assert session.close_code == 4400
    assert ws.close_calls == [(4400, "bad")]
    assert await session.send({"v": 1}) is False


def test_device_and_app_id_helpers() -> None:
    device: Any = make_device()
    app: Any = make_app()
    assert device.device_id == "a1b2c3d4"
    assert device.app_id is None
    assert app.app_id == "deadbeef"
    assert app.device_id is None
