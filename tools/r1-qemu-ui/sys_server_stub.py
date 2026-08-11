#!/usr/bin/env python3
"""Emulate the small part of Hiby ``sys_server`` needed by qemu-user.

The stock player asks ``sys_server`` to mount a detected MMC block device.  In
the qemu-user harness the SD directory is already exposed at the guest mount
path with a PRoot bind, so performing a real mount would be both unnecessary
and unsafe.  This stub only acknowledges that pre-existing bind.
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import stat
import sys
import threading
from pathlib import Path


DEFAULT_MOUNT_PATH = "/data/mnt/sd_0"
MAX_REQUEST_SIZE = 1024
MMC_DEVICES = frozenset(
    {
        "/dev/mmcblk0p1",
        "/dev/mmcblk0",
        "/dev/mmcblk1p1",
        "/dev/mmcblk1",
    }
)


def response_for(request: bytes, mount_path: str = DEFAULT_MOUNT_PATH) -> bytes:
    """Return the stock-compatible response for one complete request."""

    try:
        command = request.decode("utf-8")
    except UnicodeDecodeError:
        return b"ERROR:UNSUPPORTED"

    mount_prefix = "MOUNT:MOUNT:"
    unmount_prefix = "MOUNT:UMOUNT:"
    if command.startswith(mount_prefix):
        arguments = command[len(mount_prefix) :].split(" ")
        if len(arguments) == 2 and arguments[0] in MMC_DEVICES and arguments[1] == mount_path:
            return f"MOUNT:MOUNT:OK:{mount_path}".encode()
        return f"MOUNT:MOUNT:FAIL:{mount_path}".encode()
    if command.startswith(unmount_prefix):
        if command[len(unmount_prefix) :] == mount_path:
            return f"MOUNT:UMOUNT:OK:{mount_path}".encode()
        return f"MOUNT:UMOUNT:FAIL:{mount_path}".encode()
    return b"ERROR:UNSUPPORTED"


class SysServerStub:
    """Single-threaded Unix stream server for the mount protocol."""

    def __init__(self, socket_path: Path, mount_path: str = DEFAULT_MOUNT_PATH) -> None:
        self.socket_path = socket_path
        self.mount_path = mount_path
        self._socket: socket.socket | None = None
        self._bound_identity: tuple[int, int] | None = None

    def _remove_stale_socket(self) -> None:
        try:
            information = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(information.st_mode):
            raise RuntimeError(f"refusing to replace non-socket path: {self.socket_path}")
        self.socket_path.unlink()

    def _remove_owned_socket(self) -> None:
        if self._bound_identity is None:
            return
        try:
            information = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if (information.st_dev, information.st_ino) == self._bound_identity:
            self.socket_path.unlink()

    def serve(self, stop_event: threading.Event, ready_event: threading.Event | None = None) -> None:
        """Serve one request per connection until ``stop_event`` is set."""

        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._remove_stale_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = server
        try:
            server.bind(str(self.socket_path))
            information = self.socket_path.lstat()
            self._bound_identity = (information.st_dev, information.st_ino)
            os.chmod(self.socket_path, 0o600)
            server.listen(8)
            server.settimeout(0.2)
            if ready_event is not None:
                ready_event.set()

            while not stop_event.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(0.2)
                    try:
                        request = connection.recv(MAX_REQUEST_SIZE)
                        if request:
                            connection.sendall(response_for(request, self.mount_path))
                    except (ConnectionError, socket.timeout):
                        continue
        finally:
            server.close()
            self._socket = None
            self._remove_owned_socket()
            self._bound_identity = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("socket", type=Path, help="host path exposed as /var/run/sys_server")
    parser.add_argument(
        "--mount-path",
        default=DEFAULT_MOUNT_PATH,
        help=f"pre-bound guest SD path (default: {DEFAULT_MOUNT_PATH})",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    stop_event = threading.Event()

    def request_stop(_signal_number: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    server = SysServerStub(args.socket, args.mount_path)
    try:
        server.serve(stop_event)
    except (OSError, RuntimeError) as error:
        print(f"sys_server_stub: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
