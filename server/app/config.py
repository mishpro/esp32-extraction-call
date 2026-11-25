"""Server configuration from environment variables.

Deliberately does not depend on ``pydantic-settings``: the server reads a small,
fixed set of variables (see ``server/README.md``) and the resulting
:class:`Settings` object can be passed straight into ``create_app()`` from tests.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Role

MAX_FRAME_BYTES = 4096
DEFAULT_STATE_FILE = Path(__file__).resolve().parents[1] / ".state" / "state.json"

_TRUE = {"1", "true", "yes", "on"}


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    return default if raw is None else float(raw)


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    return default if raw is None else int(raw)


def _env_optional(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower()


class Settings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Protocol timing (Протокол §1, §2, §4.8)
    hello_timeout_s: float = Field(default=10.0, gt=0)
    ping_interval_s: float = Field(default=15.0, gt=0)
    pong_timeout_s: float = Field(default=10.0, gt=0)
    max_ping_misses: int = Field(default=3, ge=1)
    max_frame_bytes: int = Field(default=MAX_FRAME_BYTES, gt=0)

    # Authentication (ADR-0011: implemented, disabled in MVP)
    auth_required: bool = False
    device_token_sha256: str | None = None
    app_token_sha256: str | None = None

    # Persistence (Протокол §8, FR-S-13)
    state_file: Path = DEFAULT_STATE_FILE

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        values: dict[str, object] = {
            "host": source.get("HOST", "0.0.0.0"),
            "port": _env_int(source, "PORT", 8000),
            "log_level": source.get("LOG_LEVEL", "INFO"),
            "hello_timeout_s": _env_float(source, "HELLO_TIMEOUT_S", 10.0),
            "ping_interval_s": _env_float(source, "PING_INTERVAL_S", 15.0),
            "pong_timeout_s": _env_float(source, "PONG_TIMEOUT_S", 10.0),
            "max_ping_misses": _env_int(source, "MAX_PING_MISSES", 3),
            "max_frame_bytes": _env_int(source, "MAX_FRAME_BYTES", MAX_FRAME_BYTES),
            "auth_required": _env_bool(source, "AUTH_REQUIRED", False),
            "device_token_sha256": _env_optional(source, "DEVICE_TOKEN_SHA256"),
            "app_token_sha256": _env_optional(source, "APP_TOKEN_SHA256"),
        }
        if "STATE_FILE" in source:
            values["state_file"] = Path(source["STATE_FILE"])
        return cls(**values)

    def token_hash_for(self, role: Role) -> str | None:
        if role is Role.DEVICE:
            return self.device_token_sha256
        return self.app_token_sha256

    def verify_token(self, role: Role, token: str) -> bool:
        """Compare SHA-256(token) with the configured digest.

        Fails closed when no digest is configured for the role: enabling
        ``AUTH_REQUIRED`` without tokens must not silently accept clients.
        """
        expected = self.token_hash_for(role)
        if expected is None:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, expected.lower())
