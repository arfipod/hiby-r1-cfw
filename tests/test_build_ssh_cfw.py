from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "tools/build-ssh-cfw.sh"

EXPECTED_LANGUAGES = {
    "english",
    "french",
    "german",
    "italy",
    "japanese",
    "korean",
    "poland",
    "russian",
    "simplified_chinese",
    "spain",
    "thai",
    "traditional_chinese",
    "ukrainian",
}


class R1SshCfwBuildPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_default_output_does_not_replace_the_previous_lab_artifact(self) -> None:
        self.assertIn(
            "dist/r1-cfw-ssh-toggle-1.6.upt}",
            self.script,
        )
        self.assertNotIn("dist/r1-cfw-ssh-lab-1.6.upt", self.script)

    def test_patches_are_applied_before_packing_and_verified_after_unpacking(self) -> None:
        branding_apply = 'patch_r1_branding.py" "$root"'
        toggle_apply = 'patch_r1_ssh_toggle.py" apply "$root"'
        build_rootfs = 'r1fw.py" build-rootfs'
        final_extract = self.script.rindex('r1fw.py" extract-rootfs')
        branding_verify = 'patch_r1_branding.py" "$check_root" --check'
        toggle_verify = 'patch_r1_ssh_toggle.py" verify "$check_root"'
        strict_diff = 'r1fw.py" diff-rootfs'

        self.assertLess(self.script.index(branding_apply), self.script.index(build_rootfs))
        self.assertLess(self.script.index(toggle_apply), self.script.index(build_rootfs))
        self.assertLess(final_extract, self.script.index(branding_verify))
        self.assertLess(final_extract, self.script.index(toggle_verify))
        self.assertLess(self.script.index(branding_verify), self.script.index(strict_diff))
        self.assertLess(self.script.index(toggle_verify), self.script.index(strict_diff))

    def test_strict_diff_covers_every_branded_and_toggle_resource(self) -> None:
        match = re.search(
            r'resource_languages="\n(?P<languages>.*?)\n"',
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(EXPECTED_LANGUAGES, set(match.group("languages").splitlines()))
        self.assertIn(
            '--expect-changed "usr/resource/str/$language/about_dev.ini"',
            self.script,
        )
        self.assertIn(
            '--expect-changed "usr/resource/str/$language/developer_options.ini"',
            self.script,
        )
        self.assertNotIn("--expect-changed usr/resource/config.json", self.script)

        for relative_path in (
            "usr/bin/hiby_player",
            "usr/resource/layout/theme1/hiby_about_dev.view",
            "usr/resource/layout/theme2/hiby_about_dev.view",
            "usr/resource/layout/midi/theme1/hiby_about_dev.view",
        ):
            with self.subTest(relative_path=relative_path):
                self.assertIn(f"--expect-changed {relative_path}", self.script)


if __name__ == "__main__":
    unittest.main()
