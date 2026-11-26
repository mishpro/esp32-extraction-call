"""Шаблон конфигурации кнопки (S2, MVP).

Скопируй в ``device/config.py`` и заполни. ``device/config.py`` в ``.gitignore``:
пароль WiFi не должен попадать в репозиторий.

На плату файл копируется как ``config.py`` (файловая система платы плоская)::

    venv/bin/mpremote connect /dev/ttyUSB0 fs cp device/config.py :config.py
"""

# Сеть 2.4 ГГц: ESP32 не поддерживает 5 ГГц.
ssid = "YOUR_WIFI_2G"
psk = "YOUR_PASSWORD"

# LAN-адрес станции с сервером (не телефона).
server_host = "192.168.1.71"
server_port = 8000

# Часовой пояс для времени на экране (МСК = 3, UTC = 0).
utc_offset_h = 3

# §4.1 требует непустой token даже при AUTH_REQUIRED=false:
# пустая строка -> error(bad_request) + close 4400.
token = "dev-token"

# 8 lowercase hex; главное — стабильность между перезапусками.
# Удобно взять последние 4 байта MAC платы (44:1d:64:f5:f2:4c -> 64f5f24c).
device_id = "64f5f24c"
