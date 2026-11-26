"""RFC 6455 framing for the Guidecaller button — pure Python, no imports.

Why no imports: the very same file is copied to the ESP32 and imported on
CPython 3.12 under pytest.  Only the byte arithmetic of RFC 6455 lives here;
sockets, timeouts, handshake and reconnect belong to ws_client.py.

Implemented subset (Протокол §7.2):
  * client frames are masked (MASK=1, 4-byte key, payload XOR key);
  * payload lengths 7 / 7+16 / 7+64 bits;
  * opcodes 0x0 continuation, 0x1 text, 0x2 binary, 0x8 close, 0x9 ping,
    0xA pong;
  * reassembly of fragmented server messages up to FIN=1;
  * ping -> caller sends pong with the same payload; close is reported
    separately so the caller can answer and drop the socket.

Not implemented on purpose: extensions (RSV bits are rejected) and
permessage-deflate.  Outgoing messages are never fragmented — 4096 bytes
always fit in one frame.

Type hints are intentionally absent: PEP 604 unions (`bytes | None`) are
evaluated at import time on some MicroPython builds, so the types are
documented in docstrings instead.  f-strings ARE used: MicroPython supports
them (including `!r` and `:02x`) since 1.17.

Every function is stateless except Fragments; the transport keeps its receive
buffer and one Fragments instance for the whole session (Протокол §7.4).
"""

# --- opcodes ---------------------------------------------------------------

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

VALID_OPCODES = (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG)
DATA_OPCODES = (OP_TEXT, OP_BINARY)
CONTROL_OPCODES = (OP_CLOSE, OP_PING, OP_PONG)

MAX_CONTROL_PAYLOAD = 125
MAX_MESSAGE_BYTES = 4096  # Протокол §1: целое сообщение не больше 4096 байт

# kinds returned by Fragments.feed()
KIND_MESSAGE = "message"
KIND_PING = "ping"
KIND_PONG = "pong"
KIND_CLOSE = "close"


# --- payload masking -------------------------------------------------------

def mask(payload, key):
    """XOR payload with the 4-byte masking key (masking is its own inverse).

    payload: bytes/bytearray, key: exactly 4 bytes.  Returns bytes.
    """
    if len(key) != 4:
        raise ValueError(f"mask key must be 4 bytes, got {len(key)}")
    n = len(payload)
    out = bytearray(n)
    i = 0
    while i < n:
        out[i] = payload[i] ^ key[i & 3]
        i += 1
    return bytes(out)


# --- building --------------------------------------------------------------

def build_frame(opcode, payload, mask_key=None, fin=True):
    """Serialize one frame.

    opcode:   one of VALID_OPCODES.
    payload:  bytes/bytearray/memoryview (empty allowed).
    mask_key: 4 bytes -> masked client frame (MASK=1); None -> unmasked frame
              (needed by tests and by anything speaking the server side).
    fin:      FIN bit; control frames must be unfragmented (fin=True).

    Returns bytes ready for sendall().  Raises ValueError on a bad opcode,
    a control frame longer than 125 bytes / with fin=False, or a mask key of
    the wrong length.
    """
    if opcode not in VALID_OPCODES:
        raise ValueError(f"bad opcode: {opcode!r}")
    if isinstance(payload, str):
        raise TypeError("payload must be bytes, not str")
    payload = bytes(payload)

    if opcode in CONTROL_OPCODES:
        if not fin:
            raise ValueError("control frames cannot be fragmented")
        if len(payload) > MAX_CONTROL_PAYLOAD:
            raise ValueError(
                f"control payload too long: {len(payload)} > {MAX_CONTROL_PAYLOAD}"
            )

    b0 = opcode
    if fin:
        b0 |= 0x80

    masked = mask_key is not None
    if masked and len(mask_key) != 4:
        raise ValueError(f"mask key must be 4 bytes, got {len(mask_key)}")

    # MASK (bit 0x80) живёт во втором байте, рядом с длиной, а не в первом.
    mask_bit = 0x80 if masked else 0x00
    n = len(payload)
    if n <= 125:
        head = bytes((b0, mask_bit | n))
    elif n <= 0xFFFF:
        head = bytes((b0, mask_bit | 126)) + n.to_bytes(2, "big")
    elif n <= 0x7FFFFFFFFFFFFFFF:
        head = bytes((b0, mask_bit | 127)) + n.to_bytes(8, "big")
    else:
        raise ValueError(f"payload too long: {n}")

    if masked:
        return head + bytes(mask_key) + mask(payload, mask_key)
    return head + payload


def build_close(code=1000, reason="", mask_key=None):
    """Build a close frame: 2-byte big-endian code + UTF-8 reason.

    code=None builds an empty close payload (allowed by RFC 6455).
    """
    if code is None:
        payload = b""
    else:
        if not 1000 <= code <= 4999:
            raise ValueError(f"bad close code: {code!r}")
        payload = code.to_bytes(2, "big") + reason.encode("utf-8")
    return build_frame(OP_CLOSE, payload, mask_key)


# --- parsing ---------------------------------------------------------------

