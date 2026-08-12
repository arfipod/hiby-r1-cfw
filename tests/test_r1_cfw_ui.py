from __future__ import annotations

import fcntl
import os
import shutil
import stat
import struct
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "mods/cfw-ui/src"
STATE = struct.Struct("<7I")
INPUT_EVENT = struct.Struct("@llHHi")

EV_SYN = 0
EV_ABS = 3
SYN_REPORT = 0
ABS_MT_TRACKING_ID = 57
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54


class R1CFWUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            raise unittest.SkipTest("a host C compiler is unavailable")
        cls.build_temporary = tempfile.TemporaryDirectory()
        cls.host_binary = Path(cls.build_temporary.name) / "r1-cfw-ui"
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-D_FORTIFY_SOURCE=2",
                "-fstack-protector-strong",
                str(SOURCE_DIR / "r1_cfw_ui.c"),
                str(SOURCE_DIR / "r1_cfw_data.c"),
                str(SOURCE_DIR / "r1_cfw_platform.c"),
                "-o",
                str(cls.host_binary),
            ],
            check=True,
            cwd=REPO_ROOT,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.build_temporary.cleanup()

    def run_data(self, base: Path, *arguments: str, **extra: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(self.data_environment(base))
        environment.update(extra)
        return subprocess.run(
            [str(self.host_binary), *arguments],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
            timeout=5,
        )

    def data_environment(self, base: Path) -> dict[str, str]:
        data = base / "data"
        internal = base / "internal"
        sd = base / "sd"
        proc = base / "proc"
        for directory in (data, internal, sd, proc):
            directory.mkdir(parents=True, exist_ok=True)
        (proc / "meminfo").write_text(
            "MemTotal:       524288 kB\n"
            "MemFree:         32768 kB\n"
            "MemAvailable:   131072 kB\n"
            "Buffers:          4096 kB\n"
            "Cached:          65536 kB\n"
            "HugePages_Total:       0\n",
            encoding="ascii",
        )
        (proc / "uptime").write_text("93784.50 1000.0\n", encoding="ascii")
        (proc / "mounts").write_text(
            f"/dev/fake {sd} vfat rw 0 0\n", encoding="ascii"
        )
        ssh_state = base / "ssh-enabled"
        controller = base / "r1-ssh-control"
        controller.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                case "${1:-}" in
                    is-enabled)
                        [ "$(sed -n '1p' "$R1_CFW_TEST_SSH_STATE" 2>/dev/null)" = 1 ]
                        ;;
                    toggle)
                        if [ "$(sed -n '1p' "$R1_CFW_TEST_SSH_STATE" 2>/dev/null)" = 1 ]; then
                            printf '0\\n' > "$R1_CFW_TEST_SSH_STATE"
                        else
                            printf '1\\n' > "$R1_CFW_TEST_SSH_STATE"
                        fi
                        ;;
                    *) exit 2 ;;
                esac
                """
            ),
            encoding="ascii",
        )
        controller.chmod(0o755)
        if not ssh_state.exists():
            ssh_state.write_text("1\n", encoding="ascii")
        return {
            "R1_CFW_PROC_ROOT": str(proc),
            "R1_CFW_DATA_DIR": str(data),
            "R1_CFW_INTERNAL_PATH": str(internal),
            "R1_CFW_SD_PATH": str(sd),
            "R1_CFW_SSH_CONTROL": str(controller),
            "R1_CFW_TEST_SSH_STATE": str(ssh_state),
        }

    def test_physical_microsd_default_matches_the_stock_mountpoint(self) -> None:
        source = (SOURCE_DIR / "r1_cfw_data.c").read_text(encoding="utf-8")
        self.assertIn(
            '"R1_CFW_SD_PATH", "/usr/data/mnt/sd_0"',
            source,
        )
        self.assertNotIn(
            '"R1_CFW_SD_PATH", "/data/mnt/sd_0"',
            source,
        )

    def test_numeric_glyphs_are_upright(self) -> None:
        source = (SOURCE_DIR / "r1_cfw_platform.c").read_text(encoding="utf-8")
        expected = (
            "{'0',{0x3e,0x51,0x49,0x45,0x3e}}",
            "{'1',{0x00,0x42,0x7f,0x40,0x00}}",
            "{'2',{0x42,0x61,0x51,0x49,0x46}}",
            "{'3',{0x21,0x41,0x45,0x4b,0x31}}",
            "{'4',{0x18,0x14,0x12,0x7f,0x10}}",
            "{'5',{0x27,0x45,0x45,0x45,0x39}}",
            "{'6',{0x3c,0x4a,0x49,0x49,0x30}}",
            "{'7',{0x01,0x71,0x09,0x05,0x03}}",
            "{'8',{0x36,0x49,0x49,0x49,0x36}}",
            "{'9',{0x06,0x49,0x49,0x29,0x1e}}",
        )
        for glyph in expected:
            with self.subTest(glyph=glyph[:4]):
                self.assertIn(glyph, source)

    def test_launcher_masks_use_the_canonical_recoverable_format(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            data.mkdir()
            valid_masks = [
                mask
                for mask in range(0x80)
                if mask & 0x20 and 4 <= mask.bit_count() <= 6
            ]
            self.assertEqual(41, len(valid_masks))
            for mask in valid_masks:
                (data / "launcher.conf").write_text(
                    f"launcher_mask={mask:02x}\n", encoding="ascii"
                )
                result = self.run_data(base, "--launcher-show")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(f"launcher_mask={mask:02x}\n", result.stdout)

            for malformed in (
                "launcher_mask=7F\n",
                "launcher_mask=7f\n",
                "launcher_mask=0x71\n",
                "launcher_mask=70\n",
                "launcher_mask=20\n",
                "mask=71\n",
                "launcher_mask=gg\n",
                "launcher_mask=71",
                "launcher_mask=71\nlauncher_mask=73\n",
                "",
            ):
                (data / "launcher.conf").write_text(malformed, encoding="ascii")
                result = self.run_data(base, "--launcher-show")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("launcher_mask=71\n", result.stdout, malformed)
                self.assertEqual(
                    "launcher_mask=71\n",
                    (data / "launcher.conf").read_text(encoding="ascii"),
                    malformed,
                )
                self.assertEqual(
                    0o600,
                    stat.S_IMODE((data / "launcher.conf").stat().st_mode),
                    malformed,
                )

    def test_launcher_toggles_lock_cfw_and_keep_at_least_four_tiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            rejected = self.run_data(base, "--launcher-set", "music", "0")
            self.assertNotEqual(0, rejected.returncode)
            rejected = self.run_data(base, "--launcher-set", "cfw", "0")
            self.assertNotEqual(0, rejected.returncode)

            enabled = self.run_data(base, "--launcher-set", "stream", "1")
            self.assertEqual(0, enabled.returncode, enabled.stderr)
            self.assertEqual("launcher_mask=73\n", enabled.stdout)
            config = base / "data/launcher.conf"
            self.assertEqual("launcher_mask=73\n", config.read_text(encoding="ascii"))
            self.assertEqual(0o600, stat.S_IMODE(config.stat().st_mode))

            disabled = self.run_data(base, "--launcher-set", "stream", "0")
            self.assertEqual(0, disabled.returncode, disabled.stderr)
            self.assertEqual("launcher_mask=71\n", disabled.stdout)

    def test_seventh_tile_is_rejected_and_each_optional_tile_is_restorable(self) -> None:
        entries = (
            ("music", 0x01),
            ("stream", 0x02),
            ("wireless", 0x04),
            ("ebook", 0x08),
            ("system", 0x10),
            ("about", 0x40),
        )
        message = "Maximum 6 launcher tiles. Disable one first.\n"
        for name, bit in entries:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as temporary:
                    base = Path(temporary)
                    data = base / "data"
                    data.mkdir()
                    initial_mask = 0x7F & ~bit
                    config = data / "launcher.conf"
                    config.write_text(
                        f"launcher_mask={initial_mask:02x}\n", encoding="ascii"
                    )

                    blocked = self.run_data(base, "--launcher-set", name, "1")
                    self.assertNotEqual(0, blocked.returncode)
                    self.assertEqual("", blocked.stdout)
                    self.assertEqual(message, blocked.stderr)
                    self.assertEqual(
                        f"launcher_mask={initial_mask:02x}\n",
                        config.read_text(encoding="ascii"),
                    )

                    donor_name, donor_bit = next(
                        (candidate_name, candidate_bit)
                        for candidate_name, candidate_bit in entries
                        if candidate_bit != bit and initial_mask & candidate_bit
                    )
                    reduced = self.run_data(
                        base, "--launcher-set", donor_name, "0"
                    )
                    self.assertEqual(0, reduced.returncode, reduced.stderr)
                    restored = self.run_data(base, "--launcher-set", name, "1")
                    self.assertEqual(0, restored.returncode, restored.stderr)
                    expected_mask = 0x7F & ~donor_bit
                    self.assertEqual(
                        f"launcher_mask={expected_mask:02x}\n", restored.stdout
                    )
                    self.assertEqual(
                        f"launcher_mask={expected_mask:02x}\n",
                        config.read_text(encoding="ascii"),
                    )

    def test_collectors_report_storage_memory_ssh_and_wifi_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            present = self.run_data(
                base, "--dump-info", R1_CFW_TEST_WIFI_IP="192.0.2.44"
            )
            self.assertEqual(0, present.returncode, present.stderr)
            fields = dict(
                line.split("=", 1)
                for line in present.stdout.splitlines()
                if "=" in line
            )
            self.assertEqual("1", fields["ssh_enabled"])
            self.assertEqual("192.0.2.44", fields["wifi_ip"])
            self.assertEqual("524288", fields["memory_total_kib"])
            self.assertEqual("131072", fields["memory_available_kib"])
            self.assertEqual("393216", fields["memory_used_kib"])
            self.assertEqual("93784", fields["uptime_seconds"])
            self.assertEqual("1", fields["internal_present"])
            self.assertEqual("1", fields["sd_present"])
            self.assertEqual("71", fields["launcher_mask"])
            self.assertTrue(fields["kernel"])
            self.assertTrue(fields["hostname"])

            absent = self.run_data(base, "--dump-info", R1_CFW_TEST_WIFI_IP="")
            absent_fields = dict(
                line.split("=", 1)
                for line in absent.stdout.splitlines()
                if "=" in line
            )
            self.assertEqual("", absent_fields["wifi_ip"])

            degraded_environment = os.environ.copy()
            degraded_environment.update(self.data_environment(base))
            degraded_environment["R1_CFW_TEST_WIFI_IP"] = ""
            (base / "ssh-enabled").write_text("0\n", encoding="ascii")
            (base / "proc/mounts").write_text("", encoding="ascii")
            (base / "proc/meminfo").write_text(
                "MemTotal: 100 kB\n"
                "MemFree: 10 kB\n"
                "Buffers: 20 kB\n"
                "Cached: 30 kB\n"
                "Malformed line\n",
                encoding="ascii",
            )
            degraded = subprocess.run(
                [str(self.host_binary), "--dump-info"],
                check=False,
                text=True,
                capture_output=True,
                env=degraded_environment,
                timeout=5,
            )
            degraded_fields = dict(
                line.split("=", 1)
                for line in degraded.stdout.splitlines()
                if "=" in line
            )
            self.assertEqual("0", degraded_fields["ssh_enabled"])
            self.assertEqual("0", degraded_fields["sd_present"])
            self.assertEqual("100", degraded_fields["memory_total_kib"])
            self.assertEqual("60", degraded_fields["memory_available_kib"])
            self.assertEqual("40", degraded_fields["memory_used_kib"])

    def test_qemu_controller_wrapper_uses_the_target_shell_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            wrapper = base / "qemu-wrapper"
            wrapper_log = base / "qemu-wrapper.log"
            wrapper.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    printf '%s\\n' "$@" >> "$R1_CFW_TEST_WRAPPER_LOG"
                    exec "$@"
                    """
                ),
                encoding="ascii",
            )
            wrapper.chmod(0o755)
            result = self.run_data(
                base,
                "--dump-info",
                R1_QEMU_EXEC_WRAPPER=str(wrapper),
                R1_CFW_TEST_WRAPPER_LOG=str(wrapper_log),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                [
                    "/bin/sh",
                    str(base / "r1-ssh-control"),
                    "is-enabled",
                ],
                wrapper_log.read_text(encoding="ascii").splitlines(),
            )
            fields = dict(
                line.split("=", 1)
                for line in result.stdout.splitlines()
                if "=" in line
            )
            self.assertEqual("1", fields["ssh_enabled"])

    def start_ui(
        self,
        base: Path,
        *,
        stride: int = 960,
        bpp: int = 16,
        framebuffer_bytes: int = 1_536_000,
        packed: bool = False,
        touch_fd_minimum: int | None = None,
        extra_environment: dict[str, str] | None = None,
    ) -> tuple[subprocess.Popen[bytes], int, Path, Path]:
        framebuffer = base / "framebuffer.raw"
        with framebuffer.open("wb") as stream:
            stream.truncate(framebuffer_bytes)
        framebuffer_fd = os.open(framebuffer, os.O_RDWR)
        touch_read, touch_write = os.pipe()
        if touch_fd_minimum is not None:
            inherited_touch = fcntl.fcntl(
                touch_read, fcntl.F_DUPFD, touch_fd_minimum
            )
            os.close(touch_read)
            touch_read = inherited_touch
        state_path = base / "cfw-state.bin"
        environment = os.environ.copy()
        environment.update(self.data_environment(base))
        environment["R1_CFW_TEST_STATE_PATH"] = str(state_path)
        if extra_environment:
            environment.update(extra_environment)
        arguments = [
            str(self.host_binary),
            "--fb-fd",
            str(framebuffer_fd),
            "--touch-fd",
            str(touch_read),
            "--test-mode",
            "--fb-stride",
            str(stride),
            "--fb-bpp",
            str(bpp),
            "--fb-bytes",
            str(framebuffer_bytes),
        ]
        if packed:
            arguments.append("--packed-rgb565")
        process = subprocess.Popen(
            arguments,
            pass_fds=(framebuffer_fd, touch_read),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.close(framebuffer_fd)
        os.close(touch_read)
        return process, touch_write, state_path, framebuffer

    def test_inherited_touch_descriptor_above_fd_setsize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            process, touch, state_path, _ = self.start_ui(
                Path(temporary),
                touch_fd_minimum=2048,
                extra_environment={"R1_CFW_TEST_AUTO_EXIT_MS": "80"},
            )
            os.close(touch)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, (stdout, stderr))
            record = self.read_state(state_path)
            self.assertGreaterEqual(record[2], 2)

    def test_invalid_inherited_touch_descriptor_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            framebuffer = base / "framebuffer.raw"
            with framebuffer.open("wb") as stream:
                stream.truncate(1_536_000)
            framebuffer_fd = os.open(framebuffer, os.O_RDWR)
            environment = os.environ.copy()
            environment.update(self.data_environment(base))
            process = subprocess.Popen(
                [
                    str(self.host_binary),
                    "--fb-fd",
                    str(framebuffer_fd),
                    "--touch-fd",
                    "9999",
                    "--test-mode",
                    "--fb-stride",
                    "960",
                    "--fb-bpp",
                    "16",
                    "--fb-bytes",
                    "1536000",
                ],
                pass_fds=(framebuffer_fd,),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            os.close(framebuffer_fd)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(5, process.returncode, (stdout, stderr))
            self.assertEqual(
                b"invalid inherited touch descriptor\n", stderr
            )

    def read_state(
        self,
        path: Path,
        predicate=lambda record: True,
        timeout: float = 3.0,
    ) -> tuple[int, ...]:
        deadline = time.monotonic() + timeout
        last: tuple[int, ...] | None = None
        while time.monotonic() < deadline:
            try:
                data = path.read_bytes()
            except FileNotFoundError:
                data = b""
            if len(data) == STATE.size:
                last = STATE.unpack(data)
                if predicate(last):
                    return last
            time.sleep(0.01)
        self.fail(f"timed out waiting for CFW state; last={last}")

    @staticmethod
    def send_touch(descriptor: int, x: int, y: int, *, drag_x: int | None = None) -> None:
        events = [
            (EV_ABS, ABS_MT_TRACKING_ID, 1),
            (EV_ABS, ABS_MT_POSITION_X, x),
            (EV_ABS, ABS_MT_POSITION_Y, y),
            (EV_SYN, SYN_REPORT, 0),
        ]
        if drag_x is not None:
            events.extend(
                [
                    (EV_ABS, ABS_MT_POSITION_X, drag_x),
                    (EV_SYN, SYN_REPORT, 0),
                ]
            )
        events.extend(
            [
                (EV_ABS, ABS_MT_TRACKING_ID, -1),
                (EV_SYN, SYN_REPORT, 0),
            ]
        )
        os.write(
            descriptor,
            b"".join(INPUT_EVENT.pack(0, 0, kind, code, value) for kind, code, value in events),
        )

    def test_framebuffer_formats_and_emulator_lifecycle_controls(self) -> None:
        formats = (
            (960, 16, 1_536_000, False),
            (1920, 32, 3_072_000, True),
            (1920, 32, 3_072_000, False),
        )
        for stride, bpp, size, packed in formats:
            with self.subTest(stride=stride, bpp=bpp, packed=packed):
                with tempfile.TemporaryDirectory() as temporary:
                    process, touch, state_path, framebuffer = self.start_ui(
                        Path(temporary),
                        stride=stride,
                        bpp=bpp,
                        framebuffer_bytes=size,
                        packed=packed,
                        extra_environment={"R1_CFW_TEST_AUTO_EXIT_MS": "80"},
                    )
                    os.close(touch)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(0, process.returncode, (stdout, stderr))
                    record = self.read_state(state_path)
                    self.assertEqual(0x52314346, record[0])
                    self.assertGreaterEqual(record[2], 2)
                    self.assertEqual(1, record[3])
                    self.assertEqual(0x71, record[4])
                    self.assertEqual(1, record[5])
                    self.assertEqual(0, record[6])
                    self.assertNotEqual({0}, set(framebuffer.read_bytes()))

        with tempfile.TemporaryDirectory() as temporary:
            process, touch, state_path, _ = self.start_ui(
                Path(temporary),
                extra_environment={"R1_CFW_TEST_CRASH_AFTER_MS": "80"},
            )
            os.close(touch)
            process.communicate(timeout=5)
            self.assertEqual(-6, process.returncode)
            record = self.read_state(state_path)
            self.assertEqual(1, record[3])

    def test_touch_navigation_back_routes_drag_and_launcher_toggles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            process, touch, state_path, _ = self.start_ui(base)
            initial = self.read_state(state_path)
            self.assertEqual((1, 0x71), (initial[3], initial[4]))

            self.send_touch(touch, 200, 332)  # Launcher row.
            launcher = self.read_state(state_path, lambda state: state[3] == 2)
            self.assertEqual(5, launcher[5])

            self.send_touch(touch, 200, 476)  # Locked CFW tile.
            rejected = self.read_state(state_path, lambda state: state[5] == 7)
            self.assertEqual(0x71, rejected[4])

            self.send_touch(touch, 200, 188)  # Enable Stream.
            toggled = self.read_state(
                state_path, lambda state: state[5] == 6 and state[4] == 0x73
            )
            self.assertGreater(toggled[2], rejected[2])
            self.assertEqual(
                "launcher_mask=73\n",
                (base / "data/launcher.conf").read_text(encoding="ascii"),
            )

            self.send_touch(touch, 40, 30)  # Subpage Back.
            main = self.read_state(
                state_path,
                lambda state: state[3] == 1 and state[5] == 1 and state[2] > toggled[2],
            )
            self.send_touch(touch, 150, 250, drag_x=210)
            dragged = self.read_state(
                state_path, lambda state: state[5] == 8 and state[2] > main[2]
            )
            self.assertEqual(1, dragged[3])

            self.send_touch(touch, 40, 30)  # Root Back exits.
            os.close(touch)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, (stdout, stderr))

        for row, route, action in ((1, 21, 3), (2, 22, 4)):
            with self.subTest(route=route):
                with tempfile.TemporaryDirectory() as temporary:
                    process, touch, state_path, _ = self.start_ui(Path(temporary))
                    self.read_state(state_path)
                    self.send_touch(touch, 200, 80 + row * 72 + 36)
                    os.close(touch)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(route, process.returncode, (stdout, stderr))
                    record = self.read_state(state_path)
                    self.assertEqual(action, record[5])
                    self.assertEqual(route, record[6])

    def test_touch_launcher_limit_recovers_after_disabling_one_tile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            data = base / "data"
            data.mkdir()
            config = data / "launcher.conf"
            config.write_text("launcher_mask=7b\n", encoding="ascii")
            process, touch, state_path, _ = self.start_ui(base)
            initial = self.read_state(state_path)
            self.assertEqual((1, 0x7B), (initial[3], initial[4]))

            self.send_touch(touch, 200, 332)  # Launcher row.
            launcher = self.read_state(
                state_path,
                lambda state: state[3] == 2 and state[2] > initial[2],
            )
            self.send_touch(touch, 200, 260)  # Seventh tile: Wireless.
            rejected = self.read_state(
                state_path,
                lambda state: state[3] == 2
                and state[4] == 0x7B
                and state[5] == 7
                and state[2] > launcher[2],
            )
            self.assertEqual("launcher_mask=7b\n", config.read_text(encoding="ascii"))

            self.send_touch(touch, 200, 188)  # Disable Stream first.
            reduced = self.read_state(
                state_path,
                lambda state: state[4] == 0x79
                and state[5] == 6
                and state[2] > rejected[2],
            )
            self.send_touch(touch, 200, 260)  # Wireless is now restorable.
            restored = self.read_state(
                state_path,
                lambda state: state[4] == 0x7D
                and state[5] == 6
                and state[2] > reduced[2],
            )
            self.assertEqual("launcher_mask=7d\n", config.read_text(encoding="ascii"))

            self.send_touch(touch, 40, 30)  # Subpage Back.
            self.read_state(
                state_path,
                lambda state: state[3] == 1 and state[2] > restored[2],
            )
            self.send_touch(touch, 40, 30)  # Root Back exits.
            os.close(touch)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(0, process.returncode, (stdout, stderr))

    def test_target_build_produces_only_the_two_root_artifacts(self) -> None:
        zig = REPO_ROOT / "work/host-tools/zig-x86_64-linux-0.16.0/zig"
        if not zig.is_file():
            self.skipTest("the pinned Zig toolchain is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "output"
            environment = os.environ.copy()
            environment["R1_CFW_ZIG_CACHE_DIR"] = str(base / "zig-cache")
            subprocess.run(
                [str(REPO_ROOT / "tools/build-r1-cfw-ui.sh"), str(output)],
                check=True,
                cwd=REPO_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            self.assertEqual(
                ["libr1-cfw-hook.so", "r1-cfw-ui"],
                sorted(path.name for path in output.iterdir()),
            )
            for name in ("r1-cfw-ui", "libr1-cfw-hook.so"):
                header = subprocess.run(
                    ["readelf", "-h", str(output / name)],
                    check=True,
                    text=True,
                    capture_output=True,
                ).stdout
                self.assertIn("MIPS", header)
                self.assertIn("mips32r2", header)
            hook_dynamic = subprocess.run(
                ["readelf", "-d", str(output / "libr1-cfw-hook.so")],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertIn("libdl.so.2", hook_dynamic)
            hook_symbols = subprocess.run(
                ["readelf", "-Ws", str(output / "libr1-cfw-hook.so")],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertRegex(hook_symbols, r"(?m)GLOBAL\s+DEFAULT\s+\d+ ioctl$")

    def test_hook_ioctl_interposer_virtualizes_page_state_and_allows_restore(self) -> None:
        compiler = shutil.which("cc")
        if not compiler:
            self.skipTest("a host C compiler is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            hook = base / "hook.so"
            next_layer = base / "next.so"
            probe = base / "probe"
            next_source = base / "next.c"
            probe_source = base / "probe.c"
            next_source.write_text(
                textwrap.dedent(
                    r"""
                    #include <linux/fb.h>
                    #include <stdint.h>
                    #include <string.h>

                    static uint32_t physical_yoffset;

                    __attribute__((visibility("default")))
                    int test_next_ioctl(int descriptor, unsigned long request,
                                        uintptr_t argument) __asm__("ioctl");

                    __attribute__((visibility("default")))
                    int test_next_ioctl(int descriptor, unsigned long request,
                                        uintptr_t argument) {
                        struct fb_var_screeninfo *variable = (void *)argument;
                        if (descriptor != 9) return -90;
                        if (request == 0x12345678UL &&
                            argument == (uintptr_t)0xabcdef01UL) return 66;
                        if (request == (unsigned long)FBIOGET_VSCREENINFO &&
                            variable) {
                            memset(variable, 0, sizeof(*variable));
                            variable->yres = 800;
                            variable->yres_virtual = 1600;
                            variable->yoffset = physical_yoffset;
                            return 0;
                        }
                        if (request == (unsigned long)FBIOPAN_DISPLAY &&
                            variable) {
                            physical_yoffset = variable->yoffset;
                            return 77;
                        }
                        return -91;
                    }
                    """
                ),
                encoding="ascii",
            )
            probe_source.write_text(
                textwrap.dedent(
                    r"""
                    #define _GNU_SOURCE
                    #include <dlfcn.h>
                    #include <errno.h>
                    #include <linux/fb.h>
                    #include <stdint.h>
                    #include <stdio.h>
                    #include <sys/ioctl.h>

                    typedef void (*begin_t)(uint32_t, uint32_t, uint32_t);
                    typedef uint32_t (*yoffset_t)(void);
                    typedef uint64_t (*count_t)(void);
                    typedef int (*unfiltered_t)(int, unsigned long, uintptr_t);

                    int main(void) {
                        struct fb_var_screeninfo requested = {0};
                        struct fb_var_screeninfo virtual_report = {0};
                        struct fb_var_screeninfo physical_report = {0};
                        begin_t begin = (begin_t)dlsym(
                            RTLD_DEFAULT, "r1_cfw_test_begin_virtual_display");
                        yoffset_t yoffset = (yoffset_t)dlsym(
                            RTLD_DEFAULT, "r1_cfw_test_virtual_yoffset");
                        count_t count = (count_t)dlsym(
                            RTLD_DEFAULT, "r1_cfw_test_suppressed_pans");
                        unfiltered_t unfiltered = (unfiltered_t)dlsym(
                            RTLD_DEFAULT, "r1_cfw_test_unfiltered_ioctl");
                        int forwarded;
                        int suppressed;
                        int virtual_get;
                        int physical_get;
                        int invalid;
                        int invalid_errno;
                        int restored;
                        if (!begin || !yoffset || !count || !unfiltered) return 2;
                        forwarded = ioctl(9, 0x12345678UL,
                                          (uintptr_t)0xabcdef01UL);
                        begin(0, 800, 1600);
                        requested.yoffset = 800;
                        suppressed = ioctl(9, FBIOPAN_DISPLAY, &requested);
                        virtual_get = ioctl(9, FBIOGET_VSCREENINFO,
                                            &virtual_report);
                        physical_get = unfiltered(
                            9, FBIOGET_VSCREENINFO,
                            (uintptr_t)&physical_report);
                        requested.yoffset = 801;
                        errno = 0;
                        invalid = ioctl(9, FBIOPAN_DISPLAY, &requested);
                        invalid_errno = errno;
                        requested.yoffset = 800;
                        restored = unfiltered(9, FBIOPAN_DISPLAY,
                                              (uintptr_t)&requested);
                        printf("%d %d %d %u %d %u %u %llu %d %d %d\n",
                               forwarded, suppressed, virtual_get,
                               virtual_report.yoffset, physical_get,
                               physical_report.yoffset, yoffset(),
                               (unsigned long long)count(), invalid,
                               invalid_errno, restored);
                        return forwarded == 66 && suppressed == 0 &&
                                       virtual_get == 0 &&
                                       virtual_report.yoffset == 800 &&
                                       physical_get == 0 &&
                                       physical_report.yoffset == 0 &&
                                       yoffset() == 800 && count() == 1 &&
                                       invalid == -1 && invalid_errno == EINVAL &&
                                       restored == 77
                                   ? 0
                                   : 1;
                    }
                    """
                ),
                encoding="ascii",
            )
            flags = ["-std=c11", "-O2", "-Wall", "-Wextra", "-Werror"]
            subprocess.run(
                [compiler, *flags, "-shared", "-fPIC", "-DR1_CFW_HOOK_TEST",
                 str(SOURCE_DIR / "r1_cfw_hook.c"), "-ldl", "-o", str(hook)],
                check=True, cwd=REPO_ROOT,
            )
            subprocess.run(
                [compiler, *flags, "-shared", "-fPIC", str(next_source),
                 "-o", str(next_layer)],
                check=True, cwd=REPO_ROOT,
            )
            subprocess.run(
                [compiler, *flags, str(probe_source), "-ldl", "-o", str(probe)],
                check=True, cwd=REPO_ROOT,
            )
            environment = os.environ.copy()
            environment["LD_PRELOAD"] = f"{hook}:{next_layer}"
            result = subprocess.run(
                [str(probe)], check=False, text=True, capture_output=True,
                env=environment, timeout=5,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("66 0 0 800 0 0 800 1 -1 22 77\n", result.stdout)

    def test_hook_scans_for_the_named_touchscreen_and_guards_stock_addresses(self) -> None:
        source = (SOURCE_DIR / "r1_cfw_hook.c").read_text(encoding="utf-8")
        self.assertIn('strcmp(name, "hyn_ts")', source)
        self.assertIn("index < R1_INPUT_SCAN_LIMIT", source)
        self.assertIn("EVIOCGNAME(sizeof(name))", source)
        self.assertIn("touch_capabilities(descriptor)", source)
        self.assertIn("transfer_stock_grab(touch->descriptor, device_path)", source)
        self.assertIn("0x00892048U", source)
        self.assertIn("0x00892090U", source)
        self.assertIn("0x0053bbc0U", source)
        self.assertIn('"launcher_apps_vg_step"', source)
        self.assertIn('strcmp(base, "hiby_player") == 0', source)
        self.assertIn("!executable_is_player() || !stock_guards_match()", source)
        self.assertIn("forward_ioctl(descriptor, FBIOPAN_DISPLAY", source)
        self.assertIn('"R1_QEMU_INHERITED_FB_FD"', source)
        self.assertIn("execve(executable, arguments, child_environment)", source)
        self.assertIn('"/tmp/r1-host-qemu"', source)

        ui_source = (SOURCE_DIR / "r1_cfw_ui.c").read_text(encoding="utf-8")
        self.assertIn('"APPLIES ON NEXT PLAYER RESTART"', ui_source)
        self.assertIn(
            '"Maximum 6 launcher tiles. Disable one first."',
            (SOURCE_DIR / "r1_cfw_data.h").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "ui->launcher_notice = R1_CFW_LAUNCHER_LIMIT_MESSAGE", ui_source
        )


if __name__ == "__main__":
    unittest.main()
