# Guidecaller — сервер (S1)

Реализация этапа S1: WebSocket-сервер протокола Guidecaller v1.0
(`drafts/Протокол.md` §2, §4, §5, §8), HTTP-диагностика и симулятор кнопки.

Стек: Python 3.12, FastAPI, uvicorn, Pydantic v2, pytest + pytest-asyncio,
httpx, ruff; зависимости — `uv`.

## Запуск

```bash
cd server
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --ws-ping-interval 3600 --ws-ping-timeout 3600
```

Для разработки — добавить `--reload`.

### Почему uvicorn запускается с `--ws-ping-interval 3600`

У uvicorn есть собственный WS-level ping (по умолчанию каждые 20 с, таймаут
20 с). Протокол Guidecaller держит **свой** heartbeat на уровне JSON-сообщений
(`{"t":"ping"}` каждые 15 с, 3 пропуска → закрытие, §4.8). Если оставить
uvicorn-овский ping включённым, получаются два независимых таймера с разными
таймаутами: клиент может быть закрыт «сверху» раньше, чем протокол успеет
посчитать пропуски, а причина закрытия будет непрозрачной.

`3600` вместо `0` выбран сознательно: `0` в uvicorn/websockets означает «пинг
каждые 0 секунд» (то есть фактически busy-loop), а не «выключено». Час —
практически выключено, но оставляет верхнюю страховку от полностью мёртвого
сокета на уровне транспорта.

Следствие: клиенты обязаны отвечать на **протокольный** `ping` (кнопка это
делает по §4.8, приложение — по §9). WS-level pong от клиента сервер не
требует.

Лимит кадра 4096 байт проверяется в приложении (иначе close был бы `1009`, а
не `4400` по §5). `--ws-max-size` специально **не** уменьшаем до 4096:
uvicorn закрыл бы соединение своим кодом раньше, чем сервер успел отправить
`error`/`close 4400`. Значение по умолчанию (16 MiB) — только транспортная
страховка.

## Переменные окружения

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `HOST` | `0.0.0.0` | адрес (используется только при запуске из `Settings`) |
| `PORT` | `8000` | порт (для документации; uvicorn берёт `--port`) |
| `AUTH_REQUIRED` | `false` | включить проверку токена (§4.1, ADR-0011) |
| `DEVICE_TOKEN_SHA256` | — | SHA-256 токена кнопки, hex |
| `APP_TOKEN_SHA256` | — | SHA-256 токена приложения, hex |
| `STATE_FILE` | `server/.state/state.json` | файл `last_press`/`last_status`/seq |
| `HELLO_TIMEOUT_S` | `10` | таймаут первого кадра (§2) |
| `PING_INTERVAL_S` | `15` | период серверного `ping` (§4.8) |
| `PONG_TIMEOUT_S` | `10` | ожидание `pong` после ping |
| `MAX_PING_MISSES` | `3` | пропусков подряд до закрытия |
| `MAX_FRAME_BYTES` | `4096` | лимит кадра (§1) |
| `LOG_LEVEL` | `INFO` | уровень JSON-логов |

Токен **никогда** не передаётся в URL (сервер его оттуда и не читает) и не
попадает в логи: `app/log.py` выбрасывает любые поля, в имени которых есть
`token`/`secret`, а сообщения валидации Pydantic собираются без `input_value`.

`AUTH_REQUIRED=true` без настроенного хеша для роли — fail closed: `hello`
получает `error(auth_failed)` и close `4401` (иначе включение флага «забыли
токен» молча пускало бы всех).

Хеш токена:

```bash
printf %s 'super-secret' | sha256sum | cut -d' ' -f1
```

## Маршруты

| Метод | Путь | Назначение |
|---|---|---|
| WS | `/ws` | единственная точка обмена (§1) |
| GET | `/api/status` | `{"device": {...}, "app": {...}}` (§8) |
| GET | `/healthz` | liveness для systemd |

`/api/status` при отсутствии сессии отдаёт `device: {"online": false,
"state": "OFFLINE"}`, `app: {"online": false}`.

## Состояние на диске

`last_press`, `last_status` и watermark `seq` по отправителям пишутся в JSON
файл атомарно (tmp-файл → `fsync` → `rename`). Путь по умолчанию —
`server/.state/state.json`; каталог в `server/.gitignore`. Для продакшена
задайте `STATE_FILE` вне репозитория (например,
`/var/lib/guidecaller/state.json`).

## Логи

Одна JSON-строка на событие в stdout. Канонические ключи: `event`,
`device_id`, `app_id`, `session`, `seq`; события: `hello`, `hello_ok`, `press`,
`ack`, `status`, `ping_miss`, `close`, `error`, `duplicate`, `internal`.
Токен не логируется ни в каком виде.

## Проверка без железа

```bash
# терминал 1
cd server && uv run uvicorn app.main:app --port 8001 \
    --ws-ping-interval 3600 --ws-ping-timeout 3600

# терминал 2: интерактивно (Enter = нажатие)
cd server && uv run python ../tools/fake_device.py --url ws://127.0.0.1:8001/ws

# или по таймеру
cd server && uv run python ../tools/fake_device.py --url ws://127.0.0.1:8001/ws \
    --interval 1 --count 3

# полный путь press → delivered: фейковое приложение в третьем терминале
cd server && uv run python ../tools/fake_device.py --url ws://127.0.0.1:8001/ws \
    --role app --id deadbeef --auto-ack
```

## Тесты

```bash
cd server
uv run ruff check .
uv run pytest -q
```

Тесты поднимают настоящий uvicorn на случайном порту: несколько WS-клиентов
должны жить в одном event loop, иначе кросс-сессионная рассылка `status`
не проверяется.

## Ограничения S1 (обновлено в S4)

- MVP — одна пара «кнопка + приложение». При нескольких сессиях одной роли
  роутер берёт самую свежую; явного спаривания в протоколе нет.
- `timer`/`cancel` реализованы в S4: `timer` → `deadline_t` → кнопке,
  `ack(server)` приложению и `ack(delivered)` обратно после кнопки;
  `bad_duration` — recoverable ошибка без закрытия; дубликат `seq` команды
  отбрасывается (`error(duplicate)`).
- `ack(stage=delivered)` приходит от приложения (S3); сервер не синтезирует
  delivered в момент пересылки — иначе кнопка показывала бы `WAIT ETA` до
  фактической доставки. Для `timer`/`cancel` delivered присылает кнопка.
- Состояние кнопки (`ARMED`/`SENT`/`WAIT_ETA`/`COUNTDOWN`/`ARRIVED`/`OFFLINE`)
  сервер выводит сам: в протоколе нет сообщения «кнопка сообщает состояние».
  `ARRIVED` вычисляется по `deadline_t` (`asyncio`-задача, ADR-0020).
