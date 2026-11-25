"""Helpers for integration tests: run a real uvicorn server in a thread.

Starlette's ``TestClient`` runs every WebSocket in its own event loop, which
cannot model several clients sharing one registry (cross-session sends). A real
uvicorn instance on a random port is closer to production and lets tests use
timeouts via ``asyncio.wait_for``.
"""

from __future__ import annotations

import socket
import threading
import time

import uvicorn

from app.config import Settings
from app.main import create_app


class TestServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.app = create_app(settings)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(128)
        self.port = self._socket.getsockname()[1]
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            lifespan="on",
            ws="websockets-sansio",
            # Protocol heartbeat is tested, not uvicorn's WS-level ping.
            ws_ping_interval=3600,
            ws_ping_timeout=3600,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            kwargs={"sockets": [self._socket]},
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn did not start in time")
            if not self._thread.is_alive():
                raise RuntimeError("uvicorn thread died during startup")
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        self._socket.close()

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @property
    def http_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
