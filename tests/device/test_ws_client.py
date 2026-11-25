"""Tests for device/ws_client.py (handshake §7.1, backoff §6, socket I/O)."""

import base64
import hashlib
import socket
import threading

import frames
import pytest
import ws_client

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ---------------------------------------------------------------------------
# handshake
# ---------------------------------------------------------------------------


def test_accept_key_rfc6455_vector():
    # RFC 6455 §1.3: the canonical example.
    assert (
        ws_client.accept_key("dGhlIHNhbXBsZSBub25jZQ==")
        == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    )


def test_b64_has_no_trailing_newline():
    assert ws_client.b64(b"x") == "eA=="


def _head(key, status="101 Switching Protocols", upgrade="websocket", accept=None):
    if accept is None:
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
    return (
        "HTTP/1.1 " + status + "\r\n"
        "Upgrade: " + upgrade + "\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n"
    ).encode()


def test_check_handshake_ok():
    key = b"0123456789abcdef"
    headers = ws_client.check_handshake(_head(key), key)
    assert headers["upgrade"] == "websocket"


def test_check_handshake_header_case_insensitive():
    key = b"0123456789abcdef"
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
    head = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "UPGRADE: WebSocket\r\n"
        "SEC-WEBSOCKET-ACCEPT: " + accept + "\r\n"
    ).encode()
    assert ws_client.check_handshake(head, key)["upgrade"] == "WebSocket"


def test_check_handshake_rejects_non_101():
    key = b"0123456789abcdef"
    with pytest.raises(ws_client.WsError):
        ws_client.check_handshake(_head(key, status="400 Bad Request"), key)


def test_check_handshake_rejects_missing_upgrade():
    key = b"0123456789abcdef"
    with pytest.raises(ws_client.WsError):
        ws_client.check_handshake(_head(key, upgrade="h2c"), key)


def test_check_handshake_rejects_wrong_accept():
    key = b"0123456789abcdef"
    with pytest.raises(ws_client.WsError):
        ws_client.check_handshake(_head(key, accept="AAAAAAAAAAAAAAAAAAAAAAAAAAA="), key)


# ---------------------------------------------------------------------------
# backoff (§6)
# ---------------------------------------------------------------------------


def test_backoff_attempt_zero_and_one():
    assert ws_client.backoff_s(0, rnd=lambda: 0.5) == 1.0
    assert ws_client.backoff_s(1, rnd=lambda: 0.5) == 2.0
    assert ws_client.backoff_s(2, rnd=lambda: 0.5) == 4.0
    assert ws_client.backoff_s(3, rnd=lambda: 0.5) == 8.0


def test_backoff_jitter_is_twenty_percent():
    assert ws_client.backoff_s(0, rnd=lambda: 0.0) == pytest.approx(0.8)
    assert ws_client.backoff_s(0, rnd=lambda: 1.0) == pytest.approx(1.2)


def test_backoff_never_exceeds_active_cap():
    for attempt in range(20):
        for r in (0.0, 0.5, 1.0):
            delay = ws_client.backoff_s(attempt, cap_s=10.0, rnd=lambda r=r: r)
            assert 0 < delay <= 10.0


def test_backoff_idle_cap_is_sixty():
    assert ws_client.backoff_s(6, cap_s=60.0, rnd=lambda: 1.0) == pytest.approx(60.0)
    assert ws_client.backoff_s(9, cap_s=60.0, rnd=lambda: 0.0) == pytest.approx(60.0)


def test_backoff_handles_negative_and_huge_attempts():
    assert ws_client.backoff_s(-5, rnd=lambda: 0.5) == 1.0
    assert ws_client.backoff_s(10_000, cap_s=10.0, rnd=lambda: 0.0) == 10.0


# ---------------------------------------------------------------------------
# real socket round-trip against a tiny in-process server
# ---------------------------------------------------------------------------


def _serve(listener, seen):
    conn, _ = listener.accept()
    _serve_connection(conn, seen)


