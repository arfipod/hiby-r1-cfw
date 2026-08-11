from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "tools/run-r1-ui-qemu.sh"


class R1QemuRunnerPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = RUNNER.read_text(encoding="utf-8")

    def test_runner_remains_valid_posix_shell(self) -> None:
        subprocess.run(["sh", "-n", str(RUNNER)], check=True)

    def test_explicit_profile_is_size_checked_and_prepared(self) -> None:
        self.assertIn("profile_template=${R1_QEMU_USER_INI:-}", self.script)
        self.assertIn('profile_size=$(wc -c < "$profile_source")', self.script)
        self.assertIn('if [ "$profile_size" -ne 2768 ]; then', self.script)
        self.assertIn('userdata.py" prepare', self.script)

    def test_sd_fixture_is_strict_and_controls_the_device_bind(self) -> None:
        self.assertIn("sd_present=${R1_QEMU_SD_PRESENT:-1}", self.script)
        self.assertIn('R1_QEMU_SD_PRESENT must be 0 or 1', self.script)
        self.assertIn(
            'set -- "$@" -b "$runtime_dev/mmcblk0p1:/dev/mmcblk0p1"',
            self.script,
        )
        self.assertIn("R1_CFW_ASSUME_SD_MOUNTED=1", self.script)

    def test_cfw_launcher_variants_are_limited_to_three_disposable_targets(self) -> None:
        self.assertIn('patch_r1_launcher.py" \\', self.script)
        self.assertIn('select-config "$launcher_config"', self.script)
        self.assertIn('[0-9a-f][0-9a-f])', self.script)
        self.assertIn('printf \'launcher_mask=%s\\n\'', self.script)
        self.assertIn('"theme1:theme1"', self.script)
        self.assertIn('"theme2:theme2"', self.script)
        self.assertIn('"midi-theme1:midi/theme1"', self.script)
        self.assertEqual(
            1,
            self.script.count(
                "launcher/hiby_launcher_apps.view\n"
            ),
        )

    def test_cfw_hook_precedes_the_shim_and_semantic_state_is_exposed(self) -> None:
        self.assertIn(
            "guest_preload=/usr/lib/libr1-cfw-hook.so:$guest_preload",
            self.script,
        )
        self.assertIn("R1_CFW_FB_FORMAT=rgb565-padded", self.script)
        self.assertIn("R1_CFW_FB_PACKED_RGB565=1", self.script)
        self.assertIn(
            "R1_CFW_TEST_STATE_PATH=/tmp/r1-ui-run/cfw-state.bin",
            self.script,
        )
        self.assertIn('"$runtime/hgl-dma.raw" "$runtime/cfw-state.bin"', self.script)

    def test_forced_sidecar_crash_control_is_validated_and_guest_scoped(self) -> None:
        self.assertIn("cfw_crash_after_ms=${R1_QEMU_CFW_CRASH_AFTER_MS:-}", self.script)
        self.assertIn("must be an integer from 1 to 3600000", self.script)
        self.assertIn(
            "R1_CFW_TEST_CRASH_AFTER_MS=$cfw_crash_after_ms",
            self.script,
        )

    def test_root_backend_uses_bwrap_without_weakening_network_isolation(self) -> None:
        self.assertIn("root_backend=${R1_QEMU_ROOT_BACKEND:-auto}", self.script)
        self.assertIn("auto|proot|bwrap)", self.script)
        self.assertIn('--bind "$rootfs" /', self.script)
        self.assertIn('--ro-bind "$qemu" /tmp/r1-host-qemu', self.script)
        self.assertIn(
            'unshare --user --net --map-root-user "$@"',
            self.script,
        )
        self.assertIn('run_player_bwrap "$1"', self.script)

    def test_sys_server_can_only_be_skipped_explicitly(self) -> None:
        self.assertIn("skip_sys_server=${R1_QEMU_SKIP_SYS_SERVER:-0}", self.script)
        self.assertIn("R1_QEMU_SKIP_SYS_SERVER must be 0 or 1", self.script)
        self.assertIn('if [ "$skip_sys_server" = 0 ]; then', self.script)
        self.assertIn(
            "sys_server stub: explicitly disabled for this QEMU session",
            self.script,
        )


if __name__ == "__main__":
    unittest.main()
