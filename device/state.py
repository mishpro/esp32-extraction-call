"""Local button state machine and server clock (MicroPython-safe, no imports).

Wire states (Протокол §4.2) are ``BOOT``, ``OFFLINE``, ``ARMED``, ``SENT``,
``WAIT_ETA``, ``NO_ACK``. ``NO APP`` and ``AUTH FAIL`` are local display-only
states from §4.9. The module has no imports, so it is unit-tested on CPython.
"""

BOOT = "BOOT"
OFFLINE = "OFFLINE"
ARMED = "ARMED"
SENT = "SENT"
WAIT_ETA = "WAIT_ETA"
NO_ACK = "NO_ACK"
NO_APP = "NO APP"
AUTH_FAIL = "AUTH FAIL"

# A press is accepted when idle or when the previous delivery failed/arrived.
# While SENT the button waits for ack(delivered) to avoid double presses.
PRESSABLE = (ARMED, WAIT_ETA, NO_ACK)

NO_APP_MS = 5000
TICKS_MASK = 0x3FFFFFFF  # MicroPython ticks wrap at 2^30 ms (~12 days)


def fmt_hms(epoch_sec):
    """``HH:MM:SS`` from Unix epoch seconds (None -> ``--:--:--``)."""
    if epoch_sec is None:
        return "--:--:--"
    sec = int(epoch_sec) % 86400
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


class Clock:
    """Server time from ``t_epoch`` + a monotonic tick counter.

    The ESP32 RTC has no NTP and ``time.time()`` counts from 2000-01-01, so the
    wall clock is never read: the latest server ``t_epoch`` is the base and the
    elapsed time comes from ``ticks_ms`` (wrap-safe).
    """

    __slots__ = ("offset_h", "epoch", "base_ms")

    def __init__(self, offset_h=0):
        self.offset_h = offset_h
        self.epoch = None
        self.base_ms = 0

    def sync(self, t_epoch, now_ms):
        self.epoch = t_epoch
        self.base_ms = now_ms

    def now(self, now_ms):
        if self.epoch is None:
            return None
        elapsed = (now_ms - self.base_ms) & TICKS_MASK
        return self.epoch + elapsed // 1000

    def hms(self, now_ms):
        return self.hms_of(self.now(now_ms))

    def hms_of(self, epoch_sec):
        if epoch_sec is None:
            return "--:--:--"
        return fmt_hms(epoch_sec + self.offset_h * 3600)


class StateMachine:
    """Transitions for one button.

    ``press()`` returns the next ``seq`` or ``None`` when a press must be
    ignored. ``tick()`` expires the NO APP notice and turns a stale SENT into
    NO_ACK after ``ack_timeout_ms`` (10 s, mirroring the app's ack timeout).
    """

    __slots__ = (
        "state",
        "app_online",
        "seq",
        "last_epoch",
        "notice",
        "notice_until",
        "sent_at",
        "ack_timeout_ms",
    )

    def __init__(self, ack_timeout_ms=10000):
        self.state = BOOT
        self.app_online = False
        self.seq = 0
        self.last_epoch = None
        self.notice = None
        self.notice_until = 0
        self.sent_at = 0
        self.ack_timeout_ms = ack_timeout_ms

    def ws_connected(self, now_ms, app_online):
        self.state = ARMED
        self.app_online = bool(app_online)
        self.notice = None
        self.sent_at = 0

    def ws_lost(self):
        if self.state != AUTH_FAIL:
            self.state = OFFLINE
        self.app_online = False
        self.sent_at = 0

    def auth_failed(self):
        self.state = AUTH_FAIL
        self.app_online = False
        self.notice = None
        self.sent_at = 0

    def press(self, now_ms):
        if self.state not in PRESSABLE:
            return None
        self.seq += 1
        self.state = SENT
        self.sent_at = now_ms
        self.notice = None
        return self.seq

    def on_ack(self, stage, ok, now_ms):
        if stage == "server":
            return
        self.sent_at = 0
        self.state = WAIT_ETA if ok else ARMED

    def on_error(self, code, now_ms):
        if code == "no_peer":
            self.notice = NO_APP
            self.notice_until = now_ms + NO_APP_MS
            self.state = ARMED
            self.sent_at = 0
        elif code == "auth_failed":
            self.auth_failed()
        # duplicate / bad_duration / bad_request: ignore here, the transport
        # closes the session for fatal protocol errors.

    def on_status(self, app_online):
        self.app_online = bool(app_online)

    def tick(self, now_ms):
        if self.notice is not None and ((now_ms - self.notice_until) & TICKS_MASK) < 0x20000000:
            self.notice = None
        if self.state == SENT and self.sent_at:
            elapsed = (now_ms - self.sent_at) & TICKS_MASK
            if elapsed >= self.ack_timeout_ms:
                self.state = NO_ACK
                self.sent_at = 0