def _serve_connection(conn, seen):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    key = b""
    for line in head.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"sec-websocket-key":
            key = value.strip()
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
    conn.sendall(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
        ).encode()
    )

    data = bytearray(rest)
    frame = None
    while frame is None:
        if len(data) >= 2:
            frame = frames.parse_frame(data)
        if frame is None:
            chunk = conn.recv(512)
            if not chunk:
                return
            data.extend(chunk)
    _, opcode, payload, used = frame
    seen.append((opcode, bytes(payload)))

    conn.sendall(frames.build_frame(frames.OP_TEXT, b"echo:" + payload))
    conn.sendall(frames.build_frame(frames.OP_PING, b"hb"))

    # The client answers the ping on its own.
    data = data[used:]
    frame = None
    conn.settimeout(3)
    while frame is None:
        if len(data) >= 2:
            frame = frames.parse_frame(data)
        if frame is None:
            chunk = conn.recv(512)
            if not chunk:
                return
            data.extend(chunk)
    seen.append((frame[1], bytes(frame[2])))

    conn.sendall(frames.build_close(1000, "bye"))
    conn.close()


def test_socket_handshake_text_ping_pong_and_close():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    seen = []
    thread = threading.Thread(target=_serve, args=(listener, seen))
    thread.start()

    client = ws_client.WsClient("127.0.0.1", port, timeout=3)
    client.connect()
    try:
        client.send_text("hi")
        event = client.read_event(3000)
        assert event[0] == frames.KIND_MESSAGE
        assert event[2] == b"echo:hi"

        event = client.read_event(3000)
        assert event[0] == frames.KIND_PING
        assert event[2] == b"hb"

        event = client.read_event(3000)
        assert event[0] == frames.KIND_CLOSE
    finally:
        client.close()
        thread.join(3)
        listener.close()

    assert not thread.is_alive()
    assert seen[0][0] == frames.OP_TEXT
    assert seen[0][1] == b"hi"
    assert seen[1][0] == frames.OP_PONG
    assert seen[1][1] == b"hb"


def test_send_text_rejects_oversized_message():
    client = ws_client.WsClient("127.0.0.1", 1)
    with pytest.raises(ValueError):
        client.send_text("x" * (frames.MAX_MESSAGE_BYTES + 1))


def test_read_event_returns_buffered_frame_without_waiting():
    client = ws_client.WsClient("127.0.0.1", 1)
    client.connected = True
    client.sock = None
    client.buf.extend(frames.build_frame(frames.OP_TEXT, b"buffered"))
    event = client.read_event(0)
    assert event == (frames.KIND_MESSAGE, frames.OP_TEXT, b"buffered")


# ---------------------------------------------------------------------------
# handshake failures and coalesced bytes
# ---------------------------------------------------------------------------


def _read_request(conn):
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(512)
        if not chunk:
            return b"", b""
        buf += chunk
    return buf.partition(b"\r\n\r\n")[0], buf.partition(b"\r\n\r\n")[2]


def _key_from(head):
    for line in head.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"sec-websocket-key":
            return value.strip()
    return b""


def _serve_status(listener, status):
    conn, _ = listener.accept()
    _read_request(conn)
    conn.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\n\r\n")
    conn.close()


def _serve_with_early_frame(listener):
    conn, _ = listener.accept()
    head, _rest = _read_request(conn)
    key = _key_from(head)
    accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
    ).encode()
    conn.sendall(response + frames.build_frame(frames.OP_TEXT, b"early"))
    conn.close()


def _start(target, args):
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    thread = threading.Thread(target=target, args=(listener,) + args)
    thread.start()
    return listener, listener.getsockname()[1], thread


def test_connect_rejects_non_101():
    listener, port, thread = _start(_serve_status, (b"400 Bad Request",))
    client = ws_client.WsClient("127.0.0.1", port, timeout=3)
    try:
        with pytest.raises(ws_client.WsError):
            client.connect()
    finally:
        client.close()
        thread.join(3)
        listener.close()
    assert not client.connected


def test_connect_keeps_frame_bytes_after_headers():
    listener, port, thread = _start(_serve_with_early_frame, ())
    client = ws_client.WsClient("127.0.0.1", port, timeout=3)
    try:
        client.connect()
        event = client.read_event(0)
        assert event == (frames.KIND_MESSAGE, frames.OP_TEXT, b"early")
    finally:
        client.close()
        thread.join(3)
        listener.close()
