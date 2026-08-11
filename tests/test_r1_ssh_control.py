from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROL = (
    REPO_ROOT
    / "mods/ssh-dropbear/rootfs-overlay/usr/bin/r1-ssh-control"
)


class R1SshControlTests(unittest.TestCase):
    def run_control(
        self, root: Path, command: str, *, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        state = root / "state"
        run = root / "run"
        sd = root / "sd"
        developer_disabled = root / "disableadb"
        run.mkdir(exist_ok=True)
        sd.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "R1_SSH_STATE_DIR": str(state),
                "R1_SSH_RUN_DIR": str(run),
                "R1_SSH_SD_ROOT": str(sd),
                "R1_SSH_DEVELOPER_DISABLED": str(developer_disabled),
            }
        )
        return subprocess.run(
            ["/bin/sh", str(CONTROL), command],
            check=check,
            capture_output=True,
            text=True,
            env=environment,
        )

    def run_control_sourced(
        self, root: Path, command: str, fake_argv0: Path
    ) -> subprocess.CompletedProcess[str]:
        state = root / "state"
        run = root / "run"
        sd = root / "sd"
        developer_disabled = root / "disableadb"
        run.mkdir(exist_ok=True)
        sd.mkdir(exist_ok=True)
        environment = os.environ.copy()
        environment.update(
            {
                "R1_SSH_STATE_DIR": str(state),
                "R1_SSH_RUN_DIR": str(run),
                "R1_SSH_SD_ROOT": str(sd),
                "R1_SSH_DEVELOPER_DISABLED": str(developer_disabled),
            }
        )
        return subprocess.run(
            [
                "/bin/bash",
                "-c",
                '. "$1" "$2"',
                str(fake_argv0),
                str(CONTROL),
                command,
            ],
            capture_output=True,
            text=True,
            env=environment,
        )

    def test_fresh_install_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_control(Path(temporary), "is-enabled")
            self.assertEqual(1, result.returncode)
            self.assertEqual("disabled", result.stdout.strip())

    def test_enable_disable_and_toggle_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            enabled_result = self.run_control(root, "enable", check=True)
            enabled = root / "state/enabled"
            disabled = root / "state/disabled"
            self.assertEqual(
                "SSH is enabled; Dropbear will follow the wlan0 IPv4 address",
                enabled_result.stdout.strip(),
            )
            self.assertTrue(enabled.is_file())
            self.assertFalse(disabled.exists())
            self.assertEqual(
                0o700, stat.S_IMODE((root / "state").stat().st_mode)
            )
            self.assertEqual(0o600, stat.S_IMODE(enabled.stat().st_mode))
            self.assertEqual(
                0, self.run_control(root, "is-enabled").returncode
            )

            disabled_result = self.run_control(root, "toggle", check=True)
            self.assertEqual("SSH is disabled", disabled_result.stdout.strip())
            self.assertFalse(enabled.exists())
            self.assertTrue(disabled.is_file())
            self.assertEqual(0o600, stat.S_IMODE(disabled.stat().st_mode))
            self.assertEqual(
                1, self.run_control(root, "is-enabled").returncode
            )

            enabled_result = self.run_control(root, "toggle", check=True)
            self.assertEqual(
                "SSH is enabled; Dropbear will follow the wlan0 IPv4 address",
                enabled_result.stdout.strip(),
            )
            self.assertTrue(enabled.is_file())
            self.assertFalse(disabled.exists())

    def test_toggle_calls_shared_functions_without_self_exec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            invocation_marker = root / "self-exec-was-called"
            fake_argv0 = root / "forbidden-controller-reexec"
            fake_argv0.write_text(
                "#!/bin/sh\n"
                f"touch {invocation_marker}\n"
                "exit 99\n",
                encoding="ascii",
            )
            fake_argv0.chmod(0o755)

            enabled = self.run_control_sourced(root, "toggle", fake_argv0)
            self.assertEqual(0, enabled.returncode, enabled.stderr)
            self.assertEqual(
                "SSH is enabled; Dropbear will follow the wlan0 IPv4 address",
                enabled.stdout.strip(),
            )
            self.assertTrue((root / "state/enabled").is_file())
            self.assertFalse(invocation_marker.exists())

            server_pid = root / "run/dropbear-r1.pid"
            bound_ip = root / "run/dropbear-r1.ip"
            server_pid.write_text("999999999\n", encoding="ascii")
            bound_ip.write_text("192.0.2.1\n", encoding="ascii")
            disabled = self.run_control_sourced(root, "toggle", fake_argv0)
            self.assertEqual(0, disabled.returncode, disabled.stderr)
            self.assertEqual("SSH is disabled", disabled.stdout.strip())
            self.assertTrue((root / "state/disabled").is_file())
            self.assertFalse(server_pid.exists())
            self.assertFalse(bound_ip.exists())
            self.assertFalse(invocation_marker.exists())

    def test_marker_write_failures_are_not_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.write_text("not a directory\n", encoding="ascii")

            for command in ("enable", "toggle"):
                with self.subTest(command=command, direction="enable"):
                    result = self.run_control(root, command)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("", result.stdout)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            run = root / "run"
            state.mkdir()
            run.mkdir()
            (state / "enabled").touch()
            (state / "disabled").symlink_to(
                root / "missing-parent/disabled"
            )

            for command in ("disable", "toggle"):
                with self.subTest(command=command, direction="disable"):
                    server_pid = run / "dropbear-r1.pid"
                    bound_ip = run / "dropbear-r1.ip"
                    server_pid.write_text("999999999\n", encoding="ascii")
                    bound_ip.write_text("192.0.2.1\n", encoding="ascii")

                    result = self.run_control(root, command)
                    self.assertNotEqual(0, result.returncode)
                    self.assertEqual("", result.stdout)
                    self.assertTrue(server_pid.is_file())
                    self.assertTrue(bound_ip.is_file())
                    self.assertTrue((state / "enabled").is_file())

    def test_legacy_host_key_migrates_logically_to_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            (state / "dropbear_ed25519_host_key").write_bytes(b"legacy-key")

            result = self.run_control(root, "is-enabled")
            self.assertEqual(0, result.returncode)
            self.assertEqual("enabled", result.stdout.strip())

            self.run_control(root, "disable", check=True)
            self.assertEqual(
                1, self.run_control(root, "is-enabled").returncode
            )

    def test_explicit_disabled_state_wins_over_legacy_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            (state / "dropbear_ed25519_host_key").write_bytes(b"legacy-key")
            (state / "disabled").touch()

            self.assertEqual(
                1, self.run_control(root, "is-enabled").returncode
            )


if __name__ == "__main__":
    unittest.main()
