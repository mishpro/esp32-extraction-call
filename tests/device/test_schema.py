"""Сверка ядра с machine-readable схемой (plan/schema/messages.schema.json).

Тест необязательный: jsonschema ставится отдельно
(`uv run --with pytest --with jsonschema pytest tests/device`), иначе модуль
пропускается — ядро на плате схему не проверяет (plan/schema/README.md).
"""

import json
from pathlib import Path

import proto
import pytest
from test_proto import EXAMPLE_IDS, EXAMPLES

pytest.importorskip("jsonschema")

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "plan" / "schema" / "messages.schema.json"


@pytest.fixture(scope="module")
def validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_IDS)
def test_protocol_examples_match_schema(validator, example):
    validator.validate(example)


@pytest.mark.parametrize(
    "raw",
    [
        proto.build_hello("device", "tok", "a1b2c3d4", fw="1.0.0"),
        proto.build_hello("app", "tok", "0f0e0d0c"),
        proto.build_press(7, 1793001234),
        proto.build_ping(1793001600),
        proto.build_pong(1793001600),
        proto.build_ack(7, True, "server"),
        proto.build_ack(7, False, "delivered"),
        proto.build_error("auth_failed", "bad token"),
        proto.build_error("bad_duration", "duration_s out of range", seq=3),
    ],
)
def test_builders_match_schema(validator, raw):
    validator.validate(json.loads(raw))


def test_parser_accepts_every_schema_example():
    for example in EXAMPLES:
        assert proto.parse_message(example)["t"] == example["t"]
