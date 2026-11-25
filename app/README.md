# Guidecaller — Android app (S3, шаги 3.1–3.4)

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
./gradlew assembleDebug -Pguidecaller.url=ws://192.168.1.50:8000/ws -Pguidecaller.token=<token>
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

Тесты: 29 JVM (`ProtocolTest` 18, `BackoffTest` 9, `WsClientTest` 2 — MockWebServer
проверяет, что `hello` уходит первым кадром, `hello_ok` переводит в Connected,
а `ping` получает `pong`).

Не входит (следующие шаги S3): `SignalService` (3.5), каналы уведомлений и `ack(delivered)`
на `press` (3.6), список нажатий/статус кнопки (3.7), `installDebug` на телефон (3.8).
