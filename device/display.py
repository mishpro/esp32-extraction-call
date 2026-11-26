"""One-screen SSD1306 view: status bar + state + last event (128x64, 16x8 font).

Layout (6 rows of 10 px, separator under the status bar)::

    WS:OK APP:ON      <- connection + app presence
    TIME 10:42:56     <- server time (offset from config.utc_offset_h)
    STATE: ARMED      <- or NO APP / AUTH FAIL notice
    LAST 10:42:56     <- last press / delivered ack
    SEQ 7

``screen_lines()`` is pure and tested on CPython; [Display] only adds the
SSD1306 driver. The built-in framebuf font is 8x8, so 128 px = 16 characters.
"""

try:
    import ssd1306
except ImportError:  # CPython: only screen_lines() is usable
    ssd1306 = None

LINE_H = 10
COLS = 16


def screen_lines(model):
    """Build the five text lines for a state model (each <= 16 chars)."""
    ws = "OK" if model.get("ws") else "ERR"
    app = "ON" if model.get("app") else "OFF"
    state = model.get("state", "BOOT")
    if state == "WAIT_ETA":
        state = "WAIT ETA"
    time_text = model.get("time", "--:--:--")
    last_text = model.get("last", "--:--:--")
    seq = model.get("seq", 0)
    lines = [
        f"WS:{ws} APP:{app}",
        f"TIME {time_text}",
        f"STATE: {state}",
        f"LAST {last_text}",
        f"SEQ {seq}",
    ]
    notice = model.get("notice")
    if notice:
        lines[2] = notice
    return [line[:COLS] for line in lines]


class Display:
    def __init__(self, i2c, width=128, height=64, addr=0x3C):
        if ssd1306 is None:
            raise RuntimeError("ssd1306 driver is not available")
        self.i2c = i2c
        self.width = width
        self.height = height
        self.addr = addr
        self.oled = None
        self._open()

    def _open(self):
        self.oled = ssd1306.SSD1306_I2C(self.width, self.height, self.i2c, self.addr)

    def render(self, model):
        # A loose GND / hot-unplug raises OSError (ENODEV) from show(). The
        # firmware must keep running: drop the driver and retry on the next
        # frame, so the screen recovers when it is plugged back in.
        if self.oled is None:
            try:
                self._open()
            except OSError:
                return
        try:
            oled = self.oled
            oled.fill(0)
            y = 0
            for line in screen_lines(model):
                if line:
                    oled.text(line, 0, y)
                y += LINE_H
            oled.hline(0, 9, self.width, 1)
            oled.show()
        except OSError:
            self.oled = None
