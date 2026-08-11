from __future__ import annotations

import fcntl
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "tools/build-cfw-0.1.sh"


class Cfw01BuildPipelineTests(unittest.TestCase):
    def test_build_uses_a_deterministic_umask(self) -> None:
        self.assertIn("set -eu\numask 022\n", self.script)
        for mode, path in (
            ("755", "etc/init.d/S90r1-cfw"),
            ("755", "etc/init.d/S91dropbear"),
            ("600", "etc/shadow"),
            ("700", "root/.ssh"),
            ("755", "usr/bin/r1-cfw-ui"),
            ("755", "usr/lib/libr1-cfw-hook.so"),
            ("755", "usr/sbin/dropbearmulti"),
        ):
            self.assertIn(f'require_mode {mode} "$check_root/{path}"', self.script)

    def test_workspace_is_locked_and_publish_rechecks_exact_upt(self) -> None:
        self.assertIn('exec 8>"$work_dir/pipeline.lock"', self.script)
        self.assertIn("flock -n 8", self.script)
        self.assertIn(
            'if [ "$published_upt_sha256" != "$candidate_upt_sha256" ]',
            self.script,
        )
        self.assertLess(
            self.script.index('published_upt_sha256=$(sha256sum "$publish_tmp"'),
            self.script.index('mv -f "$publish_tmp" "$output"'),
        )

    def test_verification_and_publish_use_one_workspace_owned_candidate(self) -> None:
        verify_start = self.script.index("verify_candidate()")
        prepare_start = self.script.index("prepare_candidate()")
        verify = self.script[verify_start:prepare_start]
        self.assertIn('cp "$candidate" "$staged_tmp"', verify)
        self.assertIn('mv -f "$staged_tmp" "$verified_candidate"', verify)
        self.assertIn('unpack "$verified_candidate"', verify)
        self.assertNotIn('unpack "$candidate"', verify)
        self.assertLess(
            verify.index('candidate_upt_sha256=$(sha256sum "$verified_candidate"'),
            verify.index('unpack "$verified_candidate"'),
        )
        self.assertIn(
            'if [ "$verified_upt_sha256" != "$candidate_upt_sha256" ]',
            verify,
        )

        publish_start = self.script.index("publish_candidate()")
        publish_end = self.script.index('\ncase "$mode" in', publish_start)
        publish = self.script[publish_start:publish_end]
        self.assertIn('cp "$verified_candidate" "$publish_tmp"', publish)
        self.assertNotIn('cp "$candidate" "$publish_tmp"', publish)

    def test_workspace_lock_fails_fast_before_candidate_work(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "locked-work"
            work.mkdir()
            with (work / "pipeline.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                environment = os.environ.copy()
                environment["R1_CFW_WORK_DIR"] = str(work)
                result = subprocess.run(
                    [str(BUILD_SCRIPT), "prepare", str(work / "missing.upt")],
                    cwd=REPO_ROOT,
                    env=environment,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            self.assertEqual(1, result.returncode)
            self.assertIn("another CFW v0.1 build owns", result.stderr)
            self.assertFalse((work / "stock").exists())

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_release_identity_limits_and_gate_are_literal(self) -> None:
        self.assertIn(
            "stock_upt_sha256=9aada81995d8d2b2ed80d6cf292c62bc5f0f705e51e4f69c7e766ee67536ba60",
            self.script,
        )
        self.assertIn("rootfs_max_size=47185920", self.script)
        self.assertIn("dist/r1-cfw-0.1-experimental.upt}", self.script)
        self.assertIn("artifacts/ui/cfw-v0.1/validation-manifest.json}", self.script)
        self.assertIn('verify_cfw_validation.py"', self.script)

    def test_prepare_applies_every_component_before_rootfs_build(self) -> None:
        start = self.script.index("prepare_candidate()")
        finish = self.script.index("publish_candidate()")
        prepare = self.script[start:finish]
        build = prepare.index('r1fw.py" build-rootfs')
        required_before_build = (
            'apply-overlay "$root" "$ssh_overlay"',
            'apply-overlay "$root" "$cfw_overlay"',
            'build-r1-cfw-ui.sh"',
            'patch_r1_branding.py" "$root"',
            'patch_r1_ssh_toggle.py" apply "$root"',
            'patch_r1_launcher.py" generate "$root"',
            'patch_r1_cfw_integration.py" "$root"',
        )
        for needle in required_before_build:
            with self.subTest(needle=needle):
                self.assertLess(prepare.index(needle), build)

    def test_verification_reextracts_and_checks_kernel_payload_and_patches(self) -> None:
        start = self.script.index("verify_candidate()")
        finish = self.script.index("prepare_candidate()")
        verify = self.script[start:finish]
        unpack = verify.index('unpack "$verified_candidate"')
        extract = verify.index('"$check_dir/images/rootfs.squashfs" "$check_root"')
        self.assertLess(unpack, extract)
        self.assertIn('cmp "$stock_unpack/images/xImage" "$check_dir/images/xImage"', verify)
        self.assertIn('cmp "$rootfs_image" "$check_dir/images/rootfs.squashfs"', verify)
        self.assertIn('patch_r1_branding.py" "$check_root" --check', verify)
        self.assertIn('patch_r1_ssh_toggle.py" verify "$check_root"', verify)
        self.assertIn('patch_r1_launcher.py" verify "$check_root"', verify)
        self.assertIn('patch_r1_cfw_integration.py" "$check_root" --check', verify)
        self.assertIn('build-r1-cfw-ui.sh" "$ui_verify_build"', verify)
        self.assertIn(
            'cmp "$ui_verify_build/r1-cfw-ui" "$check_root/usr/bin/r1-cfw-ui"',
            self.script,
        )
        self.assertIn(
            'cmp "$ui_verify_build/libr1-cfw-hook.so" "$check_root/usr/lib/libr1-cfw-hook.so"',
            self.script,
        )
        self.assertIn("strict_rootfs_diff", verify)

    def test_strict_allowlist_has_all_launcher_variants_and_language_changes(self) -> None:
        masks_match = re.search(r'launcher_masks="\n(?P<body>.*?)\n"', self.script, re.DOTALL)
        self.assertIsNotNone(masks_match)
        assert masks_match is not None
        masks = masks_match.group("body").split()
        self.assertEqual(41, len(masks))
        self.assertEqual(41, len(set(masks)))
        self.assertNotIn("7f", masks)
        self.assertIn(
            '--expect-added "usr/resource/r1-cfw/launcher/$theme/$mask.view"',
            self.script,
        )
        self.assertIn(
            '--expect-changed "usr/resource/str/$language/launcher.ini"',
            self.script,
        )

    def test_only_publish_path_is_after_manifest_gate_and_uses_atomic_rename(self) -> None:
        start = self.script.index("publish_candidate()")
        finish = self.script.index('\ncase "$mode" in', start)
        publish = self.script[start:finish]
        gate = publish.index('verify_cfw_validation.py"')
        candidate_image = publish.index('"$check_dir/images/rootfs.squashfs"')
        copy = publish.index('cp "$verified_candidate" "$publish_tmp"')
        rename = publish.index('mv -f "$publish_tmp" "$output"')
        self.assertLess(gate, candidate_image)
        self.assertLess(candidate_image, copy)
        self.assertLess(copy, rename)
        self.assertNotIn('--ximage "$stock_unpack/images/xImage"', publish)


if __name__ == "__main__":
    unittest.main()
