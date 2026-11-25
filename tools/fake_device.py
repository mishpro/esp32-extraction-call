#!/usr/bin/env python3
"""Guidecaller device simulator (CPython + websockets).

Connects to the server, sends ``hello`` (role=device), presses the imaginary
button on Enter or on a timer, answers protocol pings and prints every incoming
frame. Used as the "button without hardware" check for stage S1.

Examples::

    # interactive: Enter = press, q = quit
    python tools/fake_device.py --url ws://127.0.0.1:8001/ws

    # a press every 0.5 s, three presses total
    python tools/fake_device.py --url ws://127.0.0.1:8001/ws --interval 0.5 --count 3

    # fake app that auto-acks deliveries (for the full press -> delivered flow)
    python tools/fake_device.py --role app --id deadbeef --auto-ack

The library's own WebSocket-level ping is disabled on purpose: the protocol
heartbeat (``{"t":"ping"}``) is what should be exercised here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import suppress
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guidecaller device/app simulator")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--id", default="a1b2c3d4", help="8 hex chars")
    parser.add_argument("--token", default="dev-token", help="never printed")
    parser.add_argument("--role", choices=("device", "app"), default="device")
    parser.add_argument("--fw", default="1.0.0-sim")
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="seconds between presses; omit for Enter-driven mode",
    )
    parser.add_argument("--count", type=int, default=None, help="stop after N presses")
    parser.add_argument(
        "--auto-ack",
        action="store_true",
        help="app role only: answer every press with ack(stage=delivered)",
    )
    return parser


def hello_frame(args: argparse.Namespace) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "v": 1,
        "t": "hello",
        "role": args.role,
        "token": args.token,
        "id": args.id,
    }
    if args.role == "device":
        frame["fw"] = args.fw
    return frame


async def run(args: argparse.Namespace) -> int:
    seq = 0
    send_lock = asyncio.Lock()

    async with connect(
        args.url,
        ping_interval=None,
        open_timeout=10,
        close_timeout=2,
        max_size=1 << 20,
    ) as ws:

        async def send(frame: dict[str, Any]) -> None:
            async with send_lock:
                await ws.send(json.dumps(frame, separators=(",", ":")))

        async def send_press() -> None:
            nonlocal seq
            seq += 1
            await send({"v": 1, "t": "press", "seq": seq, "t_epoch": int(time.time())})
            print(f">>> press seq={seq}", flush=True)

        await send(hello_frame(args))
        print(f">>> hello role={args.role} id={args.id} (token hidden)", flush=True)

        async def reader() -> None:
            try:
                async for raw in ws:
                    print(f"<<< {raw}", flush=True)
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if frame.get("t") == "ping":
                        await send(
                            {
                                "v": 1,
                                "t": "pong",
                                "t_epoch": frame.get("t_epoch", int(time.time())),
                            }
                        )
                    elif (
                        args.role == "app"
                        and args.auto_ack
                        and frame.get("t") == "press"
                    ):
                        await send(
                            {
                                "v": 1,
                                "t": "ack",
                                "seq": frame["seq"],
                                "ok": True,
                                "stage": "delivered",
                            }
                        )
                        print(f">>> ack seq={frame['seq']} stage=delivered", flush=True)
            except ConnectionClosed as exc:
                print(f"--- connection closed: {exc}", flush=True)

        async def presser() -> None:
            if args.role != "device":
                await asyncio.Event().wait()
                return
            if args.interval is not None:
                presses = 0
                while args.count is None or presses < args.count:
                    await send_press()
                    presses += 1
                    await asyncio.sleep(args.interval)
                return
            while args.count is None or seq < args.count:
                try:
                    line = await asyncio.to_thread(
                        input, "press> (Enter = press, q = quit) "
                    )
                except EOFError:
                    return
                if line.strip().lower() in {"q", "quit", "exit"}:
                    return
                await send_press()

        reader_task = asyncio.create_task(reader())
        presser_task = asyncio.create_task(presser())
        done, pending = await asyncio.wait(
            {reader_task, presser_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        print(f"connection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
