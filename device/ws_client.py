"""Minimal WebSocket client for MicroPython (Протокол §7).

Only what the button needs: RFC 6455 client handshake (§7.1), masked text
frames with ping/pong and close (§7.2), blocking-with-deadline reads via
``select.poll()`` (§7.3) and the §6 reconnect delay.

Frame/payload codecs live in ``frames.py``; JSON message builders and the
incoming-message validator live in ``proto.py``. This module knows nothing
about Guidecaller message types — it only moves text frames.

Usage::

    client = WsClient("192.168.1.10", 8000)
    client.connect()
    client.send_text(proto.build_hello("device", token, device_id, fw))

    while True:
        event = client.read_event(100)        # 100 ms
        if event is None:
            ...                               # buttons, timers, ping
        elif event[0] == frames.KIND_MESSAGE:
            message = proto.parse_message(event[2])
        elif event[0] == frames.KIND_PING:
            pass                              # pong is already sent
        elif event[0] == frames.KIND_CLOSE:
            client.close()
            break

Imports are plain stdlib names (``socket``, ``select``, ``hashlib``,
``binascii``) that exist both in MicroPython and CPython, so the same file can
be exercised against a real server on the workstation before it is copied to
the board.
"""

import binascii
import hashlib
import os
import random
import select
import socket
import time

import frames

WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
RECV_CHUNK = 512
MAX_TEXT_BYTES = frames.MAX_MESSAGE_BYTES

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:  # CPython
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b

# lwIP on the ESP32 raises EAGAIN as 11; the others are CPython/macOS/Windows.
try:
    from errno import EAGAIN
except ImportError:  # pragma: no cover - MicroPython always has errno
    EAGAIN = 11
_AGAIN = (EAGAIN, 11, 35, 10035)


class WsError(Exception):
    """Handshake failed or the peer violated the WebSocket protocol."""


class WsClosed(WsError):
    """The TCP connection was closed or became unusable."""


# ---------------------------------------------------------------------------
# pure helpers (unit-tested on CPython)
# ---------------------------------------------------------------------------


def b64(data):
    """Base64 without the trailing newline added by ``b2a_base64``."""
    return binascii.b2a_base64(data).strip().decode()


def accept_key(key):
    """``Sec-WebSocket-Accept`` for a client key (§7.1, RFC 6455 §4.2.2).

    ``key`` is the value of the ``Sec-WebSocket-Key`` header (the base64
    string), as bytes or str.
    """
    if isinstance(key, str):
        key = key.encode("utf-8")
    return b64(hashlib.sha1(key + WS_GUID).digest())


def check_handshake(head, key):
    """Validate the HTTP response head (bytes, without the final CRLFCRLF).

    Raises WsError unless the status is 101, ``Upgrade: websocket`` is present
    and ``Sec-WebSocket-Accept`` matches the key. Header names are compared
    case-insensitively.
    """
    text = head.decode("utf-8") if isinstance(head, (bytes, bytearray)) else head
    lines = text.split("\r\n")
    status = lines[0].split(" ")
    if len(status) < 2 or status[1] != "101":
        raise WsError("handshake status: " + lines[0])
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    if headers.get("upgrade", "").lower() != "websocket":
        raise WsError("missing Upgrade: websocket")
    if headers.get("sec-websocket-accept") != accept_key(key):
        raise WsError("bad Sec-WebSocket-Accept")
    return headers


