"""Shared fixtures: one real uvicorn server per test, random port."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from testserver import TestServer

from app.config import Settings


@pytest.fixture
def server_factory(tmp_path: Path) -> Iterator[Callable[..., TestServer]]:
    servers: list[TestServer] = []
    counter = itertools.count()

    def start(**overrides: object) -> TestServer:
        options: dict[str, object] = {
            "state_file": tmp_path / f"state-{next(counter)}.json",
            "log_level": "WARNING",
            "hello_timeout_s": 1.0,
            "ping_interval_s": 30.0,
            "pong_timeout_s": 5.0,
        }
        options.update(overrides)
        server = TestServer(Settings(**options))  # type: ignore[arg-type]
        server.start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()
