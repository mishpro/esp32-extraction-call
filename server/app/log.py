"""JSON logging to stdout (Протокол §8).

Every record is a single JSON line with the canonical keys ``event``,
``device_id``, ``app_id``, ``session`` and ``seq``. Tokens are never passed in
and any field whose name contains "token" is dropped as a second line of
defence.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

CANONICAL_KEYS = ("event", "device_id", "app_id", "session", "seq")

LOGGER_NAME = "guidecaller"

_logger = logging.getLogger(LOGGER_NAME)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return "token" in lowered or "secret" in lowered


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items() if not _is_sensitive(str(k))}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def setup_logging(level: str = "INFO") -> None:
    """Configure the single stdout handler; safe to call more than once."""
    _logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    _logger.propagate = False
    for handler in list(_logger.handlers):
        _logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)


def log_event(
    event: str,
    *,
    device_id: str | None = None,
    app_id: str | None = None,
    session: str | None = None,
    seq: int | None = None,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    record: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": logging.getLevelName(level),
        "event": event,
        "device_id": device_id,
        "app_id": app_id,
        "session": session,
        "seq": seq,
    }
    safe_fields = _sanitize(fields)
    record.update(safe_fields)
    _logger.log(level, json.dumps(record, ensure_ascii=False, sort_keys=False))
