#!/usr/bin/env python3
"""Сверка JSON-схемы с примерами из drafts/Протокол.md.

Извлекает все блоки ```json из документа, валидирует каждый против
plan/schema/messages.schema.json и печатает отчёт.

Запуск:
    cd /home/mish/personal_devel/esp32-extraction-call
    uv run --with jsonschema python plan/schema/validate_examples.py

Код возврата: 0 — все примеры валидны, 1 — есть расхождения.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_MD = ROOT / "drafts" / "Протокол.md"
SCHEMA = Path(__file__).with_name("messages.schema.json")

FENCE_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL)


def extract_examples(text: str) -> list[tuple[int, dict]]:
    """Возвращает (номер, объект) для каждого JSON-объекта.

    Блок §4.8 Протокола содержит два объекта (ping и pong) в одном fence —
    поэтому блок разбирается посимвольно через raw_decode, а не json.loads.
    """
    decoder = json.JSONDecoder()
    out: list[tuple[int, dict]] = []
    for block_no, block in enumerate(FENCE_RE.findall(text), start=1):
        idx = 0
        while idx < len(block):
            while idx < len(block) and block[idx] in " \t\r\n":
                idx += 1
            if idx >= len(block):
                break
            try:
                obj, end = decoder.raw_decode(block, idx)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Блок #{block_no}: невалидный JSON — {exc}")
            out.append((len(out) + 1, obj))
            idx = end
    return out


def main() -> int:
    if not PROTOCOL_MD.exists():
        raise SystemExit(f"Не найден {PROTOCOL_MD}")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    try:
        import jsonschema
    except ImportError:
        raise SystemExit("Нужен пакет jsonschema: uv run --with jsonschema python plan/schema/validate_examples.py")

    validator = jsonschema.Draft202012Validator(schema)
    examples = extract_examples(PROTOCOL_MD.read_text(encoding="utf-8"))

    failures = 0
    for idx, msg in examples:
        errors = sorted(validator.iter_errors(msg), key=lambda e: list(e.path))
        t = msg.get("t", "?")
        if errors:
            failures += 1
            print(f"[FAIL] #{idx:>2} t={t:<9} {errors[0].message}")
            for err in errors[1:]:
                print(f"       - {err.message}")
        else:
            print(f"[ OK ] #{idx:>2} t={t:<9} ok")

    print(f"\nвсего примеров: {len(examples)}, расхождений: {failures}")
    if failures:
        print("Расхождения — источник правды drafts/Протокол.md; правится документ, схема следом.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
