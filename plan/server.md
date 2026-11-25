# S1 — Сервер (FastAPI + uvicorn)

Источник правды по поведению — `drafts/Протокол.md` §2, §4, §5, §8.

## Стек

Python 3.12, FastAPI, `uvicorn[standard]`, Pydantic v2, pytest + pytest-asyncio, httpx (тесты), ruff. Управление зависимостями — `uv` (`pyproject.toml` + `uv.lock`).

## Структура

```
server/
  pyproject.toml
  app/
    main.py        # FastAPI-приложение, lifespan, маршруты
    config.py      # Settings из env: PORT, AUTH_REQUIRED, токены, путь state-файла
    models.py      # pydantic-модели сообщений (§4)
    registry.py    # DeviceSession/AppSession, реестры, last_seen
    router.py      # маршрутизация press/timer/status между ролями
    errors.py      # коды ошибок и close-коды (1000/4400/4401/1011)
    log.py         # JSON-логи: event, device_id, app_id, session, seq
    state.py       # персистенция last_press/last_timer/last_status
  tests/
```

## Маршруты

| Метод | Путь | Назначение |
|---|---|---|
| WS | `/ws` | единственная точка обмена |
| GET | `/api/status` | `{"device": {...}, "app": {...}}` — диагностика (§8) |
| GET | `/healthz` | liveness для systemd |

## Правила обработки (по §2 и §4)

1. До `hello` сервер не принимает ничего другого → close `4400`.
2. Таймаут на `hello` — 10 с → close `4400`.
3. Повторный `hello` в сессии → close `4400`.
4. Повторный `hello` (новая сессия) — ок, но старый `device_id`/`app_id` вытесняется.
5. Кадр > 4096 байт → close `4400`.
6. `v` ≠ 1 → `error` + close `4400`.
7. `role` ∈ {`device`,`app`}; отсутствие `id` → `error(bad_request)` + close `4400`.
8. Авторизация: в конфиге — SHA-256 от токена; сравнение `hmac.compare_digest`. Флаг `AUTH_REQUIRED` (MVP — `false`). Токен не принимается из URL и **никогда не логируется**.
9. Heartbeat: сервер шлёт `ping` каждые 15 с; нет `pong` 3 раза подряд → close сессии, удалить из реестра, разослать `status`.
10. `press` → `ack(stage=server)` отправителю; после передачи партнёру → `ack(stage=delivered)` отправителю и партнёру.
11. Дубликаты `seq` → игнорировать (dedupe по `seq` в памяти + на диске).
12. `status` рассылается при: старте сессии, изменении живости клиента, смене состояния кнопки.

**Важно про uvicorn:** у uvicorn есть свой WS-level ping (`--ws-ping-interval`, по умолчанию 20 с). Он дублирует протокольный heartbeat — отключаем или сильно поднимаем интервал, иначе «пропуски пингов» в двух слоях.

## Состояние и персистенция

- Реестр в памяти: `devices: dict[str, DeviceSession]`, `apps: dict[str, AppSession]`.
- `last_press`, `last_timer`, `last_status` — в памяти + сброс на диск: JSON-файл, атомарная запись (write tmp → rename). Файл в `.gitignore`.
- Токены и state-файл — вне репозитория (`secrets.json` / `.state/state.json`).

## Логи

JSON в stdout, ключи: `event`, `device_id`, `app_id`, `session`, `seq`. События: `hello`, `hello_ok`, `press`, `ack`, `status`, `ping_miss`, `close`, `error`. Токен в лог не попадает никогда.

## Тесты

`pytest` + `pytest-asyncio`:

- `test_hello.py` — успешный hello, таймаут 10 с, повторный hello, неверная роль, `v=2`;
- `test_press_flow.py` — press → ack(server) → доставка приложению → ack(delivered);
- `test_dedupe.py` — повторный `seq` игнорируется;
- `test_ping.py` — 3 пропуска pong → закрытие и `status`;
- `test_errors.py` — `bad_duration`, `no_peer`, close-коды 4400/4401/1011.

Плюс `tools/fake_device.py` — симулятор кнопки на CPython (`websockets`): подключается, шлёт `hello`, жмёт «кнопку» по таймеру, печатает все кадры. Он же проверка сервера без железа.

## Запуск (стенд)

```bash
cd server && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Деплой на VPS (когда появится)

systemd-юнит: `ExecStart=<venv>/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`, `Restart=always`, `EnvironmentFile=/etc/guidecaller.env` (токены, `AUTH_REQUIRED`). Логи — в journald (`journalctl -u guidecaller -o json`). Порт 8000 открыт наружу, TLS отложен.
