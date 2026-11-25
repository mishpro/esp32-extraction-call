"""RFC 6455 ядро (device/frames.py) на CPython.

Покрытие: маскирование (включая вектор из RFC 6455 §5.7), границы длин
7/16/64 бита, неполный буфер, нарушение протокола, сборка фрагментов,
ping -> pong, close.
"""

import ast
import pathlib

import frames
import pytest

# Ключ из примера RFC 6455 §5.7: "Hello" -> 7f 9f 4d 51 58.
KEY = b"\x37\xfa\x21\x3d"


def _frame(opcode, payload, fin=True, masked=True):
    """Готовый кадр, разобранный parse_frame (как его увидит клиент)."""
    raw = frames.build_frame(opcode, payload, KEY if masked else None, fin=fin)
    return frames.parse_frame(bytearray(raw))


# --- маскирование ---------------------------------------------------------

def test_mask_matches_rfc6455_example():
    assert frames.mask(b"Hello", KEY) == b"\x7f\x9f\x4d\x51\x58"


def test_mask_is_involution():
    data = bytes(range(64))
    assert frames.mask(frames.mask(data, KEY), KEY) == data


def test_mask_empty_payload():
    assert frames.mask(b"", KEY) == b""


def test_mask_requires_4_byte_key():
    with pytest.raises(ValueError):
        frames.mask(b"abc", b"\x00\x01")


def test_masked_frame_does_not_contain_plaintext():
    raw = frames.build_frame(frames.OP_TEXT, b"secret", KEY)
    assert b"secret" not in raw
    assert raw[1] & 0x80  # MASK=1 у клиентского кадра


def test_unmasked_frame_has_no_mask_bit():
    raw = frames.build_frame(frames.OP_TEXT, b"hi")
    assert raw[1] & 0x80 == 0
    assert raw[2:] == b"hi"


# --- границы длин ---------------------------------------------------------

@pytest.mark.parametrize("size", [0, 1, 125, 126, 127, 65535, 65536])
def test_length_boundaries_roundtrip(size):
    payload = bytes(i & 0xFF for i in range(size))
    raw = frames.build_frame(frames.OP_TEXT, payload, KEY)
    fin, opcode, got, consumed = frames.parse_frame(bytearray(raw))
    assert fin is True
    assert opcode == frames.OP_TEXT
    assert got == payload
    assert consumed == len(raw)


def test_header_7bit_length():
    raw = frames.build_frame(frames.OP_TEXT, b"a" * 125, KEY)
    assert raw[0] == 0x81
    assert raw[1] == 0x80 | 125
    assert raw[2:6] == KEY  # 7-битная длина: маска сразу после 2 байт


def test_fin_false_masked_header():
    # Регрессия: бит MASK (0x80) живёт во втором байте; в первом — только FIN.
    raw = frames.build_frame(frames.OP_TEXT, b"a", KEY, fin=False)
    assert raw[0] == 0x01
    assert raw[1] == 0x80 | 1


def test_header_16bit_length():
    raw = frames.build_frame(frames.OP_BINARY, b"a" * 126, KEY)
    assert raw[1] == 0x80 | 126
    assert int.from_bytes(raw[2:4], "big") == 126
    assert raw[4:8] == KEY


def test_header_64bit_length():
    raw = frames.build_frame(frames.OP_BINARY, b"a" * 65536, KEY)
    assert raw[1] == 0x80 | 127
    assert int.from_bytes(raw[2:10], "big") == 65536
    assert raw[10:14] == KEY


def test_65535_fits_16bit_branch():
    raw = frames.build_frame(frames.OP_BINARY, b"a" * 65535)
    assert raw[1] == 126
    assert int.from_bytes(raw[2:4], "big") == 65535


@pytest.mark.parametrize("size", [1, 125, 126, 65535, 65536])
def test_incomplete_buffer_returns_none(size):
    raw = frames.build_frame(frames.OP_BINARY, b"x" * size, KEY)
    for cut in range(len(raw)):
        assert frames.parse_frame(raw[:cut]) is None


def test_two_frames_in_one_buffer():
    one = frames.build_frame(frames.OP_TEXT, b"a", KEY)
    two = frames.build_frame(frames.OP_TEXT, b"bb", KEY)
    buf = bytearray(one + two)

    fin, opcode, payload, consumed = frames.parse_frame(buf)
    assert (fin, opcode, payload) == (True, frames.OP_TEXT, b"a")
    assert consumed == len(one)

    fin, opcode, payload, consumed2 = frames.parse_frame(buf[consumed:])
    assert (fin, opcode, payload) == (True, frames.OP_TEXT, b"bb")
    assert consumed + consumed2 == len(buf)


# --- векторы из RFC 6455 §5.7 (независимая проверка, не из нашего кода) ----

def test_rfc_single_frame_unmasked_text():
    assert frames.build_frame(frames.OP_TEXT, b"Hello") == b"\x81\x05Hello"


def test_rfc_single_frame_masked_text():
    wire = frames.build_frame(frames.OP_TEXT, b"Hello", KEY)
    assert wire == b"\x81\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58"


