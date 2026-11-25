# Скиллы проекта

Локально 15 скиллов (`/home/mish/.agents/skills/`) + 6 установленных из экосистемы skills.sh (2026-11-26). Итого 21.
Правило: грузим нужный скилл в начале своего этапа, не весь набор сразу.

## Локальные — основные

| Скилл | Этап | Что берём |
|---|---|---|
| `esp32-micropython-workflow` | S0, S2 | прошивка uV 1.29 (`esptool 5`: `erase-flash`/`write-flash`), `chip_id`/`flash_id`, `mpremote fs cp` / `run` / `repl`, venv с esptool+mpremote, грабли uV (f-strings, ручной `gc.collect()`) |
| `esp32` | S0, S2 | валидация GPIO/anti-bricking, выбор варианта чипа и прошивки, проверка RTC-пинов (GPIO4 = RTC_GPIO10), риски запинания платы, энергопотребление |
| `uv-package-manager` | S1 | `uv init`/`add`/`sync`/`lock`, `pyproject.toml` + `uv.lock`, разделение venv сервера и venv инструментов, чистка зависимостей |
| `planning-with-files` | S0–S3 | `task_plan.md` / `findings.md` / `progress.md` — трекер многошаговой работы и память между сессиями |
| `code-review` | после каждого этапа | ревью диффа перед коммитом: протокол, обработка ошибок, реконнект, утечки буферов |
| `contextual-commits` | коммиты | коммиты с обоснованием (почему ws:// без TLS, почему ручной WS-клиент, почему MVP односторонний) — решения из `plan/README.md` не должны потеряться в истории |

## Из экосистемы skills.sh — по этапам

Установлены 2026-11-26 (user-level, `~/.agents/skills`). Проверены: установки, лицензия, звёзды GitHub, свежесть.

### S1 — сервер

| Скилл | Источник | Установки | Что берём |
|---|---|---|---|
| `fastapi-expert` | `jeffallan/claude-skills` (★12.5K, MIT) | 6.7K | FastAPI + Pydantic v2, WebSocket-эндпоинты, auth-флоу |
| `websocket-engineer` | `jeffallan/claude-skills` (★12.5K, MIT) | 7.4K | двусторонние сообщения, **presence tracking** (нужен для `status.app.online`), reconnect-паттерны |

### S3 — приложение (Kotlin/Compose)

| Скилл | Источник | Что берём |
|---|---|---|
| `compose` | `rcosteira79/android-skills` (★179, MIT, обновлён 2026-11-10) | state management, recomposition, навигация, производительность |
| `kotlin-coroutines` | там же | structured concurrency, dispatcher'ы, кто владеет scope — WS живёт в Foreground Service |
| `kotlin-flows` | там же | StateFlow/SharedFlow → прямая реализация §9 Протокола (UI подписан на поток из сервиса) |
| `gradle-build-performance` | там же | первая сборка: configuration cache, kapt→KSP, диагностика медленной сборки |

Установка (для повторяемости):

```bash
npx skills add jeffallan/claude-skills@fastapi-expert -g -y
npx skills add jeffallan/claude-skills@websocket-engineer -g -y
npx skills add rcosteira79/android-skills@compose -g -y
npx skills add rcosteira79/android-skills@kotlin-coroutines -g -y
npx skills add rcosteira79/android-skills@kotlin-flows -g -y
npx skills add rcosteira79/android-skills@gradle-build-performance -g -y
```

## Ситуативные (локальные)

| Скилл | Когда подключать |
|---|---|
| `deep-research` | S5 (BLE GATT на uV: свои UUID, размер ATT-пакета, write-without-response), S6 (FCM HTTP v1, data vs notification, поведение в doze/force-stop), S7 (power path, схема TP5100 + boost + диодная развязка), вопрос «давать ли wss» после появления VPS |
| `linux-troubleshooting` | станция: порт не появился (`/dev/ttyUSB0`, драйвер CP2102, dialout), сеть/LAN-IP для телефона, uvicorn не поднимается/не видно с телефона, systemd-юнит на VPS |
| `python-dependency-resolver` | конфликт версий при `uv sync` (fastapi/pydantic/websockets/pytest-asyncio) |
| `find-skills` | поиск скилла под конкретную задачу (уже использован: Android/Kotlin, WS/FastAPI) |
| `grilling` | уже отработал: требования, стек, MVP, протокол |
| `cmux-markdown` | просмотр `plan/*.md` и `drafts/Протокол.md` в панели с live reload |
| `caveman` | режим общения (активен) |

## Отложенные из экосистемы

| Скилл | Когда ставить | Зачем |
|---|---|---|
| `jeffallan/claude-skills@firebase-expert` | S6 | FCM/firebase-admin, секреты; покрытие частичное — наш путь: FCM HTTP v1 с сервера + Android-клиент |
| `yaklang/hack-skills@websocket-security` | после появления auth/TLS | hardening WS-сервера; в MVP auth выключен, TLS нет — смысла пока нет |

## Не используем

| Скилл | Почему |
|---|---|
| `cpp-coding-standards` | новый код — Python/Kotlin; C++ только в `legacy/` (Arduino-справка), не трогаем |
| `robotics-software-principles` | проект не робототехника; базовые принципы дизайна и так в `plan/README.md` |
| `report` | баг-репорты OpenCode не нужны |
| `opencode` | только если понадобится настройка opencode/MCP — по умолчанию не тянем |

## Отвергнутые кандидаты

`unkistudio/agent-skills@esp32-micropython-workflow` (6 установок — хуже локального), `drjacky/claude-android-ninja` (830, слабее по покрытию), `partme-ai/full-stack-skills@android-kotlin` (48), `anthropics/knowledge-work-plugins@setup-zoom-websockets` (про Zoom), `mindrally/skills@websocket-development` (879, ниже по качеству, чем `websocket-engineer`).
