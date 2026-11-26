"""NVS persistence of the countdown deadline (§4.4, §8).

The deadline is the only thing that must survive a power cycle: after a reboot
the button re-reads it and keeps counting (``state.StateMachine.restore``).
MicroPython's ESP32 port exposes NVS as ``esp32.NVS``; on CPython (tests) the
import fails and the store becomes a no-op, so the pure modules stay testable.
"""

KEY = "deadline_t"


class TimerStore:
    def __init__(self, namespace="nightcall"):
        try:
            import esp32

            self._nvs = esp32.NVS(namespace)
        except (ImportError, AttributeError, OSError):
            self._nvs = None

    def load(self):
        """Stored deadline (epoch seconds) or None when absent/unavailable."""
        if self._nvs is None:
            return None
        try:
            value = self._nvs.get_i32(KEY)
        except (OSError, ValueError):
            return None
        return value if value > 0 else None

    def save(self, deadline_t):
        if self._nvs is None:
            return
        try:
            self._nvs.set_i32(KEY, int(deadline_t))
            self._nvs.commit()
        except (OSError, ValueError):
            pass

    def clear(self):
        if self._nvs is None:
            return
        try:
            self._nvs.erase_key(KEY)
            self._nvs.commit()
        except (OSError, ValueError):
            pass
