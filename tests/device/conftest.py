"""Общая настройка тестов ядра кнопки.

device/ — не пакет (файлы копируются на плату по отдельности), поэтому
добавляем каталог в sys.path, чтобы `import frames` / `import proto`
работали и из pytest, и из проверки импорта.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICE_DIR = REPO_ROOT / "device"

if str(DEVICE_DIR) not in sys.path:
    sys.path.insert(0, str(DEVICE_DIR))