def test_rfc_fragmented_unmasked_hello():
    frags = frames.Fragments()
    assert frames.feed(frags, frames.parse_frame(bytearray(b"\x01\x03Hel"))) is None
    assert frames.feed(frags, frames.parse_frame(bytearray(b"\x80\x02lo"))) == (
        frames.KIND_MESSAGE,
        frames.OP_TEXT,
        b"Hello",
    )


def test_rfc_ping_and_pong_vectors():
    assert frames.parse_frame(bytearray(b"\x89\x05Hello")) == (
        True,
        frames.OP_PING,
        b"Hello",
        7,
    )
    assert frames.build_frame(frames.OP_PONG, b"Hello", KEY) == (
        b"\x8a\x85\x37\xfa\x21\x3d\x7f\x9f\x4d\x51\x58"
    )


def test_rfc_256_byte_binary_uses_16bit_length():
    payload = bytes(i & 0xFF for i in range(256))
    wire = frames.build_frame(frames.OP_BINARY, payload)
    assert wire[:4] == b"\x82\x7e\x01\x00"
    assert frames.parse_frame(bytearray(wire))[2] == payload


def test_rfc_65536_byte_binary_uses_64bit_length():
    payload = b"\x7f" * 65536
    wire = frames.build_frame(frames.OP_BINARY, payload)
    assert wire[:10] == b"\x82\x7f\x00\x00\x00\x00\x00\x01\x00\x00"
    assert frames.parse_frame(bytearray(wire))[2] == payload


# --- нарушения протокола --------------------------------------------------

def test_rsv_bits_rejected():
    buf = bytearray(frames.build_frame(frames.OP_TEXT, b"a", KEY))
    buf[0] |= 0x40
    with pytest.raises(ValueError):
        frames.parse_frame(buf)


def test_unknown_opcode_rejected_on_parse():
    buf = bytearray(frames.build_frame(frames.OP_TEXT, b"a"))
    buf[0] = 0x83  # FIN + opcode 0x3
    with pytest.raises(ValueError):
        frames.parse_frame(buf)


def test_fragmented_control_frame_rejected():
    buf = bytearray(frames.build_frame(frames.OP_PING, b""))
    buf[0] &= 0x7F  # снять FIN
    with pytest.raises(ValueError):
        frames.parse_frame(buf)


def test_control_payload_over_125_rejected_on_parse():
    buf = bytearray((0x89, 126)) + (126).to_bytes(2, "big") + b"x" * 126
    with pytest.raises(ValueError):
        frames.parse_frame(buf)


def test_64bit_length_with_msb_set_rejected():
    buf = bytearray((0x82, 0x7F)) + (1 << 63).to_bytes(8, "big") + b"x"
    with pytest.raises(ValueError):
        frames.parse_frame(buf)


def test_max_payload_guard():
    raw = frames.build_frame(frames.OP_BINARY, b"x" * 200, KEY)
    assert frames.parse_frame(bytearray(raw), max_payload=200) is not None
    with pytest.raises(ValueError):
        frames.parse_frame(bytearray(raw), max_payload=199)


def test_build_rejects_bad_opcode():
    with pytest.raises(ValueError):
        frames.build_frame(0x3, b"")


def test_build_rejects_str_payload():
    with pytest.raises(TypeError):
        frames.build_frame(frames.OP_TEXT, "text")


def test_build_rejects_bad_mask_key():
    with pytest.raises(ValueError):
        frames.build_frame(frames.OP_TEXT, b"a", b"\x00" * 3)


def test_build_rejects_oversized_control_payload():
    with pytest.raises(ValueError):
        frames.build_frame(frames.OP_PING, b"x" * 126)


def test_build_rejects_fragmented_control():
    with pytest.raises(ValueError):
        frames.build_frame(frames.OP_PING, b"", fin=False)


# --- сборка фрагментов ----------------------------------------------------

def test_single_frame_message():
    out = frames.feed(frames.Fragments(), _frame(frames.OP_TEXT, b"hello"))
    assert out == (frames.KIND_MESSAGE, frames.OP_TEXT, b"hello")


def test_fragmented_text():
    frags = frames.Fragments()
    parts = [b"he", b"ll", b"o"]
    events = []
    for i, part in enumerate(parts):
        opcode = frames.OP_TEXT if i == 0 else frames.OP_CONT
        fin = i == len(parts) - 1
        events.append(frames.feed(frags, _frame(opcode, part, fin)))
    assert events[0] is None
    assert events[1] is None
    assert events[2] == (frames.KIND_MESSAGE, frames.OP_TEXT, b"hello")


def test_fragmented_binary_keeps_opcode():
    frags = frames.Fragments()
    assert frames.feed(frags, _frame(frames.OP_BINARY, b"\x00\x01", fin=False)) is None
    assert frames.feed(frags, _frame(frames.OP_CONT, b"\x02", fin=True)) == (
        frames.KIND_MESSAGE,
        frames.OP_BINARY,
        b"\x00\x01\x02",
    )


