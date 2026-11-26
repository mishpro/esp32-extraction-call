"""In-memory session registry (Протокол §8: devices/apps dicts)."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .models import ButtonState, Role


def new_session_id() -> str:
    return f"s-{secrets.token_hex(4)}"


def now_epoch() -> int:
    return int(time.time())


@dataclass(eq=False)
class BaseSession:
    """One WebSocket connection after a successful hello."""

    role: ClassVar[Role]

    id: str
    ws: Any
    fw: str | None = None
    session_id: str = field(default_factory=new_session_id)
    created_at: int = field(default_factory=now_epoch)
    last_seen: int = field(default_factory=now_epoch)
    hello_done: bool = False
    closed: bool = False
    close_code: int | None = None
    close_reason: str = ""
    pong_event: asyncio.Event = field(default_factory=asyncio.Event)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, frame: dict[str, Any]) -> bool:
        """Send a frame; ``False`` means the connection is unusable."""
        if self.closed:
            return False
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        async with self.send_lock:
            if self.closed:
                return False
            try:
                await self.ws.send_text(payload)
            except Exception:
                self.closed = True
                self.pong_event.set()
                return False
        return True

    async def close(self, code: int, reason: str = "") -> None:
        if self.closed:
            return
        self.closed = True
        self.close_code = code
        self.close_reason = reason
        self.pong_event.set()
        try:
            await self.ws.close(code=code, reason=reason[:120])
        except Exception:
            pass

    def mark_seen(self) -> None:
        self.last_seen = now_epoch()

    def mark_pong(self) -> None:
        self.pong_event.set()

    @property
    def device_id(self) -> str | None:
        return self.id if self.role is Role.DEVICE else None

    @property
    def app_id(self) -> str | None:
        return self.id if self.role is Role.APP else None


@dataclass(eq=False)
class DeviceSession(BaseSession):
    role: ClassVar[Role] = Role.DEVICE

    state: ButtonState = ButtonState.ARMED
    presses_received: int = 0
    # Absolute epoch of the running countdown (§4.4); None when no timer is set.
    deadline_t: int | None = None

    def remaining_s(self) -> int:
        """Seconds left on the countdown in the server clock (§4.4)."""
        if self.deadline_t is None:
            return 0
        return max(0, self.deadline_t - now_epoch())


@dataclass(eq=False)
class AppSession(BaseSession):
    role: ClassVar[Role] = Role.APP

    # Highest `seq` of a `timer`/`cancel` relayed in this session (§4.6/NFR-15).
    last_command_seq: int = 0


class Registry:
    """MVP pairing assumption: one button and one app; the router picks the
    most recently connected peer. The registry itself keeps all sessions so a
    future increment can add explicit pairing without touching the transport.
    """

    def __init__(self) -> None:
        self.devices: dict[str, DeviceSession] = {}
        self.apps: dict[str, AppSession] = {}

    # -- registration ----------------------------------------------------

    def register(self, session: BaseSession) -> BaseSession | None:
        """Add a session, returning the previous session with the same id."""
        if isinstance(session, DeviceSession):
            previous = self.devices.get(session.id)
            self.devices[session.id] = session
            return previous
        previous = self.apps.get(session.id)
        self.apps[session.id] = session
        return previous

    def unregister(self, session: BaseSession) -> bool:
        """Remove exactly this session object (a replacement must survive)."""
        if isinstance(session, DeviceSession):
            if self.devices.get(session.id) is session:
                del self.devices[session.id]
                return True
            return False
        if self.apps.get(session.id) is session:
            del self.apps[session.id]
            return True
        return False

    # -- lookup ----------------------------------------------------------

    @staticmethod
    def _latest(table: dict[str, Any]) -> Any:
        online = [session for session in table.values() if not session.closed]
        if not online:
            return None
        return max(online, key=lambda session: session.created_at)

    def latest_device(self) -> DeviceSession | None:
        return self._latest(self.devices)

    def latest_app(self) -> AppSession | None:
        return self._latest(self.apps)

    def get_device(self, device_id: str) -> DeviceSession | None:
        session = self.devices.get(device_id)
        return session if session is not None and not session.closed else None

    def sessions(self) -> list[BaseSession]:
        return [*self.devices.values(), *self.apps.values()]

    # -- diagnostics (GET /api/status) ----------------------------------

    def device_status(self) -> dict[str, Any]:
        device = self.latest_device()
        if device is None:
            return {"online": False, "state": ButtonState.OFFLINE.value}
        status: dict[str, Any] = {
            "id": device.id,
            "session": device.session_id,
            "online": True,
            "state": device.state.value,
            "remaining_s": device.remaining_s(),
            "last_seen": device.last_seen,
        }
        if device.fw is not None:
            status["fw"] = device.fw
        return status

    def app_status(self) -> dict[str, Any]:
        app = self.latest_app()
        if app is None:
            return {"online": False}
        return {
            "id": app.id,
            "session": app.session_id,
            "online": True,
            "last_seen": app.last_seen,
        }
