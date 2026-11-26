# S3 — Приложение (нативное Android)

Kotlin + Jetpack Compose. OkHttp WebSocket в Foreground Service. Push — FCM (шаг S6).

## Сборка

| Параметр | Значение |
|---|---|
| AGP | 9.6.0 |
| Gradle | wrapper ≥ 9.8.2 (системный Gradle не нужен) |
| JDK | 17 |
| compileSdk / targetSdk | 36 (требование Google Play с 31.08.2026) |
| minSdk | 26 (Android 8) — рекомендация |
| OkHttp | 5.5.1 |

## Структура

```
app/
  build.gradle.kts, settings.gradle.kts, gradle/libs.versions.toml
  app/
    src/main/AndroidManifest.xml
    src/main/java/…/NightcallApp.kt
    src/main/java/…/net/WsClient.kt            # OkHttp WebSocket, reconnect (§6)
    src/main/java/…/net/Protocol.kt            # разбор/сборка сообщений
    src/main/java/…/service/SignalService.kt   # Foreground Service (specialUse) + WS
    src/main/java/…/service/SignalBus.kt       # process-wide StateFlow для UI
    src/main/java/…/service/SignalReducer.kt   # чистые переходы состояния
    src/main/java/…/ui/MainActivity.kt
    src/main/java/…/ui/TimeFormat.kt
    src/main/java/…/notify/Notifications.kt    # каналы signals / status
    src/main/java/…/data/SecureStore.kt        # EncryptedSharedPreferences: app_id, token
    src/main/java/…/ble/ConfigProvisioning.kt  # S5: BLE-клиент (GATT)
    src/main/java/…/fcm/NightcallMessaging.kt # S6: FCM
```

## Требования из §9

- **OkHttp WebSocket** — готовый клиент, поддержка ping/pong и колбэков.
- **Foreground Service**: сокет живёт в сервисе, UI подписан на `StateFlow` из сервиса.
- **Токен** — в `EncryptedSharedPreferences` (`androidx.security:security-crypto`), в сокет передаётся первым кадром `hello`. В MVP авторизация выключена, но §4.1 требует `token` длиной ≥ 1: пустая строка даёт close `4400`, поэтому по умолчанию используется заглушка `dev-token` (реальный секрет — при провижининге, S5/S6).
- **Каналы уведомлений**: `signals` (HIGH) — сигнал от кнопки; `status` (LOW) — статусы. На Android 13+ нужно разрешение `POST_NOTIFICATIONS`.

## Протокол в приложении

- `hello` (role=`app`, `id`=app_id, `token`=…) сразу после коннекта; без `hello_ok` в течение 10 с — reconnect с backoff (§6, максимум 60 с).
- `press` от сервера → показать уведомление (канал `signals`) **и** отдать `ack(seq, stage=delivered)` (§4.6). Дедуп — по `(from, seq, t_epoch)`, потому что `seq` у кнопки начинается с 1 после каждой перезагрузки; дедуп по одному `seq` терял бы новые вызовы.
- `ping` → `pong` в течение 10 с.
- `status` → обновить UI: `device.online`, `device.state`, `app.online`.
- `error` → по коду: `auth_failed` — не переподключаться, показать `AUTH FAIL`; `no_peer` — показать `NO BUTTON`.

## Реализация S3.5–3.8 (2026-11-26)

| Компонент | Как сделано |
|---|---|
| `SignalService` | foreground, тип `specialUse` (+ `FOREGROUND_SERVICE_SPECIAL_USE` и `PROPERTY_SPECIAL_USE_FGS_SUBTYPE`), `START_REDELIVER_INTENT`; `startForeground` до старта сессии |
| Связь с UI | process-wide `SignalBus` (`StateFlow<SignalState>`), UI — `collectAsStateWithLifecycle`; Binder не нужен |
| Каналы | `signals` (HIGH, «Вызов»), `status` (LOW, постоянное уведомление сервиса) |
| `press` | дедуп `(from, seq, t_epoch)`; уведомление с серверным `t_epoch`; `ack(delivered)` отправляется даже на дубликат (потерянный ack) |
| UI | адрес сервера, Connect/Disconnect, `WS: …`, `Button: online/offline` + состояние, `App: online/offline`, offset часов, список нажатий (`#seq`, server/local, from), баннеры `AUTH FAIL` / `NO BUTTON` |
| Разрешения | `POST_NOTIFICATIONS` запрашивается на Connect (Android 13+); отказ не блокирует сокет и `ack` |
| Зависимости | `androidx.lifecycle:lifecycle-runtime-compose:2.10.0` |

**Эмулятор Android 16 (API 36), KVM:**

