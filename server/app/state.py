"""Persistence of ``last_press`` / ``last_timer`` / ``last_status`` / seq watermarks
(Протокол §8).

A single JSON file is rewritten atomically (write to ``*.tmp``, fsync, rename).
The file lives outside the repository by default (``server/.state/state.json``,
ignored by ``server/.gitignore``); ``STATE_FILE`` overrides the location.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

from .log import log_event
from .models import Role

_EMPTY: dict[str, Any] = {
    "version": 1,
    "last_press": None,
    "last_timer": None,
    "last_status": None,
    "last_seq": {},
    # FCM registration of the app (§4.10); None until it sends register_push.
    "push": None,
}


def _valid_push(value: Any) -> bool:
    """A stored registration must carry both the owner and the token."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("app_id"), str)
        and isinstance(value.get("token"), str)
        and bool(value["token"])
    )


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = copy.deepcopy(_EMPTY)

    # -- loading ---------------------------------------------------------

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            log_event("state_load_failed", level=logging.WARNING, error=str(exc))
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log_event("state_load_failed", level=logging.WARNING, error=str(exc))
            return
        if not isinstance(data, dict):
            log_event("state_load_failed", level=logging.WARNING, error="not an object")
            return
        last_seq = data.get("last_seq")
        push = data.get("push")
        self._data = {
            "version": 1,
            "last_press": data.get("last_press"),
            "last_timer": data.get("last_timer"),
            "last_status": data.get("last_status"),
            "last_seq": dict(last_seq) if isinstance(last_seq, dict) else {},
            "push": push if _valid_push(push) else None,
        }
        log_event("state_loaded", state_file=str(self.path))

    # -- reading ---------------------------------------------------------

    @property
    def last_press(self) -> dict[str, Any] | None:
        value = self._data.get("last_press")
        return copy.deepcopy(value) if isinstance(value, dict) else None

    @property
    def last_status(self) -> dict[str, Any] | None:
        value = self._data.get("last_status")
        return copy.deepcopy(value) if isinstance(value, dict) else None

    @property
    def last_timer(self) -> dict[str, Any] | None:
        """The last ``timer`` forwarded to the button (§8), if still relevant."""
        value = self._data.get("last_timer")
        return copy.deepcopy(value) if isinstance(value, dict) else None

    @property
    def push_registration(self) -> dict[str, Any] | None:
        """``{"app_id", "token"}`` of the app that registered for FCM (§4.10)."""
        value = self._data.get("push")
        return copy.deepcopy(value) if _valid_push(value) else None

    def set_push(self, app_id: str, token: str) -> None:
        """Remember the token of the (single, MVP) app registration."""
        current = self._data.get("push")
        if isinstance(current, dict) and current.get("app_id") == app_id:
            if current.get("token") == token:
                return  # idempotent: re-sent on every connect
        self._data["push"] = {"app_id": app_id, "token": token}
        self._save()

    def clear_push(self, app_id: str | None = None) -> None:
        """Drop the registration, optionally only if it belongs to ``app_id``."""
        current = self._data.get("push")
        if not _valid_push(current):
            return
        if app_id is not None and current["app_id"] != app_id:
            return
        self._data["push"] = None
        self._save()

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    @staticmethod
    def _seq_key(role: Role, sender_id: str) -> str:
        return f"{role.value}:{sender_id}"

    def last_seq(self, role: Role, sender_id: str) -> int | None:
        value = self._data["last_seq"].get(self._seq_key(role, sender_id))
        return value if isinstance(value, int) else None

    def is_duplicate(self, role: Role, sender_id: str, seq: int) -> bool:
        last = self.last_seq(role, sender_id)
        return last is not None and seq <= last

    # -- writing ---------------------------------------------------------

    def record_press(self, frame: dict[str, Any]) -> None:
        self._data["last_press"] = copy.deepcopy(frame)
        self._save()

    def record_timer(self, frame: dict[str, Any] | None) -> None:
        """Remember the forwarded ``timer``; ``None`` clears it (cancel/arrival)."""
        self._data["last_timer"] = copy.deepcopy(frame)
        self._save()

    def record_status(self, frame: dict[str, Any]) -> None:
        self._data["last_status"] = copy.deepcopy(frame)
        self._save()

    def mark_seq(self, role: Role, sender_id: str, seq: int) -> None:
        self._data["last_seq"][self._seq_key(role, sender_id)] = seq
        self._save()

    def reset_seq(self, role: Role, sender_id: str) -> None:
        """Forget a sender's watermark (client restarted its counter)."""
        if self._data["last_seq"].pop(self._seq_key(role, sender_id), None) is not None:
            self._save()

    def _save(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            log_event("state_save_failed", level=logging.ERROR, error=str(exc))
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
