"""Button entry point: WiFi -> WS hello -> press/ack loop (S2)."""

import gc
import time

import config
import frames
import machine
import net
import proto
import state as state_mod
import store
import ws_client
from button import Button
from display import Display

FW_VERSION = "0.2.0-s4"
LOOP_MS = 50
PING_MS = 20000
GC_MS = 60000
RENDER_MS = 1000
BACKOFF_CAP_S = 10.0
TICKS_MASK = state_mod.TICKS_MASK


def log(*args):
    print(*args)


def main():
    machine.freq(160_000_000)
    i2c = machine.I2C(0, sda=machine.Pin(21), scl=machine.Pin(22))
    display = Display(i2c)
    button = Button(pin_no=4, debounce_ms=40)
    last_level = button.pin.value()

    sm = state_mod.StateMachine()
    clock = state_mod.Clock(getattr(config, "utc_offset_h", 0))
    timer_store = store.TimerStore()
    # Loaded once at boot; applied after the first server clock sync.
    saved_deadline = timer_store.load()

    wlan = None
    client = None
    attempt = 0
    hello_done = False
    last_ping = 0
    last_render = 0
    last_gc = time.ticks_ms()

    def draw(ws_ok):
        now = time.ticks_ms()
        server_now = clock.now(now)
        left = sm.remaining(server_now)
        display.render(
            {
                "ws": ws_ok,
                "app": sm.app_online,
                "state": sm.state,
                "time": clock.hms(now),
                "last": clock.hms_of(sm.last_epoch),
                "seq": sm.seq,
                "notice": sm.notice,
                "left": state_mod.fmt_left(left) if left is not None else None,
            }
        )

    def restore_timer():
        """Apply the deadline loaded from NVS once the server clock is known."""
        nonlocal saved_deadline
        if saved_deadline is None:
            return
        server_now = clock.now(time.ticks_ms())
        if server_now is None:
            return
        applied = sm.restore(saved_deadline, server_now)
        saved_deadline = None
        if applied:
            if sm.state == state_mod.ARRIVED:
                timer_store.clear()
            log("timer: restored " + sm.state)

    def reconnect(reason):
        nonlocal client, attempt, hello_done
        log(f"ws: down ({reason})")
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        client = None
        sm.ws_lost()
        draw(False)
        if hello_done:
            attempt = 0
            hello_done = False
        delay = ws_client.backoff_s(attempt, cap_s=BACKOFF_CAP_S)
        attempt += 1
        log("ws: retry in " + str(round(delay, 1)) + "s")
        time.sleep_ms(int(delay * 1000))

    def stop_auth():
        nonlocal client
        sm.auth_failed()
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        client = None
        draw(False)
        log("auth failed: stopped, reset the board after fixing the token")
        while True:
            time.sleep_ms(60000)

    log(f"boot fw={FW_VERSION} id={config.device_id} heap={gc.mem_free()}")
    draw(False)

    while True:
        now = time.ticks_ms()
        dirty = False
        level = button.pin.value()
        if level != last_level:
            log(f"btn level {level}")
            last_level = level
        if sm.tick(now, clock.now(now)):
            timer_store.clear()
            log("timer: ARRIVED")
            dirty = True

        # -- WiFi ---------------------------------------------------------
        if wlan is None or not wlan.isconnected():
            if wlan is not None:
                log("wifi: lost")
                wlan = None
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
                    client = None
                sm.ws_lost()
                draw(False)
            log(f"wifi: connecting to {config.ssid}")
            try:
                wlan = net.connect(config.ssid, config.psk)
            except Exception as exc:
                log(f"wifi: error {exc}")
                wlan = None
            if wlan is None:
                log("wifi: failed, retry in 2s")
                time.sleep_ms(2000)
                continue
            ip = wlan.ifconfig()[0]
            log(f"wifi: ok ip={ip}")

        # -- WebSocket ----------------------------------------------------
        if client is None:
            client = ws_client.WsClient(config.server_host, config.server_port)
            try:
                client.connect()
                client.send_text(
                    proto.build_hello(
                        "device", config.token, config.device_id, fw=FW_VERSION
                    )
                )
            except (ws_client.WsError, ValueError) as exc:
                log(f"ws: connect failed: {exc}")
                try:
                    client.close()
                except Exception:
                    pass
                client = None
                sm.ws_lost()
                draw(False)
                delay = ws_client.backoff_s(attempt, cap_s=BACKOFF_CAP_S)
                attempt += 1
                log("ws: retry in " + str(round(delay, 1)) + "s")
                time.sleep_ms(int(delay * 1000))
                continue
            hello_done = False
            last_ping = time.ticks_ms()
            log(f"ws: hello sent to {config.server_host}:{config.server_port}")

        # -- events -------------------------------------------------------
        try:
            event = client.read_event(LOOP_MS)
        except ws_client.WsClosed as exc:
            reconnect(f"closed: {exc}")
            continue

        now = time.ticks_ms()

        # First server clock of this boot: resume a countdown from NVS (§4.4).
        restore_timer()

        if event is not None:
            kind, _opcode, payload = event
            if kind == frames.KIND_CLOSE:
                code = frames.close_code(payload)
                if code == 4401:
                    stop_auth()
                reconnect(f"close {code}")
                continue
            if kind == frames.KIND_MESSAGE:
                try:
                    msg = proto.parse_message(payload)
                except proto.ProtocolError as exc:
                    log(f"message: {exc}")
                    msg = None
                if msg is not None:
                    mtype = msg["t"]
                    if mtype == "hello_ok":
                        clock.sync(msg["t_epoch"], now)
                        peer = msg["peer"]
                        sm.ws_connected(now, peer["online"])
                        hello_done = True
                        attempt = 0
                        dirty = True
                        session = msg["session"]
                        peer_id = peer["id"]
                        peer_online = peer["online"]
                        log(f"hello_ok session={session} peer={peer_id}/{peer_online}")
                    elif mtype == "status":
                        clock.sync(msg["t_epoch"], now)
                        sm.on_status(msg["app"]["online"])
                        dirty = True
                    elif mtype == "ack":
                        seq = msg["seq"]
                        stage = msg["stage"]
                        ok = msg["ok"]
                        sm.on_ack(stage, ok, now)
                        if stage == "delivered" and ok:
                            sm.last_epoch = clock.now(now)
                        dirty = True
                        log(f"ack seq={seq} stage={stage} ok={ok}")
                    elif mtype == "timer":
                        # §4.4: the server converts duration_s into deadline_t.
                        seq = msg["seq"]
                        deadline = msg.get("deadline_t")
                        if deadline is None:
                            log("timer: missing deadline_t, ignored")
                        else:
                            sm.on_timer(deadline)
                            timer_store.save(deadline)
                            try:
                                client.send_text(
                                    proto.build_ack(seq, True, "delivered")
                                )
                            except ws_client.WsClosed as exc:
                                reconnect(f"timer ack: {exc}")
                                continue
                            log(f"timer seq={seq} deadline={deadline}")
                            dirty = True
                    elif mtype == "cancel":
                        # §4.5: stop the countdown and go back to ARMED.
                        seq = msg["seq"]
                        sm.on_cancel()
                        timer_store.clear()
                        try:
                            client.send_text(
                                proto.build_ack(seq, True, "delivered")
                            )
                        except ws_client.WsClosed as exc:
                            reconnect(f"cancel ack: {exc}")
                            continue
                        log(f"cancel seq={seq}")
                        dirty = True
                    elif mtype == "error":
                        code = msg["code"]
                        text = msg["msg"]
                        log(f"error code={code} msg={text}")
                        if code == "auth_failed":
                            stop_auth()
                        sm.on_error(code, now)
                        dirty = True
                    elif mtype == "ping":
                        # Server heartbeat: §4.8 requires pong with the same t_epoch.
                        clock.sync(msg["t_epoch"], now)
                        t_epoch = msg["t_epoch"]
                        try:
                            client.send_text(proto.build_pong(t_epoch))
                        except ws_client.WsClosed as exc:
                            reconnect(f"pong: {exc}")
                            continue
                        dirty = True

        # -- button -------------------------------------------------------
        if button.poll(now):
            seq = sm.press(now)
            if seq is None:
                log(f"press ignored in {sm.state}")
            else:
                sm.last_epoch = clock.now(now)
                # A new call drops the finished countdown from NVS too.
                timer_store.clear()
                t_epoch = sm.last_epoch if sm.last_epoch is not None else 0
                try:
                    client.send_text(proto.build_press(seq, t_epoch))
                except ws_client.WsClosed as exc:
                    reconnect(f"send: {exc}")
                    continue
                log(f"press seq={seq}")
                dirty = True

        # -- device heartbeat (§4.8: every 20 s) --------------------------
        if ((now - last_ping) & TICKS_MASK) >= PING_MS:
            last_ping = now
            t_epoch = clock.now(now)
            try:
                client.send_text(
                    proto.build_ping(t_epoch if t_epoch is not None else 0)
                )
            except ws_client.WsClosed as exc:
                reconnect(f"ping: {exc}")
                continue

        # -- render / gc --------------------------------------------------
        if dirty or ((now - last_render) & TICKS_MASK) >= RENDER_MS:
            draw(True)
            last_render = now
        if ((now - last_gc) & TICKS_MASK) >= GC_MS:
            gc.collect()
            last_gc = now
            free = gc.mem_free()
            log(f"heap={free}")


if __name__ == "__main__":
    main()
