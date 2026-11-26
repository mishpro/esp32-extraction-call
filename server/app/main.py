"""FastAPI application: /ws, /api/status, /healthz (plan/server.md).

One asyncio task per WebSocket connection (§8). The endpoint enforces the
protocol envelope: hello first (10 s), 4096-byte frames, version 1, heartbeat
every 15 s with 3 missed pongs, then hands messages to ``Router``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, WebSocket

from .config import Settings
from .errors import (
    CLOSE_AUTH,
    CLOSE_INTERNAL,
    CLOSE_NORMAL,
    CLOSE_PROTOCOL,
    ErrorCode,
    ProtocolError,
)
from .log import log_event, setup_logging
from .models import (
    NO_PEER_ID,
    ButtonState,
    Hello,
    HelloOk,
    PeerState,
    Role,
    dump,
    json_frame,
    parse_client_message,
    parse_hello,
)
from .registry import (
    AppSession,
    BaseSession,
    DeviceSession,
    Registry,
    now_epoch,
)
from .router import Router
from .state import StateStore


class _Disconnected(Exception):
    """Peer closed the socket before/while a frame was expected."""


# ---------------------------------------------------------------------------
# frame helpers
# ---------------------------------------------------------------------------


def _frame_size(raw: str | bytes) -> int:
    return len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8"))


def _check_size(raw: str | bytes, settings: Settings) -> None:
    size = _frame_size(raw)
    if size > settings.max_frame_bytes:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            f"frame too large ({size} > {settings.max_frame_bytes} bytes)",
            close_code=CLOSE_PROTOCOL,
        )


async def _receive(websocket: WebSocket) -> str | None:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        return None
    if message.get("bytes") is not None:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "binary frames are not supported",
            close_code=CLOSE_PROTOCOL,
        )
    text = message.get("text")
    if text is None:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "empty frame",
            close_code=CLOSE_PROTOCOL,
        )
    return text


async def _send_raw(websocket: WebSocket, frame: dict[str, Any]) -> None:
    try:
        await websocket.send_text(json_frame(frame))
    except Exception:
        pass


async def _reject(websocket: WebSocket, error: ProtocolError) -> None:
    await _send_raw(websocket, error.frame())
    try:
        await websocket.close(
            code=error.close_code or CLOSE_PROTOCOL,
            reason=error.msg[:120],
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# hello phase
# ---------------------------------------------------------------------------


async def _read_hello(websocket: WebSocket, settings: Settings) -> Hello:
    try:
        raw = await asyncio.wait_for(_receive(websocket), timeout=settings.hello_timeout_s)
    except TimeoutError as exc:
        raise ProtocolError(
            ErrorCode.BAD_REQUEST,
            "hello timeout",
            close_code=CLOSE_PROTOCOL,
        ) from exc
    if raw is None:
        raise _Disconnected
    _check_size(raw, settings)
    return parse_hello(raw)


def _check_auth(settings: Settings, hello: Hello) -> None:
    if not settings.auth_required:
        return
    if not settings.verify_token(hello.role, hello.token):
        raise ProtocolError(ErrorCode.AUTH_FAILED, "bad token", close_code=CLOSE_AUTH)


def _new_session(websocket: WebSocket, hello: Hello) -> BaseSession:
    if hello.role is Role.DEVICE:
        return DeviceSession(id=hello.id, ws=websocket, fw=hello.fw)
    return AppSession(id=hello.id, ws=websocket)


def _peer_payload(registry: Registry, session: BaseSession) -> PeerState:
    if session.role is Role.DEVICE:
        app = registry.latest_app()
        if app is not None:
            return PeerState(
                id=app.id,
                online=True,
                state=ButtonState.ARMED,
                remaining_s=0,
            )
    else:
        device = registry.latest_device()
        if device is not None:
            return PeerState(
                id=device.id,
                online=True,
                state=device.state,
                remaining_s=device.remaining_s(),
            )
    # No peer connected. The schema requires an 8-hex id even when offline.
    return PeerState(
        id=NO_PEER_ID,
        online=False,
        state=ButtonState.OFFLINE,
        remaining_s=0,
    )


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------


async def _heartbeat(session: BaseSession, settings: Settings) -> None:
    misses = 0
    while not session.closed:
        await asyncio.sleep(settings.ping_interval_s)
        if session.closed:
            return
        session.pong_event.clear()
        if not await session.send({"v": 1, "t": "ping", "t_epoch": now_epoch()}):
            return
        try:
            await asyncio.wait_for(session.pong_event.wait(), timeout=settings.pong_timeout_s)
            misses = 0
        except TimeoutError:
            misses += 1
            log_event(
                "ping_miss",
                device_id=session.device_id,
                app_id=session.app_id,
                session=session.session_id,
                misses=misses,
                level=logging.WARNING,
            )
            if misses >= settings.max_ping_misses:
                log_event(
                    "close",
                    device_id=session.device_id,
                    app_id=session.app_id,
                    session=session.session_id,
                    reason="heartbeat timeout",
                    level=logging.WARNING,
                )
                await session.close(CLOSE_NORMAL, "heartbeat timeout")
                return


# ---------------------------------------------------------------------------
# connection handler
# ---------------------------------------------------------------------------


async def _serve(
    websocket: WebSocket,
    settings: Settings,
    registry: Registry,
    router: Router,
) -> None:
    await websocket.accept()
    session: BaseSession | None = None
    heartbeat: asyncio.Task[None] | None = None
    try:
        hello = await _read_hello(websocket, settings)
        log_event(
            "hello",
            device_id=hello.id if hello.role is Role.DEVICE else None,
            app_id=hello.id if hello.role is Role.APP else None,
            role=hello.role.value,
            fw=hello.fw,
        )
        _check_auth(settings, hello)

        session = _new_session(websocket, hello)
        previous = registry.register(session)
        if previous is not None:
            log_event(
                "session_replaced",
                device_id=session.device_id,
                app_id=session.app_id,
                session=session.session_id,
                previous_session=previous.session_id,
            )
            await previous.close(CLOSE_NORMAL, "replaced by new session")
            if isinstance(session, DeviceSession):
                router.forget_device(session.id)

        if isinstance(session, DeviceSession):
            # A countdown survives a server restart or a WiFi drop (§8): the
            # device keeps its deadline in NVS, the server restores its view.
            router.restore_device_timer(session)

        peer = _peer_payload(registry, session)
        hello_ok = dump(
            HelloOk(
                v=1,
                t="hello_ok",
                session=session.session_id,
                t_epoch=now_epoch(),
                peer=peer,
            )
        )
        if not await session.send(hello_ok):
            return
        session.hello_done = True
        log_event(
            "hello_ok",
            device_id=session.device_id,
            app_id=session.app_id,
            session=session.session_id,
        )
        await router.broadcast_status()

        heartbeat = asyncio.create_task(
            _heartbeat(session, settings),
            name=f"heartbeat-{session.session_id}",
        )

        while True:
            raw = await _receive(websocket)
            if raw is None:
                break
            session.mark_seen()
            try:
                _check_size(raw, settings)
                message = parse_client_message(raw)
                await router.dispatch(session, message)
            except ProtocolError as error:
                await session.send(error.frame())
                log_event(
                    "error",
                    device_id=session.device_id,
                    app_id=session.app_id,
                    session=session.session_id,
                    seq=error.seq,
                    code=error.code.value,
                    msg=error.msg,
                    close_code=error.close_code,
                )
                if error.close_code is not None:
                    await session.close(error.close_code, error.msg)
                    break
    except _Disconnected:
        pass
    except ProtocolError as error:
        # Raised during the hello phase, before a session exists.
        log_event(
            "error",
            device_id=session.device_id if session else None,
            app_id=session.app_id if session else None,
            session=session.session_id if session else None,
            seq=error.seq,
            code=error.code.value,
            msg=error.msg,
            close_code=error.close_code,
        )
        if session is not None:
            await session.send(error.frame())
            await session.close(error.close_code or CLOSE_PROTOCOL, error.msg)
        else:
            await _reject(websocket, error)
    except Exception as exc:  # noqa: BLE001 - last-resort guard (§5: 1011)
        log_event(
            "internal",
            device_id=session.device_id if session else None,
            app_id=session.app_id if session else None,
            session=session.session_id if session else None,
            error=f"{type(exc).__name__}: {exc}",
            level=logging.ERROR,
        )
        internal = ProtocolError(ErrorCode.INTERNAL, "internal error", close_code=CLOSE_INTERNAL)
        if session is not None:
            await session.send(internal.frame())
            await session.close(CLOSE_INTERNAL, "internal error")
        else:
            await _reject(websocket, internal)
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if session is not None:
            removed = registry.unregister(session)
            if removed and isinstance(session, DeviceSession):
                router.forget_device(session.id)
            await session.close(CLOSE_NORMAL, "connection closed")
            log_event(
                "close",
                device_id=session.device_id,
                app_id=session.app_id,
                session=session.session_id,
                code=session.close_code,
                reason=session.close_reason,
            )
            if removed:
                await router.broadcast_status()


# ---------------------------------------------------------------------------
# app factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging(settings.log_level)

    registry = Registry()
    state = StateStore(settings.state_file)
    router = Router(registry, state)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        state.load()
        log_event(
            "startup",
            state_file=str(settings.state_file),
            auth_required=settings.auth_required,
        )
        yield
        log_event("shutdown")

    app = FastAPI(title="Nightcall server", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.registry = registry
    app.state.state_store = state
    app.state.router = router

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return {"device": registry.device_status(), "app": registry.app_status()}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await _serve(websocket, settings, registry, router)

    return app


app = create_app()
