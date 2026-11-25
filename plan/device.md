# S2 — Кнопка (MicroPython 1.29.0)

Плата: клон DOIT DevKit V1 (ESP32-WROOM-32, 36 выводов, CP2102, USB-C). Дисплей: SSD1306 128×64 I²C 0x3C. Кнопка: GPIO4.

## Структура

```
device/
  main.py        # точка входа: wifi → ws → рабочий цикл
  config.py      # SSID/PSK/host/port/token (в git НЕ коммитится — генерируется локально)
  net.py         # подключение WiFi с retry, machine.freq(160_000_000)
  ws_client.py   # ручной RFC6455-клиент (§7 Протокола)
  frames.py      # чистые функции: build_frame / parse_frame (без uV-импортов)
  proto.py       # сборка/разбор сообщений (json)
  display.py     # SSD1306 + один экран со статус-баром
  button.py      # BTN1: pull-up, debounce 30–50 мс
  state.py       # состояния и переходы
  lib/ssd1306.py # вендоренный драйвер из micropython-lib (MIT)
```

## WS-клиент — писать вручную (§7)

В `micropython-lib` WS-клиента нет; `extmod/modwebsocket.c` — WebREPL-сервер. Требования из §7:

- **Handshake**: `GET /ws HTTP/1.1` + `Upgrade: websocket`, `Sec-WebSocket-Key` (base64 16 случайных байт), `Sec-WebSocket-Version: 13`. Проверить `101` и `Sec-WebSocket-Accept == base64(sha1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))` **до** чтения первого кадра, иначе байты HTTP-ответа попадут в разбор кадра.
- **Кадры**: клиент **маскирует** (MASK=1, 4 случайных байта, XOR payload). Длина 7 / 7+16 / 7+64. Opcodes: `0x1` text, `0x8` close, `0x9` ping, `0xA` pong, `0x0` continuation.
- **Фрагментация**: собирать продолжения до `FIN=1` (сервер вправе фрагментировать). Свои исходящие — одним кадром (payload ≤ 4096).
- **ping от сервера** → ответить `pong` с той же payload.
- **close** → ответить `close`, закрыть сокет.
- **Ввод/вывод**: `socket.settimeout()` + `select.poll()`, неблокирующее чтение со своим дедлайном.
- **Память**: переиспользовать буферы, не создавать `bytearray` на каждый кадр; `gc.mem_free()` в лог на старте и раз в минуту.
- **JSON**: `import json` (в uV ≥ 1.19 — встроенный быстрый модуль).

`frames.py` держим **без** uV-специфичных импортов → тот же файл тестируется на CPython (pytest): маскирование/разбор/сборка фрагментов.

### Статус реализации (2026-11-26)

| Файл | Статус |
|---|---|
| `frames.py` | готово: 61 тест |
| `proto.py` | готово: 107 тестов + сверка со схемой |
| `ws_client.py` | готово: handshake §7.1, poll-чтение §7.3, backoff §6; 15 тестов, включая TCP-раундтрип |
| `button.py`, `display.py`, `state.py`, `net.py`, `main.py` | ждут плату (WiFi-доступы + экран) |

`ws_client.py` проверен **тем же файлом** на CPython против реального сервера: `connect()` → `hello` → `press` → `ack(server)` → `ack(delivered)`, HTTP-ответ разобран до первого кадра. Импорты (`socket`, `select`, `hashlib`, `binascii`, `os`, `random`, `time`) есть и в MicroPython, и в CPython.

API: `read_event(timeout_ms)` → `(KIND_MESSAGE|KIND_PING|KIND_PONG|KIND_CLOSE, opcode, payload)` или `None`; `ping` получает `pong` автоматически; `send_text()` маскирует и не фрагментирует; `backoff_s(attempt, cap_s=10.0)` — джиттер ±20 %, результат не больше `cap_s`.

## Состояния кнопки

Из §4.2: `BOOT`, `OFFLINE`, `ARMED`, `SENT`, `WAIT_ETA`, `COUNTDOWN`, `ARRIVED`, `NO_ACK`.

| Состояние | Когда | Экран |
|---|---|---|
| `BOOT` | поднимаем WiFi/WS | `STATE: BOOT` |
| `OFFLINE` | WS не поднят | `STATE: OFFLINE` |
| `ARMED` | готов к нажатию | `STATE: ARMED` |
| `SENT` | `press` отправлен, ждём `ack(stage=server)` | `STATE: SENT` |
| `WAIT ETA` | пришёл `ack(stage=delivered)` | `STATE: WAIT ETA` |
| `NO APP` | в `status`/`hello_ok` нет живого приложения | `STATE: NO APP` |
| `AUTH FAIL` | close 4401 / `error(auth_failed)` | `STATE: AUTH FAIL` |
| `COUNTDOWN`/`ARRIVED` | S4 (в MVP не активны) | — |

## Экран — один экран со статус-баром

128×64, шрифт 8×8 (21 колонка). Никаких отдельных экранов: строка статуса сверху, ниже — состояние и метка времени.

```
y=0   WS:OK APP:ON 12:34:56
y=16  STATE: ARMED
y=32  LAST 12:34:50
```

- Статус-бар: `WS:OK|ERR` (соединение с сервером), `APP:ON|OFF` (`status.app.online`), локальное время.
- Строка состояния — текущее состояние кнопки.
- `LAST` — метка времени последнего события (от сервера; рядом, если есть, метка кнопки).
- Обновление: при любом входящем сообщении + раз в секунду для часов.

## Время

NTP не нужен. Смещение считается из `t_epoch` в `hello_ok`/`status`/`press`: `off = t_epoch − device_now`; таймер (S4) считается по `deadline_t` из сервера: `remaining = deadline_t − (device_now + off)`. `ntptime` — опциональный fallback, в MVP не используем.

## Переподключение (§6)

Backoff `min(60, 2^attempt) ± 20 %`, сброс счётчика при `hello_ok`. В MVP кнопка всегда «ACTIVE» → максимальная задержка 10 с (deep sleep отложен до S7). На новой сессии — снова `hello`.

## Заливка на плату

```bash
.venv-tools/bin/mpremote connect /dev/ttyUSB0 fs cp device/main.py :main.py
.venv-tools/bin/mpremote connect /dev/ttyUSB0 fs cp device/config.py :config.py   # локально, не из git
.venv-tools/bin/mpremote connect /dev/ttyUSB0 fs ls
.venv-tools/bin/mpremote connect /dev/ttyUSB0 run main.py
```

`config.py` генерируется локально: `ssid`, `psk`, `server_host`, `server_port`, `token`, `device_id` (8 hex, генерируется при первом запуске, хранится на плате — §3).

**Токен в MVP не пустой.** Авторизация выключена (`AUTH_REQUIRED=false`), сервер значение не проверяет, но схема §4.1 и сервер требуют `token` длиной ≥ 1: пустая строка — это `error(bad_request)` и close `4400`. Поэтому в `config.py` кладём заглушку, например `token = "dev-token"`.

## Энергопотребление

`machine.freq(160_000_000)`, `gc.collect()` в цикле. WiFi не выключаем: статус приложения должен быть виден. Deep sleep — S7 (требует переработки: wake по ext0 GPIO4, иначе не «видно» доступность приложения).
