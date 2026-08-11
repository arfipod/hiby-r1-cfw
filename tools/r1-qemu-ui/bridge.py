#!/usr/bin/env python3
"""Display a qemu-user R1 framebuffer and inject mouse-as-touch events."""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


WIDTH = 480
HEIGHT = 800
FRAME_STRIDE = 1920
ACTIVE_LINE_BYTES = WIDTH * 2
FRAME_BYTES = FRAME_STRIDE * HEIGHT
FRAME_STATE_MAGIC = 0x52314642
FRAME_STATE = struct.Struct("<4I")
INPUT_EVENT = struct.Struct("<llHHl")

EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0
SYN_MT_REPORT = 2
BTN_TOUCH = 330
ABS_MT_TOUCH_MAJOR = 48
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54
ABS_MT_TRACKING_ID = 57
ABS_MT_PRESSURE = 58


def active_yoffset(state_path: Path | None) -> tuple[int, int]:
    if state_path is None:
        return (0, 0)
    try:
        data = state_path.read_bytes()[: FRAME_STATE.size]
    except OSError:
        return (0, 0)
    if len(data) != FRAME_STATE.size:
        return (0, 0)
    magic, version, yoffset, sequence = FRAME_STATE.unpack(data)
    if magic != FRAME_STATE_MAGIC or version != 1 or yoffset not in {0, HEIGHT}:
        return (0, 0)
    return (yoffset, sequence)


def decode_framebuffer(framebuffer: Path, yoffset: int = 0) -> Any:
    """Decode the R1's active RGB565 pixels from its 1920-byte scanlines."""

    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required for framebuffer display") from error
    with framebuffer.open("rb") as stream:
        stream.seek(yoffset * FRAME_STRIDE)
        frame = stream.read(FRAME_BYTES)
    if len(frame) != FRAME_BYTES:
        raise RuntimeError(
            f"short framebuffer: got {len(frame)} bytes, expected {FRAME_BYTES}"
        )
    active = b"".join(
        frame[row * FRAME_STRIDE : row * FRAME_STRIDE + ACTIVE_LINE_BYTES]
        for row in range(HEIGHT)
    )
    # The fbdev contract reports 32 bpp and a 1920-byte stride, while the HGL
    # DMA renderer writes 480 packed little-endian RGB565 pixels at the start
    # of each scanline. Pillow names that byte ordering BGR;16.
    return Image.frombytes("RGB", (WIDTH, HEIGHT), active, "raw", "BGR;16")


def input_event(event_type: int, code: int, value: int) -> bytes:
    return INPUT_EVENT.pack(0, 0, event_type, code, value)


def touch_events(x: int, y: int, phase: str) -> bytes:
    x = max(0, min(WIDTH, int(x)))
    y = max(0, min(HEIGHT, int(y)))
    if phase == "up":
        # Match the hardware-tested R1 injector exactly. The stock HGL input
        # path commits release on BTN_TOUCH=0; a synthetic tracking-id teardown
        # changes proprietary launcher gesture dispatch.
        return b"".join(
            (
                input_event(EV_KEY, BTN_TOUCH, 0),
                input_event(EV_SYN, SYN_MT_REPORT, 0),
                input_event(EV_SYN, SYN_REPORT, 0),
            )
        )
    if phase not in {"down", "move"}:
        raise RuntimeError(f"unsupported touch phase: {phase}")
    events = [
        input_event(EV_ABS, ABS_MT_TRACKING_ID, 0),
        input_event(EV_ABS, ABS_MT_PRESSURE, 63),
        input_event(EV_ABS, ABS_MT_TOUCH_MAJOR, 9),
        input_event(EV_ABS, ABS_MT_POSITION_X, x),
        input_event(EV_ABS, ABS_MT_POSITION_Y, y),
        input_event(EV_SYN, SYN_MT_REPORT, 0),
    ]
    if phase == "down":
        events.append(input_event(EV_KEY, BTN_TOUCH, 1))
    events.append(input_event(EV_SYN, SYN_REPORT, 0))
    return b"".join(events)


