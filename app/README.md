# Nightcall — Android app (S3, шаги 3.1–3.4)

Нативный Android-клиент: Kotlin + Compose, OkHttp WebSocket, протокол v1.

## Сборка

```bash
export JAVA_HOME="$HOME/.local/opt/jdk-17.0.21.1+2"
export ANDROID_HOME="$HOME/Android/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH"

cd app
./gradlew assembleDebug        # APK: app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest    # JVM-тесты протокола и backoff
```

Wrapper сам скачивает Gradle 9.8.2 (checksum закреплён в `gradle/wrapper/gradle-wrapper.properties`).
Нужны SDK platform `android-37.2` (compileSdk) и build-tools 36.0.0.

## Версии

| Компонент | Версия |
|---|---|
| AGP | 9.6.0-alpha06 (стабильного 9.6.0 ещё нет) |
| Gradle | 9.8.2 |
| Kotlin (built-in в AGP) | 2.4.20 |
| Compose BOM | 2026.11.01 |
| OkHttp | 5.5.1 |
| kotlinx-serialization-json | 1.13.0 |
| compileSdk / targetSdk / minSdk | 37.2 / 36 / 26 |

`targetSdk` остался 36 по plan/app.md; `compileSdk` поднят до 37.2, потому что OkHttp 5.5.1
и текущие AndroidX/Compose требуют компиляции против API 37.1+.

## Токен и адрес сервера

Секретов в коде нет. Дефолт для сборки задаётся Gradle-свойствами (в git не попадают):

```bash
./gradlew assembleDebug -Pnightcall.url=ws://192.168.1.50:8000/ws -Pnightcall.token=<token>
```

**В MVP токен — заглушка `dev-token`.** Авторизация на сервере выключена
(`AUTH_REQUIRED=false`), значение не проверяется, но §4.1 требует непустую строку:
пустой `token` — это `error(bad_request)` и close `4400`. Заглушка задана по умолчанию,
чтобы приложение подключалось без дополнительных флагов; реальный секрет появится
при провижининге (S5/S6).

`app_id` генерируется при первом запуске (8 lowercase hex) и хранится в
`EncryptedSharedPreferences`; его видно на экране — этот id нужно прописать на сервере
(вместе с токеном, когда авторизация включена).

## Что реализовано в этом этапе

- `net/Protocol.kt` — типы всех 10 сообщений, константы состояний/кодов ошибок/close-кодов,
  строгий разбор по схеме (unknown fields/type/version/диапазоны).
- `net/Backoff.kt` — чистая функция задержки переподключения (§6).
- `net/WsClient.kt` — `hello` первым кадром, `pong` на `ping`, hello-таймаут 10 с,
  reconnect с backoff (сброс после `hello_ok`), `auth_failed` → без reconnect, `StateFlow` + `SharedFlow`.
- `data/SecureStore.kt` — `app_id` и `token` в `EncryptedSharedPreferences`.
- `ui/MainActivity.kt` — поле адреса, кнопки Connect/Disconnect, строка статуса WS.

Тесты: **52 JVM** (`ProtocolTest` 18, `BackoffTest` 9, `WsClientTest` 2,
`SignalReducerTest` 16, `TimeFormatTest` 7). `WsClientTest` через MockWebServer
проверяет, что `hello` уходит первым кадром, `hello_ok` переводит в Connected,
а `ping` получает `pong`.

## Сервис и уведомления (S3.5–3.6)

- `service/SignalService.kt` — foreground-сервис с типом **`specialUse`**
  (`FOREGROUND_SERVICE_SPECIAL_USE` + `PROPERTY_SPECIAL_USE_FGS_SUBTYPE`;
  `dataSync` не подходит из-за лимита 6 ч/сутки на Android 15+).
  `START_REDELIVER_INTENT`, `startForeground` до старта сессии.
- `service/SignalBus.kt` — process-wide `StateFlow<SignalState>`, UI читает через
  `collectAsStateWithLifecycle`; Binder не нужен.
- `service/SignalReducer.kt` — чистые переходы: offset из `hello_ok`/`status`,
  дедуп нажатий по `(from, seq, t_epoch)` (seq сбрасывается при перезагрузке кнопки),
  лимит 100, `auth_failed`/`no_peer`.
- `notify/Notifications.kt` — каналы `signals` (HIGH, «Вызов» + серверное время) и
  `status` (LOW, постоянное уведомление сервиса). На Android 13+ нужно
  `POST_NOTIFICATIONS`; отказ не блокирует сокет и `ack`.
- `press` → уведомление → `ack(seq, stage=delivered)`. Ack отправляется и на
  дубликат `seq`: это признак потерянного подтверждения.

## Экран (S3.7)

- Адрес сервера (дефолт из `BuildConfig`), Connect/Disconnect.
- `WS: …`, `Button: online/offline` + состояние, `App: online/offline`,
  offset серверных часов, баннеры `AUTH FAIL` / `NO BUTTON`.
- Список последних нажатий: `#seq`, время сервера, локальное время, `from`.
- `ui/TimeFormat.kt` — `HH:mm:ss` через `java.time` (minSdk 26).

## Проверка на эмуляторе

```bash
# сервер на хосте
cd server && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --ws-ping-interval 3600 --ws-ping-timeout 3600

# сборка с адресом эмулятора (10.0.2.2 = loopback хоста)
cd app && ./gradlew assembleDebug -Pnightcall.url=ws://10.0.2.2:8000/ws
./gradlew --stop          # освободить RAM перед AVD

emulator -avd nightcall -no-window -no-audio -no-boot-anim \
    -gpu swiftshader_indirect -no-snapshot -memory 1536 &
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell pm grant io.nightcall.app android.permission.POST_NOTIFICATIONS
adb shell am start -n io.nightcall.app/.ui.MainActivity
```

На эмуляторе проверено: connected, уведомление при нажатии в фоне и с погашенным
экраном, `ack(delivered)`, Disconnect, повторный `seq=1` после «перезагрузки» кнопки.

**S4 (таймер), эмулятор API 36 + реальная плата:** 15 мин → `COUNTDOWN` и
«Remaining 00:14:58», `cancel` → `ARMED`; 1 мин → `timer_arrived` ровно в дедлайн,
`ARRIVED` и «Time is up.»; перезагрузка платы посреди 2-минутного отсчёта →
`timer: restored COUNTDOWN` и тот же дедлайн. `ack(delivered)` на `timer` — 30–90 мс.

**CMF Phone 1 (`A015`, Android 16, API 36):** установлено через `adb`, `app_id 07d4a347`,
`POST_NOTIFICATIONS` выдан, Doze-whitelist:

```bash
adb shell dumpsys deviceidle whitelist +io.nightcall.app
```

Сервис `isForeground=true`, `types=0x40000000` (`specialUse`). При заблокированном
экране 8 нажатий реальной кнопки — все `press seq 2…9` подтверждены `ack(delivered)`,
push-уведомления видны (Android группирует их в `signals`). На кнопке `WAIT ETA`.

В Nothing OS дополнительно выставь **Приложения → Nightcall → Батарея → Без ограничений**.
Если на локскрине не видно текст уведомления — это настройка приватности Android,
а не приложения; heads-up и звук приходят.

Осталось: FCM — S6.


