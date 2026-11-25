# Пошаговый план MVP (S0 → S3)

Операционный порядок для агента-исполнителя. Каждый шаг: команда → ожидаемый результат → критерий «шаг закрыт».
Сервер и приложение — без VPS: стенд на станции, телефон ходит по LAN-IP.

---

## S0 — Окружение и железо

| # | Шаг | Команда / действие | Ожидаемый результат |
|---|---|---|---|
| 0.1 | JDK 17 | `sudo apt install -y openjdk-17-jdk-headless` | `java -version` → 17.x |
| 0.2 | Права на порт | `sudo usermod -aG dialout mish` + перелогин | `groups` содержит `dialout` |
| 0.3 | venv для железа | `uv venv .venv-tools && uv pip install esptool mpremote` | `.venv-tools/bin/esptool --help` |
| 0.4 | Подключить плату | USB → `ls -l /dev/ttyUSB*` | `/dev/ttyUSB0` (CP2102) |
| 0.5 | Опрос железа | `.venv-tools/bin/esptool --port /dev/ttyUSB0 chip_id` / `flash_id` | чип ESP32, размер флеша; результат → `plan/hardware-probe.md` |
| 0.6 | Прошивка uV 1.29.0 | скачать `ESP32_GENERIC-20260824-v1.29.0.bin` → `esptool --port /dev/ttyUSB0 erase-flash` → `esptool --port /dev/ttyUSB0 --baud 460800 write-flash 0x1000 <bin>` | плата перезагружается в REPL |
| 0.7 | REPL | `.venv-tools/bin/mpremote connect /dev/ttyUSB0 repl` | баннер MicroPython 1.29.0 |
| 0.8 | I²C-скан | скрипт: `I2C(0, sda=Pin(21), scl=Pin(22)).scan()` | видит `0x3c` |
| 0.9 | Экран | `ssd1306.py` (вендоренный) + `framebuf` → текст | на OLED видно строку |
| 0.10 | Кнопка | `Pin(4, Pin.IN, Pin.PULL_UP)` → чтение | LOW при нажатии |
| 0.11 | WiFi | скрипт подключения по SSID/PSK из `config.py` | `wlan.isconnected()` → True |

**S0 закрыт, когда:** REPL живой, экран рисует текст, кнопка читается, WiFi поднимается. Результаты — в `plan/hardware-probe.md`.

---

## S1 — Сервер

| # | Шаг | Что делаем | Проверка |
|---|---|---|---|
| 1.1 | Каркас | `server/pyproject.toml` (fastapi, uvicorn[standard], pydantic, pytest, pytest-asyncio, httpx, ruff) + `uv sync` | `uv run ruff check .` чисто |
| 1.2 | Модели | `app/models.py` по `plan/schema/messages.schema.json` | `pytest` на валидные/битые сообщения |
| 1.3 | Реестр | `app/registry.py`: `devices`/`apps`, сессии, last_seen | тест: две сессии разных ролей |
| 1.4 | Логи | `app/log.py`: JSON-строки, токен не пишем | тест: в логах нет `token` |
| 1.5 | Состояние | `app/state.py`: `last_press`/`last_status` → JSON-файл, атомарно | тест: перезапуск сервера сохраняет `last_press` |
| 1.6 | `/ws` | `app/main.py`: upgrade, hello-таймаут 10 с, лимит 4096, close 4400/4401/1011, ping 15 с, дедуп `seq` | тесты `test_hello`, `test_ping`, `test_dedupe` |
| 1.7 | Роутинг | `app/router.py`: press → ack(server) → партнёру → ack(delivered); status при смене живости | `test_press_flow` |
| 1.8 | Диагностика | `GET /api/status`, `GET /healthz` | `curl localhost:8000/api/status` → JSON |
| 1.9 | Симулятор | `tools/fake_device.py` (CPython, `websockets`): hello → press по таймеру → печать кадров | запустить против сервера, в логах `press` |

**S1 закрыт, когда:** `pytest` зелёный, `fake_device` подключается, получает `hello_ok`, шлёт `press`, сервер логирует и отвечает `ack`.

---

## S2 — Кнопка

