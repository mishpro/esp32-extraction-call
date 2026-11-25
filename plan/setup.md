# S0 — Окружение станции и приведение железа

Проверено на 2026-11-26: **JDK, Android SDK, Gradle, adb, kotlinc отсутствуют**; `/usr/lib/jvm`, `~/Android`, `$ANDROID_HOME` — пусто. Есть: Ubuntu 24.04, Python 3.12.3 (без pip), Node 22.23.5, npm 10.9.9, 6 CPU, 7.2 GB RAM, 60 GB free на `/home`, 137 GB на `/`.

## 1. Python-инструменты (сервер + прошивка)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh      # uv 0.13.x; apt-пакета нет
uv venv --python 3.12 .venv                          # свой интерпретатор, без system python3-venv
uv pip install esptool mpremote                      # в отдельный venv для железа
```

Раздельно от сервера: `server/` — свой `pyproject.toml` + `uv sync`; `tools/` (esptool/mpremote) — отдельный venv `.venv-tools`, чтобы не смешивать.

## 2. Провод и доступ к плате

Пользователь выполняет (нужен sudo, даёт права на `/dev/ttyUSB*`):

```bash
sudo usermod -aG dialout mish      # после — перелогин
```

Проверка: плата должна появиться как `/dev/ttyUSB0` (CP2102). Если нет — `dmesg | tail`, проверить драйвер CP2102 (`cp210x`).

## 3. Прошивка MicroPython 1.29.0

Прошивка: `ESP32_GENERIC-20260824-v1.29.0.bin` (ESP32-WROOM-32, без PSRAM). Клон DOIT DevKit V1 = классический ESP32, CP2102.

```bash
.venv-tools/bin/esptool --port /dev/ttyUSB0 chip_id
.venv-tools/bin/esptool --port /dev/ttyUSB0 flash_id
.venv-tools/bin/esptool --port /dev/ttyUSB0 erase-flash
.venv-tools/bin/esptool --port /dev/ttyUSB0 --baud 460800 write-flash 0x1000 ESP32_GENERIC-20260824-v1.29.0.bin
```

> esptool 5.x: команды `erase-flash` / `write-flash` (не `write_flash`). `--flash-size` — по данным `flash_id`.

После прошивки:

```bash
.venv-tools/bin/mpremote connect /dev/ttyUSB0 fs ls
.venv-tools/bin/mpremote connect /dev/ttyUSB0 repl
```

## 4. Проверка железа (до написания прошивки)

1. **Экран**: `ssd1306` из micropython-lib вендорим в `device/lib/ssd1306.py` (источник: `micropython/micropython-lib/micropython/drivers/display/ssd1306/`, MIT). Проверяем: I²C-адрес `0x3C`, SDA=21/SCL=22, вывод текста.

   Текст рисуем через `framebuf.Framebuf(..., framebuf.MONO_VLSB)` + `fb.text()` — в generic `extmod/modframebuf.c` есть встроенный шрифт PETME128 8×8 (`font_petme128_8x8`), т.е. `fb.text()` на ESP32 доступен без внешних библиотек. Если вдруг нет — свой шрифт 6×8 (~1 КБ) + `draw_text()`.

2. **Кнопка**: GPIO4, `Pin(4, Pin.IN, Pin.PULL_UP)`, читаем LOW = нажатие, debounce 30–50 мс.

3. **Пин-карта** (закреплено):

   | Что | Пин | Примечание |
   |---|---|---|
   | BTN1 (вызов) | GPIO4 | RTC_GPIO10, ext0-wake, pull-up, активный LOW |
   | SSD1306 SDA | GPIO21 | I²C, 0x3C |
   | SSD1306 SCL | GPIO22 | |
   | Тумблер питания | — | аппаратно на батарейной ветке, не GPIO |
   | BTN2 («стоп», S8) | GPIO14 | RTC_GPIO16 |
   | Резерв | GPIO25/26/27, GPIO32/33 | RTC-пины |

   Не использовать: `0/2/5/12/15` (strapping), `1/3` (UART), `6–11` (flash), `34–39` (только вход).

4. **Экран — один экран со статус-баром** (128×64, шрифт 8×8 → 21 колонка):

   ```
   ┌────────────────────────┐ y=0   WS:OK APP:OK 12:34:56
   │                        │
   │        STATE: ARMED    │ y=16  состояние кнопки (BOOT/ARMED/SENT/WAIT ETA/…)
   │      LAST 12:34:50     │ y=32  метка времени последнего события
   └────────────────────────┘
   ```

   Строка статуса: `WS:OK|ERR` (соединение с сервером), `APP:ON|OFF` (приложение на связи, по `status.app.online`), локальное время. Обновление при любом событии + раз в секунду для часов.

## 5. Android-окружение

```bash
sudo apt install -y openjdk-17-jdk-headless     # AGP 9.6 требует JDK 17
# cmdline-tools: скачать с developer.android.com/studio → "Command line tools only"
sdkmanager --sdk_root=$HOME/Android/sdk "platform-tools" "platforms;android-36" "build-tools;36.0.0"
sdkmanager --sdk_root=$HOME/Android/sdk --licenses
```

- Gradle — только wrapper (`gradlew`), системный Gradle не нужен.
- AGP 9.6, Gradle ≥ 9.8.2, compileSdk/targetSdk 36, minSdk 26.
- Диск: ~4–6 ГБ — место есть.
- Без эмулятора: проверка на реальном телефоне по USB (`adb devices`).

## 6. Сеть для стенда

Сервер поднимается на станции: `uvicorn --host 0.0.0.0 --port 8000`. Телефон ходит на `ws://<LAN-IP-станции>:8000/ws`.
`cleartext` в приложении: `android:usesCleartextTraffic="true"` (ws без TLS).
