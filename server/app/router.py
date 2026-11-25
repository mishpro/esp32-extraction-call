"""Routing of press/ack/ping between roles (Протокол §4, plan/server.md §1.7).

MVP assumption: one button and one app. When several peers of a role are
connected, the most recently connected one is used (see ``Registry``).
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .errors import CLOSE_PROTOCOL, ErrorCode, ProtocolError
from .log import log_event
from .models import (
    Ack,
    AckStage,
    AppStatus,
    ButtonState,
    Cancel,
    DeviceStatus,
    Message,
    Ping,
    Pong,
    Press,
    Role,
    Status,
    Timer,
    dump,
)
from .registry import AppSession, BaseSession, DeviceSession, Registry, now_epoch
from .state import StateStore


class Router:
    PENDING_LIMIT = 1024

    def __init__(self, registry: Registry, state: StateStore) -> None:
        self.registry = registry
        self.state = state
        # seq -> device_id for presses forwarded but not yet acked by the app
        self.pending_press: dict[int, str] = {}
        self._delivered: deque[int] = deque(maxlen=256)

    # -- status ----------------------------------------------------------

    def status_frame(self) -> dict[str, Any]:
        device = self.registry.latest_device()
        app = self.registry.latest_app()
        if device is None:
            device_status = DeviceStatus(online=False, state=ButtonState.OFFLINE)
        else:
            device_status = DeviceStatus(
                online=True,
                state=device.state,
                remaining_s=0,
                fw=device.fw,
            )
        frame = Status(
            v=1,
            t="status",
            t_epoch=now_epoch(),
            device=device_status,
            app=AppStatus(online=app is not None),
        )
        return dump(frame)

    async def broadcast_status(self) -> None:
        frame = self.status_frame()
        self.state.record_status(frame)
        device_session = self.registry.latest_device()
        app_session = self.registry.latest_app()
        device = frame["device"]
        log_event(
            "status",
            device_id=device_session.id if device_session else None,
            app_id=app_session.id if app_session else None,
            device_online=device["online"],
            app_online=frame["app"]["online"],
            state=device["state"],
        )
        for session in self.registry.sessions():
            if session.hello_done and not session.closed:
                await session.send(frame)

    # -- inbound ---------------------------------------------------------

    async def dispatch(self, session: BaseSession, message: Message) -> None:
        if isinstance(message, Press):
            await self._press(session, message)
        elif isinstance(message, Ack):
            await self._ack(session, message)
        elif isinstance(message, Ping):
            await self._ping(session, message)
        elif isinstance(message, Pong):
            session.mark_pong()
        elif isinstance(message, Timer):
            await self._timer(session, message)
        elif isinstance(message, Cancel):
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                "cancel is not implemented in MVP (S4)",
                seq=message.seq,
                close_code=CLOSE_PROTOCOL,
            )
        else:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                f"message {message.t!r} is not accepted from clients",
                close_code=CLOSE_PROTOCOL,
            )

    async def _press(self, session: BaseSession, message: Press) -> None:
        if session.role is not Role.DEVICE or not isinstance(session, DeviceSession):
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                "press is only accepted from devices",
                seq=message.seq,
                close_code=CLOSE_PROTOCOL,
            )

        # seq is not persisted on the client, so a fresh boot starts at 1.
        # Treat seq=1 as the first press of a new client epoch and drop the
        # persisted watermark, otherwise the button would stay blocked forever.
        if session.presses_received == 0 and message.seq == 1:
            self.state.reset_seq(Role.DEVICE, session.id)
            log_event("seq_epoch_reset", device_id=session.id, session=session.session_id)

        if self.state.is_duplicate(Role.DEVICE, session.id, message.seq):
            log_event(
                "duplicate",
                device_id=session.id,
                session=session.session_id,
                seq=message.seq,
            )
            raise ProtocolError(ErrorCode.DUPLICATE, "duplicate seq", seq=message.seq)

        self.state.mark_seq(Role.DEVICE, session.id, message.seq)
        session.presses_received += 1
        log_event(
            "press",
            device_id=session.id,
            session=session.session_id,
            seq=message.seq,
        )

        server_ack = dump(Ack(v=1, t="ack", seq=message.seq, ok=True, stage=AckStage.SERVER))
        if not await session.send(server_ack):
            return
        log_event(
            "ack",
            device_id=session.id,
            session=session.session_id,
            seq=message.seq,
            stage=AckStage.SERVER.value,
        )

        peer = self.registry.latest_app()
        if peer is None:
            raise ProtocolError(
                ErrorCode.NO_PEER,
                "no app connected",
                seq=message.seq,
            )

        forwarded = dump(
            Press(
                v=1,
                t="press",
                seq=message.seq,
                t_epoch=now_epoch(),
                sender=session.id,
            )
        )
        if not await peer.send(forwarded):
            raise ProtocolError(
                ErrorCode.NO_PEER,
                "app disconnected during delivery",
                seq=message.seq,
            )

        self.pending_press[message.seq] = session.id
        while len(self.pending_press) > self.PENDING_LIMIT:
            oldest = next(iter(self.pending_press))
            del self.pending_press[oldest]
        self.state.record_press(forwarded)
        session.state = ButtonState.SENT
        await self.broadcast_status()

    async def _ack(self, session: BaseSession, message: Ack) -> None:
        if session.role is not Role.APP or not isinstance(session, AppSession):
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                "ack is only accepted from apps in MVP",
                seq=message.seq,
                close_code=CLOSE_PROTOCOL,
            )
        if message.stage is AckStage.SERVER:
            log_event(
                "ack_ignored",
                app_id=session.id,
                session=session.session_id,
                seq=message.seq,
                stage=message.stage.value,
            )
            return

        device_id = self.pending_press.pop(message.seq, None)
        if device_id is None:
            code = ErrorCode.DUPLICATE if message.seq in self._delivered else ErrorCode.BAD_REQUEST
            log_event(
                "ack_unknown",
                app_id=session.id,
                session=session.session_id,
                seq=message.seq,
            )
            raise ProtocolError(code, "unknown press seq", seq=message.seq)
        self._delivered.append(message.seq)

        frame = dump(
            Ack(
                v=1,
                t="ack",
                seq=message.seq,
                ok=message.ok,
                stage=AckStage.DELIVERED,
            )
        )
        device = self.registry.get_device(device_id)
        log_event(
            "ack",
            device_id=device_id,
            app_id=session.id,
            session=session.session_id,
            seq=message.seq,
            stage=AckStage.DELIVERED.value,
            ok=message.ok,
        )
        if device is not None:
            await device.send(frame)
        await session.send(frame)
        if device is not None:
            device.state = ButtonState.WAIT_ETA if message.ok else ButtonState.ARMED
            await self.broadcast_status()

    async def _ping(self, session: BaseSession, message: Ping) -> None:
        # The button also pings on its own (§4.8); answer with the same t_epoch.
        await session.send(dump(Pong(v=1, t="pong", t_epoch=message.t_epoch)))

    async def _timer(self, session: BaseSession, message: Timer) -> None:
        if session.role is not Role.APP:
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                "timer is only accepted from apps",
                seq=message.seq,
                close_code=CLOSE_PROTOCOL,
            )
        # MVP: timer/cancel are S4. Valid requests get a protocol error and a
        # clean close instead of being silently dropped.
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "timer is not implemented in MVP (S4)",
            seq=message.seq,
            close_code=CLOSE_PROTOCOL,
        )

    # -- bookkeeping -----------------------------------------------------

    def forget_device(self, device_id: str) -> None:
        for seq, owner in list(self.pending_press.items()):
            if owner == device_id:
                del self.pending_press[seq]