| # | Шаг | Что делаем | Проверка |
|---|---|---|---|
| 2.1 | `frames.py` | чистые `build_frame`/`parse_frame`: маскирование, длины 7/16/64, сборка фрагментов, ping/pong | тест на CPython (`pytest`), без uV-импортов в ядре |
| 2.2 | `ws_client.py` | handshake + проверка `101`/`Sec-WebSocket-Accept` **до** чтения кадра; select.poll; reconnect §6 | подключается к серверу, шлёт `hello` |
| 2.3 | `proto.py` | сборка/разбор сообщений | round-trip тест |
| 2.4 | `button.py` | BTN1 GPIO4, pull-up, debounce 30–50 мс | нажатие даёт одно событие |
| 2.5 | `display.py` | один экран: статус-бар `WS:OK APP:ON 12:34:56` + `STATE:` + метка времени | текст на OLED |
| 2.6 | `state.py` | BOOT/OFFLINE/ARMED/SENT/WAIT_ETA/NO APP/AUTH FAIL | переходы видно на экране |
| 2.7 | `main.py` | wifi → ws → цикл: читаем кнопку, шлём `press`, ждём `ack` | — |
| 2.8 | Заливка | `mpremote fs cp` для всех модулей + `config.py` | нажатие → `press` в логах сервера, экран `SENT` |

**S2 закрыт, когда:** реальная плата шлёт `press`, получает `ack`, экран показывает `SENT`/`WAIT ETA`, статус-бар отражает живость WS и приложения.

**Прогресс (2026-11-26):** 2.1–2.3 готовы и проверены на CPython (`frames.py`, `proto.py`, `ws_client.py`; 206 тестов; `ws_client` прошёл полный путь `hello → press → ack(delivered)` против реального сервера). 2.4–2.8 блокированы: экран не отвечает по I²C, нет WiFi-доступов.

---

## S3 — Приложение

| # | Шаг | Что делаем | Проверка |
|---|---|---|---|
| 3.1 | Каркас | `app/` Gradle wrapper, AGP 9.6, Kotlin, Compose, minSdk 26, target/compile 36, OkHttp 5.5.1 | `./gradlew assembleDebug` |
| 3.2 | Хранилище | `SecureStore`: `app_id` (8 hex), токен — `EncryptedSharedPreferences` | значение переживает перезапуск |
| 3.3 | Протокол | `Protocol.kt` — разбор/сборка по схеме | unit-тест на примерах |
| 3.4 | WS-клиент | `WsClient`: `hello` первым кадром, `pong` на `ping`, reconnect §6 (макс 60 с) | подключается к `ws://<LAN-IP>:8000/ws` |
| 3.5 | Сервис | `SignalService` (foreground), WS внутри, наружу `StateFlow` | сервис живёт при сворачивании |
| 3.6 | Уведомления | каналы `signals` (HIGH) и `status` (LOW), `POST_NOTIFICATIONS` | уведомление появляется |
| 3.7 | UI | статус WS, статус кнопки (`online/state`), список нажатий (метка сервера + кнопка) | нажатие видно в списке |
| 3.8 | Сборка на телефон | `./gradlew installDebug` (adb) | приложение на телефоне |

**S3 закрыт, когда:** нажатие кнопки → уведомление на телефоне, приложение шлёт `ack(delivered)`, кнопка показывает `WAIT ETA`.

**Прогресс (2026-11-26):** 3.1–3.4 готовы: `assembleDebug` и 29 JVM-тестов (Protocol 18, Backoff 9, WsClient 2 через MockWebServer: `hello` первым кадром, `pong` на `ping`, `hello_ok` → Connected). Отклонения: AGP 9.6.0-alpha06 (стабильного 9.6.0 нет), `compileSdk 37.2` (требование OkHttp 5.5.1/Compose), `targetSdk` остался 36. 3.5–3.8 ждут телефон/эмулятор.

---

## Порядок и правила

1. S1 → S2 → S3 строго: каждый следующий этап проверяет предыдущий на стенде.
2. Перед коммитом — `code-review`; коммит с обоснованием — `contextual-commits`.
3. Скиллы по этапам — `plan/skills.md`.
4. Секреты (SSID/PSK/токены) — только локально, в `.gitignore`: `secrets.json`, `*.secret`, `device/config.py`.
5. Всё, что выходит за рамки MVP (`timer`, BLE, FCM, питание), — S4…S8, отдельными шагами.

## Вне MVP (не делаем сейчас)

Обратный путь `timer`/`cancel` и отсчёт на кнопке; BLE-провижининг; FCM; питание и deep sleep; кнопка «стоп»; домен/TLS; авторизация включением.