def backoff_s(attempt, cap_s=10.0, rnd=None):
    """Reconnect delay from Протокол §6: min(cap, 2^attempt) ± 20 %.

    ``cap_s`` is 10 s for an active button and 60 s for an idle one / the app.
    ``rnd`` is injectable so tests are deterministic; by default
    ``random.random()``. The result never exceeds ``cap_s``.
    """
    attempt = max(attempt, 0)
    attempt = min(attempt, 30)
    base = 1 << attempt
    r = random.random() if rnd is None else rnd()
    delay = base * (0.8 + 0.4 * r)
    return min(delay, cap_s)


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class WsClient:
    def __init__(self, host, port, path="/ws", timeout=5.0):
        self.host = host
        self.port = port
        self.path = path
        self.timeout = timeout
        self.sock = None
        self.connected = False
        self.buf = bytearray()
        self.frags = frames.Fragments()

    # -- connection ------------------------------------------------------

    def connect(self):
        """TCP connect + client handshake. Raises WsError on any failure."""
        try:
            addr = socket.getaddrinfo(self.host, self.port)[0][-1]
            sock = socket.socket()
            sock.settimeout(self.timeout)
            sock.connect(addr)
        except OSError as exc:
            raise WsError("connect failed: " + str(exc))
        self.sock = sock

        key = b64(os.urandom(16))
        request = (
            "GET " + self.path + " HTTP/1.1\r\n"
            "Host: " + self.host + ":" + str(self.port) + "\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: " + key + "\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        try:
            sock.sendall(request.encode("utf-8"))
            raw = b""
            deadline = ticks_ms() + int(self.timeout * 1000)
            while b"\r\n\r\n" not in raw:
                if ticks_diff(ticks_ms(), deadline) > 0:
                    raise WsError("handshake timeout")
                chunk = sock.recv(RECV_CHUNK)
                if not chunk:
                    raise WsError("connection closed during handshake")
                raw += chunk
        except WsError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise WsError("handshake failed: " + str(exc))

        head, _, rest = raw.partition(b"\r\n\r\n")
        try:
            check_handshake(head, key)
        except WsError:
            self.close()
            raise

        # Frames may already follow the headers in the same TCP segment.
        self.buf = bytearray(rest)
        try:
            sock.settimeout(0)
        except OSError:
            pass
        self.connected = True

    def close(self, code=1000, reason=""):
        """Best-effort close frame, then close the socket. Idempotent."""
        if self.connected and self.sock is not None:
            try:
                self.sock.sendall(frames.build_close(code, reason, os.urandom(4)))
            except OSError:
                pass
        self.connected = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        self.buf[:] = b""
        self.frags.reset()

    # -- sending ---------------------------------------------------------

    def _send_frame(self, opcode, payload):
        if not self.connected or self.sock is None:
            raise WsClosed("not connected")
        data = frames.build_frame(opcode, payload, os.urandom(4))
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self.connected = False
            raise WsClosed("send failed: " + str(exc))

    def send_text(self, text):
        """Send one masked text frame (never fragmented, §7.2)."""
        if isinstance(text, str):
            payload = text.encode("utf-8")
        else:
            payload = bytes(text)
        if len(payload) > MAX_TEXT_BYTES:
            raise ValueError("message too large: " + str(len(payload)))
        self._send_frame(frames.OP_TEXT, payload)

    def send_pong(self, payload):
        self._send_frame(frames.OP_PONG, payload)

    def send_ping(self, payload=b""):
        self._send_frame(frames.OP_PING, payload)

    # -- receiving -------------------------------------------------------

    def _next_event(self):
        """Return one event from the buffer, or None if a frame is incomplete.

        Event: (frames.KIND_MESSAGE, opcode, payload_bytes) for a complete
        text/binary message, or (KIND_PING|KIND_PONG|KIND_CLOSE, opcode,
        payload). Pings are answered with pong here and still returned.
        """
        while self.buf:
            try:
                parsed = frames.parse_frame(self.buf, MAX_TEXT_BYTES)
            except ValueError as exc:
                self.connected = False
                raise WsError("bad frame: " + str(exc))
            if parsed is None:
                return None
            consumed = parsed[3]
            # MicroPython has no `del bytearray[:n]`; a self-slice assignment
            # resizes the same buffer without allocating a new object.
            self.buf[:] = self.buf[consumed:]
            try:
                event = frames.feed(self.frags, parsed)
            except ValueError as exc:
                self.connected = False
                raise WsError("bad message: " + str(exc))
            if event is None:
                continue
            if event[0] == frames.KIND_PING:
                try:
                    self.send_pong(event[2])
                except WsClosed:
                    pass
            return event
        return None

    def read_event(self, timeout_ms=0):
        """Wait up to ``timeout_ms`` for one event; None on timeout.

        Uses poll() and its own deadline (§7.3), so the caller can run the
        button/display/ping timers in the same loop. ``timeout_ms=0`` only
        drains frames that are already buffered.
        """
        deadline = ticks_ms() + timeout_ms
        while True:
            event = self._next_event()
            if event is not None:
                return event
            remaining = ticks_diff(deadline, ticks_ms())
            if remaining <= 0:
                return None
            poller = select.poll()
            poller.register(self.sock, select.POLLIN)
            if not poller.poll(remaining + 1):
                continue
            try:
                chunk = self.sock.recv(RECV_CHUNK)
            except OSError as exc:
                if exc.args and exc.args[0] in _AGAIN:
                    continue
                self.connected = False
                raise WsClosed("recv failed: " + str(exc))
            if not chunk:
                self.connected = False
                raise WsClosed("connection closed")
            self.buf.extend(chunk)
