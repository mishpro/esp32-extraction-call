"""WiFi station setup with a bounded wait.

``wlan.connect()`` is asynchronous in MicroPython: it only starts the attempt,
so the loop below waits for ``isconnected()``. On failure the caller retries;
the interface is left active to avoid a full radio restart on every attempt.

The wait counts iterations instead of reading a clock: the ESP32 port has no
``time.monotonic()``, and this keeps the function free of tick wraparound math.
"""

import time

try:
    import network
except ImportError:  # CPython
    network = None

STEP_MS = 200


def connect(ssid, psk, timeout_s=20):
    """Return a connected WLAN object, or None after ``timeout_s``."""
    if network is None:
        return None
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    try:
        wlan.disconnect()
    except OSError:
        pass
    wlan.connect(ssid, psk)
    attempts = int(timeout_s * 1000 / STEP_MS)
    while not wlan.isconnected():
        if attempts <= 0:
            return None
        attempts -= 1
        time.sleep_ms(STEP_MS)
    return wlan
