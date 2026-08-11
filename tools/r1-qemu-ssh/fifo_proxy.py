#!/usr/bin/env python3
"""Bridge OpenSSH ProxyCommand stdio to two validator-owned FIFOs."""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path


def copy_fd(source: int, destination: int) -> None:
    while True:
        block = os.read(source, 65536)
        if not block:
            return
        view = memoryview(block)
        while view:
            written = os.write(destination, view)
            view = view[written:]


def bridge(to_server: Path, from_server: Path, ready: Path | None = None) -> None:
    if not to_server.is_fifo() or not from_server.is_fifo():
        raise RuntimeError("both proxy paths must be existing FIFOs")
    outbound = os.open(to_server, os.O_WRONLY)
    inbound = os.open(from_server, os.O_RDONLY)
    if ready is not None:
        ready.write_text(f"{os.getpid()}\n", encoding="ascii")
    sender = threading.Thread(
        target=copy_fd, args=(sys.stdin.fileno(), outbound), daemon=True
    )
    sender.start()
    try:
        copy_fd(inbound, sys.stdout.fileno())
    finally:
        os.close(inbound)
        os.close(outbound)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("to_server", type=Path)
    parser.add_argument("from_server", type=Path)
    parser.add_argument("--ready", type=Path)
    args = parser.parse_args()
    try:
        bridge(args.to_server, args.from_server, args.ready)
    except (OSError, RuntimeError) as error:
        print(f"fifo_proxy.py: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
