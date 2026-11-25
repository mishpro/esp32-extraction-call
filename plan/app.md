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
    src/main/java/…/GuidecallerApp.kt
    src/main/java/…/net/WsClient.kt            # OkHttp WebSocket, reconnect (§6)
    src/main/java/…/net/Protocol.kt            # разбор/сборка сообщений
    src/main/java/…/service/SignalService.kt   # Foreground Service + StateFlow
    src/main/java/…/ui/MainActivity.kt
    src/main/java/…/ui/MainViewModel.kt
    src/main/java/…/notify/Notifications.kt    # каналы signals / status
    src/main/java/…/data/SecureStore.kt        # EncryptedSharedPreferences: app_id, token
    src/main/java/…/ble/ConfigProvisioning.kt  # S5: BLE-клиент (GATT)
    src/main/java/…/fcm/GuidecallerMessaging.kt # S6: FCM
```

## Требования из §9

- **OkHttp WebSocket** — готовый клиент, поддержка ping/pong и колбэков.
- **Foreground Service**: сокет живёт в сервисе, UI подписан на `StateFlow` из сервиса.
- **Токен** — в `EncryptedSharedPreferences` (`androidx.security:security-crypto`), в сокет передаётся первым кадром `hello`. В MVP авторизация выключена, но §4.1 требует `token` длиной ≥ 1: пустая строка даёт close `4400`, поэтому по умолчанию используется заглушка `dev-token` (реальный секрет — при провижининге, S5/S6).
- **Каналы уведомлений**: `signals` (HIGH) — сигнал от кнопки; `status` (LOW) — статусы. На Android 13+ нужно разрешение `POST_NOTIFICATIONS`.

## Протокол в приложении

- `hello` (role=`app`, `id`=app_id, `token`=…) сразу после коннекта; без `hello_ok` в течение 10 с — reconnect с backoff (§6, максимум 60 с).
- `press` от сервера → показать уведомление (канал `signals`) **и** отдать `ack(seq, stage=delivered)` (§4.6).
- `ping` → `pong` в течение 10 с.
- `status` → обновить UI: `device.online`, `device.state`, `app.online`.
- `error` → по коду: `auth_failed` — не переподключаться, показать `AUTH FAIL`; `no_peer` — показать `NO BUTTON`.

## UI MVP

- Статус соединения с сервером (`WS: connected / reconnecting`).
- Статус кнопки: `online/offline`, её `state`.
- Список последних нажатий: метка времени от сервера (`press.t_epoch`) и от кнопки (если есть) + расхождение часов.
- (S4) Поле ввода времени → `timer(seq, duration_s, t_epoch)`, ожидание `ack(stage=delivered)` 10 с, иначе «Кнопка не отвечает, время не установлено» (§4.6).

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
