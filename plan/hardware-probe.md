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

## Экран: решено (128×64, 0x3C)

После проверки проводки модуль ответил: `I2C.scan() → [60]`. Отрисовка двумя кадрами
(128×32 и 128×64) показала, что на панели помещается кадр 64 px: видны рамка,
`64PX`, `MIDDLE` и `BOTTOM`. **Модуль 128×64, SSD1306, адрес 0x3C.**

Драйвер — `device/lib/ssd1306.py` (micropython-lib, MIT). При отключении GND
`show()` бросает `OSError(ENODEV)`; `Display.render()` теперь пересоздаёт драйвер
на следующем кадре, поэтому прошивка не падает при плохом контакте.

## S2 на железе: результаты и грабли

| Что | Результат |
|---|---|
| WiFi | 2.4 ГГц, плата получила `192.168.1.73`, сервер на `192.168.1.71:8000` |
| WS-клиент | handshake, `hello`, `hello_ok`, `status` — работает на uV 1.29 |
| Кнопка | 3-контактный модуль, два провода: `OUT→GPIO4`, `GND→GND`; active-low |
| E2E | `press seq=1` → `ack(server)` → `ack(delivered)` → экран `WAIT ETA` |
| Heartbeat | `pong` на JSON `ping` держит сессию; после фикса нет `ping_miss` |

Несовместимости MicroPython, которых не видит CPython:

1. **Нет `time.monotonic()`** → `ws_client`/`net` на `ticks_ms`/`ticks_diff`.
2. **Нет `del bytearray[:n]`** → `buf[:] = buf[n:]`; `Fragments.reset` — `buf[:] = b""`.
3. **F-строка не принимает строковый литерал в `{...}`** (`f"{d['k']}"`) → промежуточные переменные.
4. **Серверный JSON `ping` — не WS-ping**: `ws_client` отвечает только на opcode 0x9, поэтому `main.py` обязан слать JSON `pong` сам.
5. Экран при потере GND — `ENODEV`; нужна переинициализация драйвера.

Ещё: при опросе кнопки раз в 100 мс короткий тап проскакивал; сейчас 50 мс,
нажатие с удержанием 0.5 с ловится стабильно. В лог пишется каждое изменение
уровня (`btn level 0/1`) — удобно для диагностики проводки.

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
