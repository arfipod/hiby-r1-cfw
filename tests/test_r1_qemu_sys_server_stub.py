from __future__ import annotations

import importlib.util
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "r1_qemu_sys_server_stub",
    REPO_ROOT / "tools/r1-qemu-ui/sys_server_stub.py",
)
assert MODULE_SPEC and MODULE_SPEC.loader
stub = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = stub
MODULE_SPEC.loader.exec_module(stub)


class R1QemuSysServerStubTests(unittest.TestCase):
    def test_validated_mount_responses(self) -> None:
        self.assertEqual(
            b"MOUNT:MOUNT:OK:/data/mnt/sd_0",
            stub.response_for(b"MOUNT:MOUNT:/dev/mmcblk0p1 /data/mnt/sd_0"),
        )
        self.assertEqual(
            b"MOUNT:UMOUNT:OK:/data/mnt/sd_0",
            stub.response_for(b"MOUNT:UMOUNT:/data/mnt/sd_0"),
        )

    def test_malformed_or_unrelated_requests_are_not_acknowledged(self) -> None:
        self.assertEqual(
            b"MOUNT:MOUNT:FAIL:/data/mnt/sd_0",
            stub.response_for(b"MOUNT:MOUNT:/dev/not-an-mmc-device /data/mnt/sd_0"),
        )
        self.assertEqual(b"ERROR:UNSUPPORTED", stub.response_for(b"POWER:OFF"))

    def test_server_round_trip_and_clean_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "run/sys_server"
            socket_path.parent.mkdir(parents=True)
            probe_path = socket_path.parent / "permission-probe"
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.bind(str(probe_path))
            except PermissionError:
                self.skipTest("sandbox does not permit Unix socket creation")
            finally:
                probe.close()
                probe_path.unlink(missing_ok=True)
            stop_event = threading.Event()
            ready_event = threading.Event()
            server = stub.SysServerStub(socket_path)
            thread = threading.Thread(
                target=server.serve,
                args=(stop_event, ready_event),
                daemon=True,
            )
            thread.start()
            self.assertTrue(ready_event.wait(2.0))

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with client:
                client.settimeout(2.0)
                client.connect(str(socket_path))
                client.sendall(b"MOUNT:MOUNT:/dev/mmcblk0p1 /data/mnt/sd_0")
                self.assertEqual(
                    b"MOUNT:MOUNT:OK:/data/mnt/sd_0",
                    client.recv(stub.MAX_REQUEST_SIZE),
                )

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            with client:
                client.settimeout(2.0)
                client.connect(str(socket_path))
                client.sendall(b"MOUNT:UMOUNT:/data/mnt/sd_0")
                self.assertEqual(
                    b"MOUNT:UMOUNT:OK:/data/mnt/sd_0",
                    client.recv(stub.MAX_REQUEST_SIZE),
                )

            stop_event.set()
            thread.join(2.0)
            self.assertFalse(thread.is_alive())
            self.assertFalse(socket_path.exists())

    def test_server_refuses_to_replace_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "sys_server"
            socket_path.write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "non-socket"):
                stub.SysServerStub(socket_path).serve(threading.Event())
            self.assertEqual("keep", socket_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
