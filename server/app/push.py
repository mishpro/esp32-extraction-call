"""FCM delivery of a press (protocol v1.1 §4.10, S6).

The WebSocket stays the primary channel: the server forwards ``press`` to the
app as before and *additionally* sends the same press as an FCM data message,
so a call still arrives when the app process is gone. Delivery is therefore
at-least-once and the app dedups by ``(from, seq, t_epoch)``; the ``ack`` still
travels over the WebSocket only, so "delivered" keeps meaning "the app took it".

The module is deliberately small: Firebase Admin SDK would pull in gRPC and
protobuf for one HTTP POST. ``google-auth`` turns the service-account JSON into
an OAuth token, ``httpx`` performs the call, and tests inject a fake sender.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
FCM_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class PushResult:
    ok: bool
    #: FCM says the token is dead (app uninstalled / data cleared): drop it.
    unregistered: bool = False
    #: Short, token-free reason for the log.
    error: str | None = None


class PushSender(Protocol):
    async def send(self, token: str, data: dict[str, str]) -> PushResult: ...

    async def close(self) -> None: ...


def _short(value: object, limit: int = 120) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _unregistered(response: httpx.Response) -> bool:
    """FCM v1 reports a dead token as 404 with errorCode UNREGISTERED."""
    if response.status_code == 404:
        return True
    try:
        body: Any = response.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    if isinstance(details, list):
        return any(
            isinstance(item, dict) and item.get("errorCode") == "UNREGISTERED"
            for item in details
        )
    return False


class FcmPushSender:
    """Send data-only messages through FCM HTTP v1 (§4.10)."""

    def __init__(
        self,
        credentials: Path,
        *,
        project_id: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.credentials_path = Path(credentials)
        try:
            info = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"cannot read FCM credentials: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("FCM credentials are not valid JSON") from exc
        if not isinstance(info, dict):
            raise ValueError("FCM credentials must be a JSON object")
        self.project_id = project_id or info.get("project_id")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise ValueError("FCM credentials have no project_id")
        self._credentials = info
        self._token: str | None = None
        self._expiry = 0.0
        self._signer: Any = None
        self._token_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    # -- OAuth -----------------------------------------------------------

    async def _fetch_token(self) -> tuple[str, float]:
        """Service-account JWT -> OAuth access token, without the requests transport.

        ``google-auth`` picks its transport lazily, and the default one needs
        ``requests``; the server already speaks HTTP through httpx, so the
        standard jwt-bearer grant is issued here directly.
        """
        from google.auth import jwt as google_jwt
        from google.auth.crypt import RSASigner

        if self._signer is None:
            self._signer = RSASigner.from_service_account_info(self._credentials)
        now = int(time.time())
        assertion = google_jwt.encode(
            self._signer,
            {
                "iss": self._credentials["client_email"],
                "scope": FCM_SCOPE,
                "aud": TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
        )
        # google-auth returns bytes; the form value must be the JWT text.
        if isinstance(assertion, bytes):
            assertion = assertion.decode("ascii")
        response = await self._client.post(
            TOKEN_URL,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"token endpoint returned {response.status_code}: {_short(response.text, 200)}"
            )
        body = response.json()
        lifetime = float(body.get("expires_in", 3600))
        return body["access_token"], time.time() + lifetime - 60

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and time.time() < self._expiry:
                return self._token
            token, expiry = await self._fetch_token()
            self._token, self._expiry = token, expiry
            return token

    # -- sending ---------------------------------------------------------

    async def send(self, token: str, data: dict[str, str]) -> PushResult:
        if not token:
            return PushResult(ok=False, error="empty token")
        try:
            access = await self._access_token()
        except Exception as exc:  # noqa: BLE001 - surfaced as a log line
            self._token = None
            self._expiry = 0.0
            return PushResult(ok=False, error=f"auth: {_short(exc)}")

        body = {
            "message": {
                "token": token,
                # FCM requires every data value to be a string.
                "data": {key: str(value) for key, value in data.items()},
                "android": {"priority": "high"},
            }
        }
        url = FCM_URL.format(project=self.project_id)
        try:
            response = await self._client.post(
                url, json=body, headers={"Authorization": f"Bearer {access}"}
            )
        except httpx.HTTPError as exc:
            return PushResult(ok=False, error=f"http: {_short(exc)}")

        if response.status_code == 200:
            return PushResult(ok=True)
        if response.status_code == 401:
            # Stale OAuth token: refresh on the next attempt.
            self._token = None
            self._expiry = 0.0
        if _unregistered(response):
            return PushResult(ok=False, unregistered=True, error="UNREGISTERED")
        text = response.text or ""
        return PushResult(
            ok=False,
            error=f"status {response.status_code}: {_short(text)}",
        )

    async def close(self) -> None:
        await self._client.aclose()
