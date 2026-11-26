"""Routing of press/ack/ping between roles (Протокол §4, plan/server.md §1.7).

MVP assumption: one button and one app. When several peers of a role are
connected, the most recently connected one is used (see ``Registry``).
"""

from __future__ import annotations

import asyncio
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
        # app seq -> (device_id, app_id, kind, forwarded frame) for timer/cancel
        # forwarded but not yet acked by the device
        self.pending_command: dict[int, tuple[str, str, str, dict[str, Any] | None]] = {}
        self._delivered: deque[int] = deque(maxlen=256)
        self._commands_delivered: deque[int] = deque(maxlen=256)
        # device_id -> task that flips COUNTDOWN to ARRIVED at deadline_t
        self._timer_tasks: dict[str, asyncio.Task[None]] = {}

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
                remaining_s=device.remaining_s(),
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
            await self._cancel(session, message)
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
        # A new call supersedes a finished countdown: drop the stored deadline.
        if session.deadline_t is not None:
            session.deadline_t = None
            self.state.record_timer(None)
            self._cancel_timer_task(session.id)
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
        if isinstance(session, DeviceSession):
            await self._device_ack(session, message)
            return
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
        server_now = now_epoch()
        deadline = server_now + message.duration_s
        forwarded = dump(
            Timer(
                v=1,
                t="timer",
                seq=message.seq,
                duration_s=message.duration_s,
                deadline_t=deadline,
                t_epoch=server_now,
            )
        )
        await self._forward_command(session, "timer", message.seq, forwarded, deadline)

    async def _cancel(self, session: BaseSession, message: Cancel) -> None:
        forwarded = dump(
            Cancel(v=1, t="cancel", seq=message.seq, t_epoch=now_epoch())
        )
        await self._forward_command(session, "cancel", message.seq, forwarded, None)

    async def _forward_command(
        self,
        session: BaseSession,
        kind: str,
        seq: int,
        frame: dict[str, Any],
        deadline: int | None,
    ) -> None:
        """Relay ``timer``/``cancel`` from the app and wait for the device ack (§4.4–4.6)."""
        if session.role is not Role.APP or not isinstance(session, AppSession):
            raise ProtocolError(
                ErrorCode.BAD_REQUEST,
                f"{kind} is only accepted from apps",
                seq=seq,
                close_code=CLOSE_PROTOCOL,
            )
        # The app's seq is its own command counter; a retry after the 10 s
        # timeout must use a new seq (§4.6), so an old one is always a duplicate.
        if seq <= session.last_command_seq:
            log_event(
                "duplicate",
                app_id=session.id,
                session=session.session_id,
                seq=seq,
            )
            raise ProtocolError(ErrorCode.DUPLICATE, "duplicate seq", seq=seq)
        session.last_command_seq = seq

        server_ack = dump(Ack(v=1, t="ack", seq=seq, ok=True, stage=AckStage.SERVER))
        if not await session.send(server_ack):
            return
        log_event(
            "ack",
            app_id=session.id,
            session=session.session_id,
            seq=seq,
            stage=AckStage.SERVER.value,
        )

        device = self.registry.latest_device()
        if device is None:
            raise ProtocolError(ErrorCode.NO_PEER, "no device connected", seq=seq)
        if not await device.send(frame):
            raise ProtocolError(
                ErrorCode.NO_PEER,
                "device disconnected during delivery",
                seq=seq,
            )

        self.pending_command[seq] = (device.id, session.id, kind, frame)
        while len(self.pending_command) > self.PENDING_LIMIT:
            oldest = next(iter(self.pending_command))
            del self.pending_command[oldest]
        log_event(
            kind,
            device_id=device.id,
            app_id=session.id,
            session=session.session_id,
            seq=seq,
            duration_s=frame.get("duration_s"),
            deadline_t=deadline,
        )

    async def _device_ack(self, session: DeviceSession, message: Ack) -> None:
        """The button acks a relayed ``timer``/``cancel`` (§4.6, stage=delivered)."""
        if message.stage is AckStage.SERVER:
            log_event(
                "ack_ignored",
                device_id=session.id,
                session=session.session_id,
                seq=message.seq,
                stage=message.stage.value,
            )
            return

        pending = self.pending_command.pop(message.seq, None)
        if pending is None or pending[0] != session.id:
            code = (
                ErrorCode.DUPLICATE
                if message.seq in self._commands_delivered
                else ErrorCode.BAD_REQUEST
            )
            log_event(
                "ack_unknown",
                device_id=session.id,
                session=session.session_id,
                seq=message.seq,
            )
            raise ProtocolError(code, "unknown command seq", seq=message.seq)
        _, app_id, kind, forwarded = pending
        self._commands_delivered.append(message.seq)

        if message.ok:
            if kind == "timer" and forwarded is not None:
                session.deadline_t = forwarded.get("deadline_t")
                session.state = ButtonState.COUNTDOWN
                self.state.record_timer(forwarded)
                self._schedule_timer_expiry(session)
            elif kind == "cancel":
                session.deadline_t = None
                session.state = ButtonState.ARMED
                self.state.record_timer(None)
                self._cancel_timer_task(session.id)

        frame = dump(
            Ack(
                v=1,
                t="ack",
                seq=message.seq,
                ok=message.ok,
                stage=AckStage.DELIVERED,
            )
        )
        logged = {
            "device_id": session.id,
            "app_id": app_id,
            "session": session.session_id,
            "seq": message.seq,
            "stage": AckStage.DELIVERED.value,
            "ok": message.ok,
        }
        log_event("ack", **logged)
        app = self.registry.apps.get(app_id)
        if app is not None and not app.closed:
            await app.send(frame)
        await self.broadcast_status()

    def restore_device_timer(self, device: DeviceSession) -> None:
        """Apply the persisted countdown to a (re)connecting device (§8).

        The device keeps its own deadline in NVS; this only keeps the server's
        view (``status``/``hello_ok``) in sync after a server restart or a
        network drop, so the app does not show ``ARMED`` during a countdown.
        """
        last = self.state.last_timer
        if last is None:
            return
        deadline = last.get("deadline_t")
        if type(deadline) is not int:
            return
        device.deadline_t = deadline
        device.state = (
            ButtonState.COUNTDOWN if deadline > now_epoch() else ButtonState.ARRIVED
        )
        if device.state is ButtonState.COUNTDOWN:
            self._schedule_timer_expiry(device)

    # -- countdown expiry -------------------------------------------------

    def _cancel_timer_task(self, device_id: str) -> None:
        task = self._timer_tasks.pop(device_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_timer_expiry(self, device: DeviceSession) -> None:
        """Flip the server view to ARRIVED exactly at ``deadline_t`` (§4.7).

        The button also flips locally, but it has no message for that, and the
        app needs a ``status`` with ``ARRIVED`` to show "time is up".
        """
        self._cancel_timer_task(device.id)
        if device.deadline_t is None or device.closed:
            return
        self._timer_tasks[device.id] = asyncio.create_task(
            self._expire_timer(device, device.deadline_t)
        )

    async def _expire_timer(self, device: DeviceSession, deadline: int) -> None:
        await asyncio.sleep(max(0, deadline - now_epoch()))
        if device.closed or device.deadline_t != deadline:
            return
        device.state = ButtonState.ARRIVED
        # Only drop our own entry: a reconnect may already have scheduled a new task.
        if self._timer_tasks.get(device.id) is asyncio.current_task():
            self._timer_tasks.pop(device.id, None)
        log_event(
            "timer_arrived",
            device_id=device.id,
            session=device.session_id,
            deadline_t=deadline,
        )
        await self.broadcast_status()

    # -- bookkeeping -----------------------------------------------------

    def forget_device(self, device_id: str) -> None:
        self._cancel_timer_task(device_id)
        for seq, owner in list(self.pending_press.items()):
            if owner == device_id:
                del self.pending_press[seq]
        for seq, pending in list(self.pending_command.items()):
            if pending[0] == device_id:
                del self.pending_command[seq]
