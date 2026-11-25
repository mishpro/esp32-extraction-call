"""Persistence of last_press/last_status/seq, atomic writes (§8, step 1.5)."""

from __future__ import annotations

import json

from app.models import Role
from app.state import StateStore

PRESS = {"v": 1, "t": "press", "seq": 7, "t_epoch": 1_793_001_234, "from": "a1b2c3d4"}


def test_last_press_survives_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    first = StateStore(path)
    first.record_press(PRESS)
    first.mark_seq(Role.DEVICE, "a1b2c3d4", 7)

    second = StateStore(path)
    second.load()
    assert second.last_press == PRESS
    assert second.is_duplicate(Role.DEVICE, "a1b2c3d4", 7)
    assert not second.is_duplicate(Role.DEVICE, "a1b2c3d4", 8)


def test_status_survives_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    status = {
        "v": 1,
        "t": "status",
        "t_epoch": 1,
        "device": {"online": True, "state": "ARMED", "remaining_s": 0},
        "app": {"online": False},
    }
    store = StateStore(path)
    store.record_status(status)

    reloaded = StateStore(path)
    reloaded.load()
    assert reloaded.last_status == status


def test_atomic_write_leaves_no_tmp_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "nested" / "state.json"
    store = StateStore(path)
    store.record_press(PRESS)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["last_press"] == PRESS
    assert list(path.parent.glob("*.tmp")) == []


def test_corrupt_file_is_ignored(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    store = StateStore(path)
    store.load()
    assert store.last_press is None
    # The store still works and rewrites the file atomically.
    store.record_press(PRESS)
    assert StateStore(path).path.exists()


def test_reset_seq_unblocks_sender(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.mark_seq(Role.DEVICE, "a1b2c3d4", 57)
    assert store.is_duplicate(Role.DEVICE, "a1b2c3d4", 1)
    store.reset_seq(Role.DEVICE, "a1b2c3d4")
    assert not store.is_duplicate(Role.DEVICE, "a1b2c3d4", 1)


def test_seq_watermarks_are_per_sender(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.mark_seq(Role.DEVICE, "a1b2c3d4", 5)
    assert store.is_duplicate(Role.DEVICE, "a1b2c3d4", 5)
    assert not store.is_duplicate(Role.DEVICE, "deadbeef", 5)
    assert not store.is_duplicate(Role.APP, "a1b2c3d4", 5)
