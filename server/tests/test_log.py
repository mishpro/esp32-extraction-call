"""Logging: one JSON line per event, tokens never appear (§8)."""

from __future__ import annotations

import json

from app.log import log_event, setup_logging


def last_record(capsys) -> dict:  # type: ignore[no-untyped-def]
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "no log line written"
    return json.loads(out[-1])


def test_log_line_has_canonical_keys(capsys) -> None:  # type: ignore[no-untyped-def]
    setup_logging("INFO")
    log_event("press", device_id="a1b2c3d4", session="s-1111", seq=7)
    record = last_record(capsys)
    for key in ("event", "device_id", "app_id", "session", "seq"):
        assert key in record
    assert record["event"] == "press"
    assert record["device_id"] == "a1b2c3d4"
    assert record["seq"] == 7


def test_token_never_logged(capsys) -> None:  # type: ignore[no-untyped-def]
    setup_logging("INFO")
    secret = "super-secret-token-value"
    log_event("hello", device_id="a1b2c3d4", token=secret)
    log_event("nested", extra={"device": {"token": secret, "id": "a1b2c3d4"}})
    out = capsys.readouterr().out
    assert secret not in out
    assert "token" not in out.lower()


def test_event_fields_merge(capsys) -> None:  # type: ignore[no-untyped-def]
    setup_logging("INFO")
    log_event("ping_miss", device_id="a1b2c3d4", misses=2)
    record = last_record(capsys)
    assert record["misses"] == 2
    assert record["level"] == "INFO"
