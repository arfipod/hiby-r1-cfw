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

    def test_fresh_install_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_control(Path(temporary), "is-enabled")
            self.assertEqual(1, result.returncode)
            self.assertEqual("disabled", result.stdout.strip())

    def test_enable_disable_and_toggle_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_control(root, "enable", check=True)
            enabled = root / "state/enabled"
            disabled = root / "state/disabled"
            self.assertTrue(enabled.is_file())
            self.assertFalse(disabled.exists())
            self.assertEqual(0o600, stat.S_IMODE(enabled.stat().st_mode))
            self.assertEqual(
                0, self.run_control(root, "is-enabled").returncode
            )

            self.run_control(root, "toggle", check=True)
            self.assertFalse(enabled.exists())
            self.assertTrue(disabled.is_file())
            self.assertEqual(0o600, stat.S_IMODE(disabled.stat().st_mode))
            self.assertEqual(
                1, self.run_control(root, "is-enabled").returncode
            )

            self.run_control(root, "toggle", check=True)
            self.assertTrue(enabled.is_file())
            self.assertFalse(disabled.exists())

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