def test_empty_continuation_frames():
    frags = frames.Fragments()
    assert frames.feed(frags, _frame(frames.OP_BINARY, b"ab", fin=False)) is None
    assert frames.feed(frags, _frame(frames.OP_CONT, b"", fin=False)) is None
    assert frames.feed(frags, _frame(frames.OP_CONT, b"", fin=True)) == (
        frames.KIND_MESSAGE,
        frames.OP_BINARY,
        b"ab",
    )


def test_ping_between_fragments_does_not_break_reassembly():
    frags = frames.Fragments()
    assert frames.feed(frags, _frame(frames.OP_TEXT, b"ab", fin=False)) is None
    assert frames.feed(frags, _frame(frames.OP_PING, b"nox")) == (
        frames.KIND_PING,
        frames.OP_PING,
        b"nox",
    )
    assert frames.feed(frags, _frame(frames.OP_CONT, b"cd", fin=True)) == (
        frames.KIND_MESSAGE,
        frames.OP_TEXT,
        b"abcd",
    )


def test_continuation_without_start_rejected():
    with pytest.raises(ValueError):
        frames.feed(frames.Fragments(), _frame(frames.OP_CONT, b"x"))


def test_new_data_frame_mid_message_rejected():
    frags = frames.Fragments()
    assert frames.feed(frags, _frame(frames.OP_TEXT, b"a", fin=False)) is None
    with pytest.raises(ValueError):
        frames.feed(frags, _frame(frames.OP_TEXT, b"b"))


def test_accumulated_message_size_limit():
    frags = frames.Fragments(max_size=8)
    assert frames.feed(frags, _frame(frames.OP_BINARY, b"12345", fin=False)) is None
    with pytest.raises(ValueError):
        frames.feed(frags, _frame(frames.OP_CONT, b"6789", fin=True))
    # после ошибки состояние сброшено — следующий кадр принимается
    assert frames.feed(frags, _frame(frames.OP_TEXT, b"ok")) == (
        frames.KIND_MESSAGE,
        frames.OP_TEXT,
        b"ok",
    )


def test_single_frame_over_limit_rejected():
    frags = frames.Fragments(max_size=8)
    with pytest.raises(ValueError):
        frames.feed(frags, _frame(frames.OP_BINARY, b"x" * 9))


def test_buffer_is_reused_between_messages():
    frags = frames.Fragments()
    frames.feed(frags, _frame(frames.OP_TEXT, b"first"))
    buf_id = id(frags.buf)
    frames.feed(frags, _frame(frames.OP_TEXT, b"second"))
    assert id(frags.buf) == buf_id
    assert bytes(frags.buf) == b""


# --- ping / pong / close --------------------------------------------------

def test_ping_payload_echoed_in_pong():
    kind, opcode, payload = frames.feed(frames.Fragments(), _frame(frames.OP_PING, b"seq-42"))
    assert (kind, opcode, payload) == (frames.KIND_PING, frames.OP_PING, b"seq-42")

    pong_raw = frames.build_frame(frames.OP_PONG, payload, KEY)
    fin, op, pong_payload, _ = frames.parse_frame(bytearray(pong_raw))
    assert (fin, op, pong_payload) == (True, frames.OP_PONG, b"seq-42")


def test_pong_from_server_is_reported():
    assert frames.feed(frames.Fragments(), _frame(frames.OP_PONG, b"x")) == (
        frames.KIND_PONG,
        frames.OP_PONG,
        b"x",
    )


def test_close_with_code_and_reason():
    raw = frames.build_close(1000, "bye", KEY)
    frame = frames.parse_frame(bytearray(raw))
    fin, opcode, payload, consumed = frame
    assert fin is True
    assert opcode == frames.OP_CLOSE
    assert consumed == len(raw)
    assert frames.close_code(payload) == 1000
    assert frames.close_reason(payload) == "bye"
    assert frames.feed(frames.Fragments(), frame) == (
        frames.KIND_CLOSE,
        frames.OP_CLOSE,
        payload,
    )


def test_close_without_payload():
    payload = frames.parse_frame(bytearray(frames.build_close(None, mask_key=KEY)))[2]
    assert payload == b""
    assert frames.close_code(payload) is None
    assert frames.close_reason(payload) == ""


def test_close_code_4401_auth_failed():
    payload = frames.parse_frame(bytearray(frames.build_close(4401, "auth", KEY)))[2]
    assert frames.close_code(payload) == 4401
    assert frames.close_reason(payload) == "auth"


def test_one_byte_close_payload_rejected():
    with pytest.raises(ValueError):
        frames.feed(frames.Fragments(), (True, frames.OP_CLOSE, b"\x03", 3))


def test_undecodable_close_reason_is_empty():
    assert frames.close_reason(b"\x03\xe8\xff\xfe") == ""


def test_build_close_rejects_bad_code():
    with pytest.raises(ValueError):
        frames.build_close(999, mask_key=KEY)


def test_feed_rejects_unknown_opcode():
    with pytest.raises(ValueError):
        frames.feed(frames.Fragments(), (True, 0x3, b"", 0))


# --- требование §7: ядро без uV-импортов ---------------------------------

def test_frames_module_imports_nothing():
    src = pathlib.Path(frames.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == set(), f"frames.py должен быть без импортов, найдено: {imported!r}"
