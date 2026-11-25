# Опрос платы (S0)

Дата: 2026-11-26. Порт: `/dev/ttyUSB0` (CP2102, `10c4:ea60`), доступ по `dialout` есть.

## Чип

| Параметр | Значение |
|---|---|
| Чип | `ESP32-D0WD-V3` (revision v3.1), dual core + LP core, 240 MHz |
| MAC | `44:1d:64:f5:f2:4c` |
| Flash | 4 MB (manufacturer `0x5e`, device `0x4016`) |
| Кристалл | 26 MHz (esptool предупреждает: детект 15.55 MHz — «unsupported crystal») |
| Плата | клон DOIT DevKit V1, 36 выводов, USB-C → CP2102 |

## Прошивка

| Параметр | Значение |
|---|---|
| Образ | `ESP32_GENERIC-20260824-v1.29.0.bin` (1 790 544 Б) |
| Запись | `esptool 5.7.0 --no-stub --baud 115200 write-flash 0x1000` |
| Полный стирание | `--no-stub erase-region 0x0 0x400000` (3.2 с) |
| Проверка | hash verified; `os.uname()` → `v1.29.0 on 2026-08-24` |

### Почему без стиба

Стиб-флешер на этой плате не стартует: `Failed to start stub flasher. There was no response.` (на фоне предупреждения о «неподдерживаемом кристалле»). Следствия:
- `erase-flash` недоступен (ROM-загрузчик его не умеет) → чистим `erase-region 0x0 0x400000`.
- Запись идёт через ROM на 115200 → 1.7 МБ пишутся ~3 минуты. Терпимо, но каждую перепрошивку планировать.
- Без предварительного стирания плата уходит в цикл `The filesystem appears to be corrupted` → **всегда стирать регион ПЗУ перед записью**.

## Периферия

| Проверка | Результат |
|---|---|
| `os.uname()` | `sysname='esp32', release='1.29.0', machine='Generic ESP32 module with ESP32'` |
| heap | 163 440 Б свободно сразу после старта |
| freq | 160 MHz (дефолт uV 1.29) |
| Кнопка GPIO4 (`PULL_UP`) | `1` в покое (нажатие даст `0`) |
| I²C 21/22 (hard, 400 кГц) | `[]` — устройств нет |
| I²C 22/21 (hard и soft) | `[]` — устройств нет |
| Файловая система | пустая, только стоковый `boot.py` (139 Б) |

## Открытый вопрос: экран не отвечает

Скан I²C пуст во всех четырёх вариантах (hard/soft × 21-22/22-21). Значит одно из:
1. модуль не подключён к SDA/SCL (только питание или вообще не подключён);
2. перепутаны провода внутри модуля (тогда скан 21/22 и 22/21 уже покрыт — не помогло);
3. модуль не получает 3.3 В (или питается от 5 В, которого на плате нет на этой линии);
4. модуль не SSD1306/I²C (например, SPI-версия или SH1106 с иным поведением — но SH1106 всё равно отвечает по I²C);
5. модуль неисправен.

Действие: проверить физическое подключение — `VCC`→3V3, `GND`→GND, `SDA`→GPIO21, `SCL`→GPIO22. После подключения повторить:

```bash
venv/bin/mpremote connect /dev/ttyUSB0 exec \
  "from machine import Pin,I2C; print(I2C(0,sda=Pin(21),scl=Pin(22)).scan())"
```

Ожидаем `[60]` (0x3C). Пока экрана нет, S2 можно довести до состояния «кнопка + WS + статус в лог», а отрисовку проверить позже.

## Инструменты на станции

| Что | Где | Версия |
|---|---|---|
| uv | `~/.local/bin/uv` | 0.13.10 |
| esptool | `venv/bin/esptool` (репо) | 5.7.0 |
| mpremote | `venv/bin/mpremote` | 1.29.0 |
| JDK | `~/.local/opt/jdk-17.0.21.1+2` (Temurin, без sudo) | 17.0.21.1 |
| Android cmdline-tools | `~/Android/sdk/cmdline-tools/latest` | 13114758 |
| Прошивка | `~/firmware/ESP32_GENERIC-20260824-v1.29.0.bin` | v1.29.0 |

`sudo` на станции требует пароль, поэтому JDK поставлен локально (Temurin tarball). Если нужен системный: `sudo apt install openjdk-17-jdk-headless`.

## Переменные окружения

```bash
export JAVA_HOME="$HOME/.local/opt/jdk-17.0.21.1+2"
export ANDROID_HOME="$HOME/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"
```