- `WS: connected`, `Button: online / WAIT ETA`, `App: online`;
- `press` → уведомление `signals` («Вызов / Нажатие в HH:MM:SS») и `ack(delivered)`;
- фон и погашенный экран: сервис `isForeground=true`, `types=0x40000000` (`specialUse`), уведомление приходит;
- Disconnect: сервис остановлен, FGS-уведомление убрано, UI `WS: idle`, список очищен;
- ребут кнопки (`seq` снова 1): нажатие не теряется.

**Реальный CMF Phone 1 (`A015`, Android 16 / API 36):**

- установка через `adb`, `POST_NOTIFICATIONS` выдан, `app_id 07d4a347`;
- Doze-whitelist: `user,io.nightcall.app`;
- сервис `isForeground=true`, `types=0x40000000` (`specialUse`);
- при заблокированном экране (`mWakefulness=Dozing`) 8 нажатий реальной кнопки: `press seq 2…9`, каждое подтверждено `ack(delivered)`, push-уведомления видны (Android сгруппировал в `signals`);
- на кнопке — `WAIT ETA`.

Тестов на JVM: **66** (Protocol 18, Backoff 9, WsClient 2, SignalReducer 28, TimeFormat 9). MVP S0–S3 и S4 (таймер) замкнуты. Дальше S6 (FCM), S5 (BLE), S7 (питание), S8 (стоп-кнопка).

## Реализация S4 (2026-11-26)

| Компонент | Как сделано |
|---|---|
| Команды | process-wide `CommandBus` (`SharedFlow<Command>`): activity → сервис; интенты не используются, чтобы не понижать `START_REDELIVER_INTENT` |
| `timer` | пресеты 15/30/60 мин + поле минут (1…1440) → `timer(seq, duration_s, t_epoch)`; `t_epoch` — серверная шкала |
| Ожидание ack | `CommandPhase.PENDING` → `ack(delivered)` в течение 10 с; иначе `FAILED` с текстом «Button did not answer — time not set.» и повтор с новым `seq` |
| `cancel` | кнопка «Cancel timer» активна в `COUNTDOWN`/`ARRIVED`; тот же 10-секундный watchdog |
| `seq` команд | начинается с `1_000_000`, чтобы `ack(delivered)` нажатия не завершал команду (оба приходят в приложение) |
| Отсчёт | `status.remaining_s` переводится в локальный дедлайн; UI тикает раз в секунду, на нуле — «Time is up.» |
| `ARRIVED` | сервер присылает `status` ровно в `deadline_t` (кнопка сама об этом не сообщает) |

**Проверено на эмуляторе API 36 + реальной плате (fw `0.2.0-s4`):**

- 15 мин → `COUNTDOWN`, «Remaining 00:14:58»; `cancel` → `ARMED`;
- 1 мин → `timer_arrived` ровно в дедлайн → `ARRIVED`, «Time is up.»;
- 2 мин → перезагрузка платы через `mpremote` → `timer: restored COUNTDOWN`, сервер восстановил `COUNTDOWN`, приложение продолжило отсчёт с того же дедлайна;
- `ack(delivered)` от кнопки приходит за 30–90 мс.

## UI MVP

- Статус соединения с сервером (`WS: connected / reconnecting`).
- Статус кнопки: `online/offline`, её `state`.
- Список последних нажатий: метка времени от сервера (`press.t_epoch`) и от кнопки (если есть) + расхождение часов.
- Пресеты 15/30/60 мин и поле минут → `timer(seq, duration_s, t_epoch)`; ожидание `ack(stage=delivered)` 10 с, иначе «Button did not answer — time not set.»; «Cancel timer» в `COUNTDOWN`/`ARRIVED` (§4.6).

## Сеть

- `ws://<host>:8000/ws` без TLS → `android:usesCleartextTraffic="true"` (или network security config с исключениями).
- Адрес сервера — в настройках приложения (плюс дефолт из `BuildConfig`).

## FCM (S6)

- Firebase-проект → `google-services.json` (не в git), плагин `com.google.gms.google-services`, зависимость `firebase-messaging`.
- Сервер шлёт data-сообщение через FCM HTTP v1 → приложение показывает локальное уведомление (тот же канал `signals`).
- **Риск: нужен Google Play Services на телефоне.** На де-Гугла-устройствах FCM не работает — потому Foreground Service с WS держится в любом случае, и уведомление приходит локально.
- Push-токены приложения регистрируются на сервере (сообщение `register_push`, добавляется в `Протокол.md` как расширение).

## BLE-провижининг (S5)

Кнопка — GATT-периферия (свой UUID-сервис + характеристика для записи), приложение — GATT-клиент: пишет JSON `{ssid, psk, server_host, server_port, token, device_name}`. Разрешения Android 12+: `BLUETOOTH_SCAN`, `BLUETOOTH_CONNECT` (runtime). В MVP вместо этого — `config.py` через `mpremote`.
