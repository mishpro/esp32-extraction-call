"""BTN1 debounce: one physical press -> one event.

The debouncer is pure (level + timestamp in, bool out) and tested on CPython;
[Button] only adds ``machine.Pin``. Idle level is 1 (pull-up), a press pulls
the pin to 0, so the event fires on the debounced falling edge.
"""

import time

try:
    from machine import Pin
except ImportError:  # CPython: only Debouncer is usable
    Pin = None

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
except AttributeError:  # CPython fallback
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


class Debouncer:
    """Stable-level debounce with a single event per press.

    ``update(level, now_ms)`` returns True exactly once, when the pin has been
    low for ``debounce_ms``. Releasing (low -> high) never produces an event.
    """

    __slots__ = ("debounce_ms", "level", "candidate", "changed_at")

    def __init__(self, debounce_ms=40):
        self.debounce_ms = debounce_ms
        self.level = 1
        self.candidate = 1
        self.changed_at = 0

    def set_level(self, level):
        """Adopt the current pin level without emitting an event.

        Used at start-up: a button held down (or a shorted pin) must not look
        like a fresh press on the first poll.
        """
        self.level = 1 if level else 0
        self.candidate = self.level
        self.changed_at = 0

    def update(self, level, now_ms):
        level = 1 if level else 0
        if level != self.candidate:
            self.candidate = level
            self.changed_at = now_ms
            return False
        if self.candidate == self.level:
            return False
        if ticks_diff(now_ms, self.changed_at) < self.debounce_ms:
            return False
        previous = self.level
        self.level = self.candidate
        return previous == 1 and self.level == 0


class Button:
    """GPIO button with an internal pull-up (BTN1 = GPIO4)."""

    def __init__(self, pin_no=4, debounce_ms=40):
        if Pin is None:
            raise RuntimeError("machine.Pin is not available")
        self.pin = Pin(pin_no, Pin.IN, Pin.PULL_UP)
        self.debouncer = Debouncer(debounce_ms)
        self.debouncer.set_level(self.pin.value())

    def poll(self, now_ms=None):
        if now_ms is None:
            now_ms = ticks_ms()
        return self.debouncer.update(self.pin.value(), now_ms)
