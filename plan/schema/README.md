# Схема протокола (машиночитаемая выжимка)

`messages.schema.json` — JSON Schema (draft 2020-12) всех типов сообщений из `drafts/Протокол.md` v1.0.

**Источник правды — `drafts/Протокол.md`.** Схема — производная: при правке документа правим и её, затем гоняем валидатор.

## Что покрыто

| Тип | Направление | Обязательные поля |
|---|---|---|
| `hello` | клиент → сервер | `v`, `t`, `role`, `token`, `id` (+`fw` для device) |
| `hello_ok` | сервер → клиент | `v`, `t`, `session`, `t_epoch`, `peer{id,online,state}` |
| `press` | кнопка → сервер → приложение | `v`, `t`, `seq`, `t_epoch` (+`from` от сервера) |
| `timer` | приложение → сервер → кнопка | `v`, `t`, `seq`, `duration_s` (60…86400), `t_epoch` (+`deadline_t` от сервера) |
| `cancel` | приложение → сервер → кнопка | `v`, `t`, `seq`, `t_epoch` |
| `ack` | в обе стороны | `v`, `t`, `seq`, `ok`, `stage` (`server`/`delivered`) |
| `status` | сервер → обоим | `v`, `t`, `t_epoch`, `device{online,state}`, `app{online}` |
| `ping` / `pong` | обе | `v`, `t`, `t_epoch` |
| `error` | сервер → клиент | `v`, `t`, `code`, `msg` (+`seq`) |

Состояния кнопки (`state`): `BOOT`, `OFFLINE`, `ARMED`, `SENT`, `WAIT_ETA`, `COUNTDOWN`, `ARRIVED`, `NO_ACK`.
Коды ошибок: `auth_failed`, `bad_request`, `bad_duration`, `no_peer`, `duplicate`, `internal`.

Во всех типах `additionalProperties: false` — лишнее поле = ошибка валидации. Это страховка от опечаток в разных реализациях.

## Валидация примеров из документа

```bash
cd /home/mish/personal_devel/esp32-extraction-call
uv run --with jsonschema python plan/schema/validate_examples.py
```

Скрипт вытаскивает все JSON-блоки из `drafts/Протокол.md` (в блоке §4.8 два объекта — `ping` и `pong`, поэтому разбор через `raw_decode`) и проверяет каждый по схеме.

Текущий результат: **13 примеров, 0 расхождений.**

## Как использовать в коде

- **Сервер (Python)**: pydantic-модели в `server/app/models.py` — по одному `BaseModel` на тип; сервер — единственное место, где проверяется входящее сообщение.
- **Кнопка (MicroPython)**: JSON — встроенный быстрый модуль, схему на плате не проверяем; разбор по полю `t` + ручная валидация обязательных полей.
- **Приложение (Kotlin)**: можно сгенерировать data-классы из схемы — но для MVP проще написать руками (10 типов) и держать рядом `proto/messages.json` как справку.

## Что не покрыто (осознанно)

- `register_push` (регистрация push-токена) — появится вместе с FCM на S6, тогда и дополним схему и `Протокол.md`.
- Транспортные параметры (лимит 4096 байт, ping 15 с, таймаут hello 10 с, close-коды) живут в `plan/protocol-mvp.md` и в самом `Протокол.md` — в JSON Schema они не выражаются.
