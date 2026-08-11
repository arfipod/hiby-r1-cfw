from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "mods/cfw-ui/rootfs-overlay/etc/init.d/S90r1-cfw"


class R1CfwThemeSelectorTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, dict[str, str], Path]:
        resource = root / "resource"
        state = root / "state"
        state.mkdir()
        for path in (
            "layout/theme1/launcher",
            "layout/theme2/launcher",
            "layout/midi/theme1/launcher",
            "litegui/theme1",
            "litegui/theme2",
            "r1-cfw/launcher/theme1",
            "r1-cfw/launcher/theme2",
            "r1-cfw/launcher/midi-theme1",
            "r1-cfw/launcher/retro",
            "r1-cfw/themes/retro/layout/launcher",
            "r1-cfw/themes/retro/layout/topbar",
            "r1-cfw/themes/retro/litegui/launcher",
            "r1-cfw/themes/retro/litegui/topbar",
        ):
            (resource / path).mkdir(parents=True, exist_ok=True)
        for path in (
            "layout/theme1/launcher/hiby_launcher_apps.view",
            "layout/theme2/launcher/hiby_launcher_apps.view",
            "layout/midi/theme1/launcher/hiby_launcher_apps.view",
        ):
            (resource / path).write_text("stock\n", encoding="ascii")
        for theme in ("theme1", "theme2", "midi-theme1", "retro"):
            (resource / f"r1-cfw/launcher/{theme}/71.view").write_text(
                "variant\n", encoding="ascii"
            )
        (resource / "r1-cfw/themes/retro/.r1-theme").write_text(
            "theme=retro-handheld\nformat=1\n", encoding="ascii"
        )
        for path in (
            "r1-cfw/themes/retro/layout/launcher/hiby_launcher_apps.view",
            "r1-cfw/themes/retro/layout/topbar/topbar.view",
            "r1-cfw/themes/retro/litegui/launcher/music.png",
            "r1-cfw/themes/retro/litegui/topbar/retro_bg.png",
        ):
            (resource / path).write_bytes(b"asset")

        log = root / "mount.log"
        mount = root / "mount"
        mount.write_text(
            "#!/bin/sh\nprintf 'mount %s\\n' \"$*\" >> \"$R1_TEST_LOG\"\n",
            encoding="ascii",
        )
        umount = root / "umount"
        umount.write_text(
            "#!/bin/sh\nprintf 'umount %s\\n' \"$*\" >> \"$R1_TEST_LOG\"\n",
            encoding="ascii",
        )
        mountpoint = root / "mountpoint"
        mountpoint.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
        for executable in (mount, umount, mountpoint):
            executable.chmod(0o755)
        environment = os.environ.copy()
        environment.update(
            {
                "R1_CFW_RESOURCE_ROOT": str(resource),
                "R1_CFW_STATE_DIR": str(state),
                "R1_CFW_MOUNT": str(mount),
                "R1_CFW_UMOUNT": str(umount),
                "R1_CFW_MOUNTPOINT": str(mountpoint),
                "R1_TEST_LOG": str(log),
            }
        )
        (state / "launcher.conf").write_text("launcher_mask=71\n", encoding="ascii")
        return resource, state, environment, log

    def run_selector(self, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(SELECTOR), "start"],
            text=True,
            capture_output=True,
            env=environment,
            timeout=5,
            check=False,
        )

    def test_stock_light_dark_and_retro_are_complete_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resource, state, environment, log = self.fixture(Path(temporary))
            expectations = {
                "stock": (3, "/launcher/theme1/71.view", "/launcher/theme2/71.view"),
                "light": (5, "/layout/theme1 /", "/launcher/theme1/71.view"),
                "dark": (5, "/layout/theme2 /", "/launcher/theme2/71.view"),
                "retro": (7, "/themes/retro/layout", "/launcher/retro/71.view"),
            }
            for theme, (count, first, second) in expectations.items():
                with self.subTest(theme=theme):
                    log.write_text("", encoding="ascii")
                    (state / "theme.conf").write_text(
                        f"theme={theme}\n", encoding="ascii"
                    )
                    result = self.run_selector(environment)
                    self.assertEqual(0, result.returncode, result.stderr)
                    lines = log.read_text(encoding="ascii").splitlines()
                    self.assertEqual(count, len(lines), lines)
                    joined = "\n".join(lines)
                    self.assertIn(first, joined)
                    self.assertIn(second, joined)
                    self.assertIn(
                        str(resource / "r1-cfw/launcher/midi-theme1/71.view"),
                        joined,
                    )

    def test_incomplete_retro_and_malformed_state_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            resource, state, environment, log = self.fixture(Path(temporary))
            (state / "theme.conf").write_text("theme=retro\n", encoding="ascii")
            (resource / "r1-cfw/themes/retro/.r1-theme").unlink()
            result = self.run_selector(environment)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(log.exists() and log.stat().st_size)

            (state / "theme.conf").write_text(
                "theme=retro\ntheme=dark\n", encoding="ascii"
            )
            log.write_text("", encoding="ascii")
            result = self.run_selector(environment)
            self.assertEqual(0, result.returncode, result.stderr)
            lines = log.read_text(encoding="ascii").splitlines()
            self.assertEqual(3, len(lines), lines)
            self.assertIn("/launcher/theme1/71.view", lines[0])
            self.assertIn("/launcher/theme2/71.view", lines[1])


if __name__ == "__main__":
    unittest.main()
