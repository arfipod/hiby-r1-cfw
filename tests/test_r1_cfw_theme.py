from __future__ import annotations

import os
import stat
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mods/cfw-ui/src"
STATE = struct.Struct("<7I")
INPUT_EVENT = struct.Struct("@llHHi")
EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54
ABS_MT_TRACKING_ID = 57


class R1CfwThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.build.name) / "r1-cfw-ui"
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(SOURCE / "r1_cfw_ui.c"),
                str(SOURCE / "r1_cfw_data.c"),
                str(SOURCE / "r1_cfw_platform.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.build.cleanup()

    def fixture(self, root: Path) -> dict[str, str]:
        for name in ("data", "internal", "sd", "proc"):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / "proc/meminfo").write_text(
            "MemTotal: 100 kB\nMemAvailable: 60 kB\n", encoding="ascii"
        )
        (root / "proc/uptime").write_text("10 0\n", encoding="ascii")
        (root / "proc/mounts").write_text("", encoding="ascii")
        controller = root / "r1-ssh-control"
        controller.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
        controller.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "R1_CFW_PROC_ROOT": str(root / "proc"),
                "R1_CFW_DATA_DIR": str(root / "data"),
                "R1_CFW_INTERNAL_PATH": str(root / "internal"),
                "R1_CFW_SD_PATH": str(root / "sd"),
                "R1_CFW_SSH_CONTROL": str(controller),
                "R1_CFW_TEST_WIFI_IP": "",
            }
        )
        return environment

    def command(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.binary), *arguments],
            check=False,
            text=True,
            capture_output=True,
            env=self.fixture(root),
            timeout=5,
        )

    def test_theme_state_is_canonical_atomic_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shown = self.command(root, "--theme-show")
            self.assertEqual((0, "theme=stock\n"), (shown.returncode, shown.stdout))

            config = root / "data/theme.conf"
            for name in ("light", "dark", "retro", "stock"):
                with self.subTest(name=name):
                    selected = self.command(root, "--theme-set", name)
                    self.assertEqual(0, selected.returncode, selected.stderr)
                    self.assertEqual(f"theme={name}\n", selected.stdout)
                    self.assertEqual(f"theme={name}\n", config.read_text(encoding="ascii"))
                    self.assertEqual(0o600, stat.S_IMODE(config.stat().st_mode))

            for malformed in (
                "theme=RETRO\n",
                "theme=retro\nextra=1\n",
                "retro\n",
                "theme=unknown\n",
                "theme=retro",
            ):
                with self.subTest(malformed=malformed):
                    config.write_text(malformed, encoding="ascii")
                    recovered = self.command(root, "--theme-show")
                    self.assertEqual("theme=stock\n", recovered.stdout)
                    self.assertEqual("theme=stock\n", config.read_text(encoding="ascii"))

            invalid = self.command(root, "--theme-set", "unknown")
            self.assertEqual(1, invalid.returncode)
            self.assertIn("theme update failed", invalid.stderr)

    @staticmethod
    def send_touch(descriptor: int, x: int, y: int) -> None:
        events = (
            (EV_ABS, ABS_MT_TRACKING_ID, 1),
            (EV_ABS, ABS_MT_POSITION_X, x),
            (EV_ABS, ABS_MT_POSITION_Y, y),
            (EV_SYN, SYN_REPORT, 0),
            (EV_ABS, ABS_MT_TRACKING_ID, -1),
            (EV_SYN, SYN_REPORT, 0),
        )
        os.write(
            descriptor,
            b"".join(
                INPUT_EVENT.pack(0, 0, event_type, code, value)
                for event_type, code, value in events
            ),
        )

    def read_state(
        self,
        path: Path,
        predicate=lambda state: True,
        timeout: float = 3.0,
    ) -> tuple[int, ...]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                data = b""
            if len(data) == STATE.size:
                state = STATE.unpack(data)
                if predicate(state):
                    return state
            time.sleep(0.01)
        self.fail("timed out waiting for CFW theme state")

    def test_about_appearance_selects_retro_without_moving_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = self.fixture(root)
            framebuffer = root / "framebuffer.raw"
            framebuffer.write_bytes(b"\0" * 1_536_000)
            framebuffer_fd = os.open(framebuffer, os.O_RDWR)
            touch_read, touch_write = os.pipe()
            state_path = root / "state.bin"
            environment["R1_CFW_TEST_STATE_PATH"] = str(state_path)
            process = subprocess.Popen(
                [
                    str(self.binary),
                    "--fb-fd",
                    str(framebuffer_fd),
                    "--touch-fd",
                    str(touch_read),
                    "--test-mode",
                    "--fb-stride",
                    "960",
                    "--fb-bpp",
                    "16",
                    "--fb-bytes",
                    "1536000",
                ],
                pass_fds=(framebuffer_fd, touch_read),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            os.close(framebuffer_fd)
            os.close(touch_read)
            try:
                initial = self.read_state(state_path)
                self.assertEqual(1, initial[3])
                self.send_touch(touch_write, 200, 80 + 8 * 72 + 36)
                about = self.read_state(state_path, lambda state: state[3] == 7)
                self.assertEqual(5, about[5])
                self.send_touch(touch_write, 200, 80 + 4 * 72 + 36)
                appearance = self.read_state(state_path, lambda state: state[3] == 8)
                self.assertEqual(5, appearance[5])
                self.send_touch(touch_write, 200, 80 + 3 * 72 + 36)
                selected = self.read_state(
                    state_path, lambda state: state[3] == 8 and state[5] == 9
                )
                self.assertGreater(selected[2], appearance[2])
                self.assertEqual(
                    "theme=retro\n",
                    (root / "data/theme.conf").read_text(encoding="ascii"),
                )
                self.assertIn(b"\x7c\xf7", framebuffer.read_bytes())
                self.send_touch(touch_write, 40, 30)
                self.read_state(
                    state_path, lambda state: state[3] == 7 and state[5] == 1
                )
                self.send_touch(touch_write, 40, 30)
                self.read_state(
                    state_path, lambda state: state[3] == 1 and state[5] == 1
                )
                self.send_touch(touch_write, 40, 30)
            finally:
                os.close(touch_write)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, (stdout, stderr))


if __name__ == "__main__":
    unittest.main()