def parse_frame(buf, max_payload=None):
    """Parse one frame from the head of buf.

    buf: bytearray/bytes with at least the frame bytes (trailing bytes of the
         next frame are ignored).
    max_payload: optional hard cap on the declared payload length, so a bogus
         length cannot make the caller wait for gigabytes.

    Returns (fin, opcode, payload, consumed) or None if the buffer does not
    hold a whole frame yet (caller reads more bytes and retries with the same
    buffer).  Raises ValueError on protocol violations: RSV bits set, unknown
    opcode, fragmented control frame, control payload > 125, 64-bit length
    with the MSB set, or max_payload exceeded.
    """
    n = len(buf)
    if n < 2:
        return None

    b0 = buf[0]
    b1 = buf[1]

    if b0 & 0x70:
        raise ValueError(f"RSV bits set: 0x{b0:02x}")

    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    if opcode not in VALID_OPCODES:
        raise ValueError(f"bad opcode: 0x{opcode:x}")

    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    pos = 2

    if length == 126:
        if n < pos + 2:
            return None
        length = int.from_bytes(buf[pos:pos + 2], "big")
        pos += 2
    elif length == 127:
        if n < pos + 8:
            return None
        length = int.from_bytes(buf[pos:pos + 8], "big")
        if length >> 63:
            raise ValueError("64-bit length with MSB set")
        pos += 8

    if opcode in CONTROL_OPCODES:
        if not fin:
            raise ValueError("fragmented control frame")
        if length > MAX_CONTROL_PAYLOAD:
            raise ValueError(
                f"control payload too long: {length} > {MAX_CONTROL_PAYLOAD}"
            )

    if max_payload is not None and length > max_payload:
        raise ValueError(f"frame payload {length} > limit {max_payload}")

    key = None
    if masked:
        if n < pos + 4:
            return None
        key = bytes(buf[pos:pos + 4])
        pos += 4

    if n < pos + length:
        return None

    payload = bytes(buf[pos:pos + length])
    if masked:
        payload = mask(payload, key)
    return (fin, opcode, payload, pos + length)


# --- close frame helpers ---------------------------------------------------

def close_code(payload):
    """Close code from a close payload, or None for an empty payload."""
    if len(payload) < 2:
        return None
    return (payload[0] << 8) | payload[1]


def close_reason(payload):
    """UTF-8 reason from a close payload ('' when absent or undecodable)."""
    if len(payload) <= 2:
        return ""
    try:
        return payload[2:].decode("utf-8")
    except UnicodeError:
        # MicroPython и CPython обе бросают UnicodeError на битом UTF-8.
        return ""


# --- reassembly ------------------------------------------------------------

class Fragments:
    """Reassembles fragmented data frames; one instance per connection.

    The buffer is reused between messages (``buf[:] = b""``) so steady-state
    traffic does not allocate a new bytearray per frame (Протокол §7.4).
    """

    __slots__ = ("buf", "max_size", "opcode")

    def __init__(self, max_size=MAX_MESSAGE_BYTES):
        self.opcode = None
        self.buf = bytearray()
        self.max_size = max_size

    def reset(self):
        self.opcode = None
        # MicroPython bytearray has no item deletion (`del buf[:]` raises);
        # slice assignment with a shorter value does resize in place.
        self.buf[:] = b""

    def feed(self, frame):
        """Consume a parsed frame; return (kind, opcode, payload) or None.

        kind is one of KIND_MESSAGE (complete message, payload assembled,
        opcode 0x1/0x2), KIND_PING, KIND_PONG or KIND_CLOSE.  Control frames
        may arrive between fragments and do not disturb the buffer.

        Raises ValueError on a continuation without a started message, a new
        data frame while a message is still incomplete, a message larger than
        max_size, or a close payload of exactly one byte (RFC 6455 forbids it).
        After an error the state is reset.
        """
        fin, opcode, payload, _consumed = frame

        if opcode in CONTROL_OPCODES:
            if opcode == OP_PING:
                return (KIND_PING, OP_PING, payload)
            if opcode == OP_PONG:
                return (KIND_PONG, OP_PONG, payload)
            if len(payload) == 1:
                raise ValueError("close payload must be empty or >= 2 bytes")
            return (KIND_CLOSE, OP_CLOSE, payload)

        if opcode == OP_CONT:
            if self.opcode is None:
                raise ValueError("continuation frame without a started message")
        elif opcode in DATA_OPCODES:
            if self.opcode is not None:
                raise ValueError("new data frame while a message is incomplete")
            self.opcode = opcode
        else:
            # parse_frame отсекает такие opcodes, но feed() должен вести себя
            # предсказуемо и при ручном вызове (тесты, отладка на плате).
            raise ValueError(f"unexpected opcode: 0x{opcode:x}")

        if len(self.buf) + len(payload) > self.max_size:
            self.reset()
            raise ValueError(f"message exceeds {self.max_size} bytes")

        self.buf.extend(payload)
        if not fin:
            return None

        op = self.opcode
        data = bytes(self.buf)
        self.reset()
        return (KIND_MESSAGE, op, data)


def feed(frags, frame):
    """Module-level shortcut for Fragments.feed(frame)."""
    return frags.feed(frame)