def inject_touch(path: Path, x: int, y: int, phase: str) -> int:
    data = touch_events(x, y, phase)
    endpoints = sorted(
        candidate
        for candidate in path.parent.glob(path.name + ".*")
        if candidate.is_fifo()
    )
    if not endpoints:
        endpoints = [path]
    total = 0
    errors: list[str] = []
    for endpoint in endpoints:
        try:
            descriptor = os.open(endpoint, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            errors.append(f"{endpoint}: {error}")
            continue
        try:
            written = os.write(descriptor, data)
        finally:
            os.close(descriptor)
        if written != len(data):
            errors.append(f"{endpoint}: short write {written} != {len(data)}")
            continue
        total += written
    if total == 0:
        detail = "; ".join(errors) if errors else "no live endpoints"
        raise RuntimeError(f"cannot inject touch through {path}: {detail}")
    return total


BRIDGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HiBy R1 QEMU UI bridge</title>
<style>
 :root { color-scheme: dark; font: 14px system-ui,sans-serif; }
 body { margin: 0; background: #11151b; color: #e8edf3; text-align: center; }
 header { padding: .8rem; background: #1a2029; }
 #screen { display: block; width: 480px; height: 800px; margin: 1rem auto;
   background: black; box-shadow: 0 8px 35px #000a; touch-action: none; user-select: none; }
 #status { color: #9eabb9; }
</style>
</head>
<body>
<header><strong>Real hiby_player under qemu-user</strong> · mouse/pointer is injected as evdev touch<br>
<span id="status">Waiting for framebuffer…</span></header>
<img id="screen" draggable="false" alt="R1 framebuffer">
<script>
const screen = document.querySelector('#screen');
const status = document.querySelector('#status');
let pointerDown = false, lastMove = 0;
function refresh() { screen.src = '/frame.png?n=' + Date.now(); }
setInterval(refresh, 100); refresh();
async function send(phase, event) {
  const rect = screen.getBoundingClientRect();
  const x = Math.floor((event.clientX - rect.left) * 480 / rect.width);
  const y = Math.floor((event.clientY - rect.top) * 800 / rect.height);
  try {
    const response = await fetch('/touch', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({phase,x,y})});
    const value = await response.json();
    status.textContent = `${phase} x=${x} y=${y} · frame ${value.sequence}`;
  } catch (error) { status.textContent = error; }
}
screen.addEventListener('pointerdown', event => {
  pointerDown = true; screen.setPointerCapture(event.pointerId); send('down', event);
});
screen.addEventListener('pointermove', event => {
  if (!pointerDown || performance.now()-lastMove < 25) return;
  lastMove = performance.now(); send('move', event);
});
function release(event) { if (pointerDown) { pointerDown=false; send('up', event); } }
screen.addEventListener('pointerup', release); screen.addEventListener('pointercancel', release);
</script>
</body></html>
"""


def command_capture(args: argparse.Namespace) -> None:
    yoffset, sequence = active_yoffset(args.state)
    image = decode_framebuffer(args.framebuffer, yoffset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG", optimize=False)
    print(f"captured yoffset={yoffset} sequence={sequence}: {args.output}")


def command_touch(args: argparse.Namespace) -> None:
    phases = ("down",) + ("move",) * 7 + ("up",) if args.phase == "tap" else (args.phase,)
    total = 0
    for index, phase in enumerate(phases):
        total += inject_touch(args.touch_fifo, args.x, args.y, phase)
        if index + 1 < len(phases) and args.interval_ms:
            time.sleep(args.interval_ms / 1000)
    print(f"injected {args.phase} x={args.x} y={args.y}: {total} bytes")


def command_serve(args: argparse.Namespace) -> None:
    framebuffer = args.framebuffer.resolve()
    state = args.state.resolve() if args.state else None
    touch_fifo = args.touch_fifo.resolve()

    class Handler(BaseHTTPRequestHandler):
        def send_content(
            self, content: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            route = urllib.parse.urlparse(self.path).path
            try:
                if route == "/":
                    self.send_content(BRIDGE_HTML.encode(), "text/html; charset=utf-8")
                    return
                if route == "/frame.png":
                    yoffset, _sequence = active_yoffset(state)
                    image = decode_framebuffer(framebuffer, yoffset)
                    output = io.BytesIO()
                    image.save(output, format="PNG", optimize=False)
                    self.send_content(output.getvalue(), "image/png")
                    return
                if route == "/status":
                    yoffset, sequence = active_yoffset(state)
                    self.send_content(
                        json.dumps({"yoffset": yoffset, "sequence": sequence}).encode(),
                        "application/json",
                    )
                    return
                self.send_content(b"not found\n", "text/plain", HTTPStatus.NOT_FOUND)
            except Exception as error:
                self.send_content(
                    (str(error) + "\n").encode(),
                    "text/plain; charset=utf-8",
                    HTTPStatus.BAD_REQUEST,
                )

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if urllib.parse.urlparse(self.path).path != "/touch":
                self.send_content(b"not found\n", "text/plain", HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise RuntimeError("touch request must be an object")
                phase = str(value.get("phase"))
                x = int(value.get("x"))
                y = int(value.get("y"))
                written = inject_touch(touch_fifo, x, y, phase)
                _yoffset, sequence = active_yoffset(state)
                self.send_content(
                    json.dumps({"written": written, "sequence": sequence}).encode(),
                    "application/json",
                )
            except Exception as error:
                self.send_content(
                    json.dumps({"error": str(error)}).encode(),
                    "application/json",
                    HTTPStatus.BAD_REQUEST,
                )

        def log_message(self, format: str, *values: Any) -> None:
            if args.verbose:
                super().log_message(format, *values)

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    host, port = server.server_address[:2]
    visible_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"HiBy R1 QEMU UI bridge: http://{visible_host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="save the active framebuffer page as PNG")
    capture.add_argument("framebuffer", type=Path)
    capture.add_argument("output", type=Path)
    capture.add_argument("--state", type=Path)
    capture.set_defaults(function=command_capture)

    touch = subparsers.add_parser("touch", help="inject one touch phase or a complete tap")
    touch.add_argument("touch_fifo", type=Path)
    touch.add_argument("x", type=int)
    touch.add_argument("y", type=int)
    touch.add_argument("--phase", choices=("tap", "down", "move", "up"), default="tap")
    touch.add_argument(
        "--interval-ms",
        type=int,
        default=40,
        help="delay between tap frames (default: 40; ignored for one phase)",
    )
    touch.set_defaults(function=command_touch)

    serve = subparsers.add_parser("serve", help="serve live video and mouse-as-touch input")
    serve.add_argument("framebuffer", type=Path)
    serve.add_argument("touch_fifo", type=Path)
    serve.add_argument("--state", type=Path)
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766, help="use 0 to choose a free port")
    serve.add_argument("--verbose", action="store_true")
    serve.set_defaults(function=command_serve)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.function(args)
    except RuntimeError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
