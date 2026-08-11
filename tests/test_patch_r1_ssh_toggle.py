from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/patch_r1_ssh_toggle.py"
MODULE_SPEC = importlib.util.spec_from_file_location("patch_r1_ssh_toggle", MODULE_PATH)
assert MODULE_SPEC and MODULE_SPEC.loader
ssh_toggle = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = ssh_toggle
MODULE_SPEC.loader.exec_module(ssh_toggle)

STOCK_ROOTFS = REPO_ROOT / "work/r1-1.6/rootfs-full"
STOCK_BINARY = STOCK_ROOTFS / "usr/bin/hiby_player"


def synthetic_stock_binary() -> bytes:
    size = ssh_toggle.CAVE_OFFSET + ssh_toggle.CAVE_SIZE
    data = bytearray(size)
    for patch in ssh_toggle.BINARY_PATCHES:
        end = patch.offset + len(patch.original)
        data[patch.offset:end] = patch.original
    return bytes(data)


def decode_jump_target(instruction: int, pc: int) -> int:
    return ((pc + 4) & 0xF0000000) | ((instruction & 0x03FFFFFF) << 2)


def word_at(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def synthetic_translation(newline: str = "\r\n") -> bytes:
    text = newline.join(
        (
            '<?Python version="3.9.7" encoding="utf-16"?>',
            "<resources>",
            "  <volume_locked>Volume locked</volume_locked>",
            "  <screen_short>Screenshot</screen_short>",
            "</resources>",
            "",
        )
    )
    return b"\xff\xfe" + text.encode("utf-16le")


class R1SshToggleBinaryTests(unittest.TestCase):
    def test_exact_hook_plan_and_code_cave_layout(self) -> None:
        cave = ssh_toggle.build_cave()
        self.assertEqual(ssh_toggle.CAVE_SIZE, len(cave))
        self.assertEqual(
            bytes.fromhex(
                "68487800"  # volume_locked pointer
                "78487800"  # screen_short pointer
                "0ce17500"  # ssh_server pointer in the code cave
            ),
            cave[:12],
        )
        self.assertEqual(
            b"ssh_server\0",
            cave[
                ssh_toggle.SSH_KEY_ASCII_ADDRESS - ssh_toggle.CAVE_ADDRESS :
                ssh_toggle.SSH_KEY_ASCII_ADDRESS - ssh_toggle.CAVE_ADDRESS + 11
            ],
        )
        self.assertIn(b"/usr/data/dropbear/enabled\0", cave)
        self.assertIn(b"/usr/bin/r1-ssh-control toggle\0", cave)
        self.assertEqual(
            "ssh_server\0".encode("utf-16le"),
            cave[
                ssh_toggle.SSH_KEY_WIDE_ADDRESS - ssh_toggle.CAVE_ADDRESS :
                ssh_toggle.SSH_KEY_WIDE_ADDRESS - ssh_toggle.CAVE_ADDRESS + 22
            ],
        )

        expected_hooks = (
            (0x0D193C, ssh_toggle.KEY_SETUP_ADDRESS),
            (0x0D1B74, ssh_toggle.STATE_HOOK_ADDRESS),
            (0x0D1F24, ssh_toggle.CLICK_HOOK_ADDRESS),
        )
        for patch, (offset, destination) in zip(
            ssh_toggle.BINARY_PATCHES[:3], expected_hooks
        ):
            self.assertEqual(offset, patch.offset)
            self.assertEqual(0, word_at(patch.patched, 4))
            self.assertEqual(
                destination,
                decode_jump_target(word_at(patch.patched, 0), offset + 0x00400000),
            )

        self.assertEqual(
            "da0e8e6f2c3bbb76dd40b5e49b14179a15f198f4a2f3f3c7bbbcaeacf47048db",
            ssh_toggle.sha256_bytes(cave),
        )
        self.assertLessEqual(
            ssh_toggle.CLICK_HOOK_ADDRESS
            - ssh_toggle.CAVE_ADDRESS
            + len(ssh_toggle.build_click_hook()),
            ssh_toggle.CAVE_SIZE,
        )

    def test_synthetic_apply_and_verify_are_exact(self) -> None:
        stock = synthetic_stock_binary()
        patched = ssh_toggle.apply_binary_patch(stock, enforce_hash=False)
        self.assertEqual(len(stock), len(patched))
        for patch in ssh_toggle.BINARY_PATCHES:
            end = patch.offset + len(patch.patched)
            self.assertEqual(patch.patched, patched[patch.offset:end])
        ssh_toggle.verify_binary_patch(patched, enforce_hash=False)

        with self.assertRaisesRegex(RuntimeError, "unexpected preimage"):
            ssh_toggle.apply_binary_patch(patched, enforce_hash=False)

    def test_apply_rejects_a_changed_hook_and_changed_cave(self) -> None:
        for changed_offset, expected_name in (
            (0x0D193C, "developer row-key array setup"),
            (ssh_toggle.CAVE_OFFSET + 0x1FF, "SSH toggle code cave"),
        ):
            data = bytearray(synthetic_stock_binary())
            data[changed_offset] ^= 0x01
            with self.subTest(offset=changed_offset):
                with self.assertRaisesRegex(RuntimeError, expected_name):
                    ssh_toggle.apply_binary_patch(bytes(data), enforce_hash=False)

    def test_verify_rejects_tampering_inside_and_outside_patch_ranges(self) -> None:
        patched = bytearray(
            ssh_toggle.apply_binary_patch(
                synthetic_stock_binary(), enforce_hash=False
            )
        )
        patched[ssh_toggle.STATE_HOOK_ADDRESS - 0x00400000] ^= 0x01
        with self.assertRaisesRegex(RuntimeError, "SSH toggle code cave"):
            ssh_toggle.verify_binary_patch(bytes(patched), enforce_hash=False)

        # Production verification also checks the whole-file digest, so bytes
        # outside the declared ranges cannot be changed silently.
        exact_stock = bytearray(synthetic_stock_binary())
        exact_stock[0x100] ^= 0x01
        with self.assertRaisesRegex(RuntimeError, "not exact stock R1 1.6"):
            ssh_toggle.apply_binary_patch(bytes(exact_stock), enforce_hash=True)

    @unittest.skipUnless(STOCK_BINARY.is_file(), "extracted stock firmware unavailable")
    def test_real_stock_binary_has_the_locked_preimage_and_digest(self) -> None:
        stock = STOCK_BINARY.read_bytes()
        self.assertEqual(ssh_toggle.STOCK_BINARY_SHA256, ssh_toggle.sha256_bytes(stock))
        patched = ssh_toggle.apply_binary_patch(stock)
        self.assertEqual(
            ssh_toggle.PATCHED_BINARY_SHA256, ssh_toggle.sha256_bytes(patched)
        )
        ssh_toggle.verify_binary_patch(patched)


class R1SshToggleTranslationTests(unittest.TestCase):
    def test_translation_patch_preserves_bom_and_newline_style(self) -> None:
        for newline in ("\r\n", "\n"):
            with self.subTest(newline=repr(newline)):
                patched = ssh_toggle.patch_translation(
                    synthetic_translation(newline), "SSH server"
                )
                self.assertTrue(patched.startswith(b"\xff\xfe"))
                text = patched[2:].decode("utf-16le")
                self.assertEqual(1, text.count("<ssh_server>SSH server</ssh_server>"))
                self.assertEqual(
                    0 if newline == "\r\n" else text.count("\r\n"),
                    text.replace("\r\n", "").count("\r\n"),
                )
                if newline == "\r\n":
                    self.assertNotIn("\n", text.replace("\r\n", ""))
                else:
                    self.assertNotIn("\r", text)

    def test_translation_patch_rejects_invalid_or_repeated_input(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "UTF-16LE"):
            ssh_toggle.patch_translation(b"<resources/>", "SSH server")

        patched = ssh_toggle.patch_translation(synthetic_translation(), "SSH server")
        with self.assertRaisesRegex(RuntimeError, "already contains"):
            ssh_toggle.patch_translation(patched, "SSH server")

        invalid = b"\xff\xfe" + "<resources/>".encode("utf-16le")
        with self.assertRaisesRegex(RuntimeError, "unexpected resources root"):
            ssh_toggle.patch_translation(invalid, "SSH server")

    @unittest.skipUnless(STOCK_ROOTFS.is_dir(), "extracted stock firmware unavailable")
    def test_real_stock_translation_digests_are_locked(self) -> None:
        resource_root = STOCK_ROOTFS / "usr/resource"
        for language, spec in ssh_toggle.TRANSLATIONS.items():
            path = ssh_toggle.translation_path(resource_root, language)
            with self.subTest(language=language):
                stock = path.read_bytes()
                self.assertEqual(spec.stock_sha256, ssh_toggle.sha256_bytes(stock))
                patched = ssh_toggle.patch_translation(stock, spec.label)
                self.assertEqual(spec.patched_sha256, ssh_toggle.sha256_bytes(patched))


class R1SshToggleRootfsTests(unittest.TestCase):
    @unittest.skipUnless(STOCK_ROOTFS.is_dir(), "extracted stock firmware unavailable")
    def test_apply_and_verify_on_an_isolated_stock_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rootfs = Path(temporary)
            binary_target = rootfs / "usr/bin/hiby_player"
            binary_target.parent.mkdir(parents=True)
            binary_target.write_bytes(STOCK_BINARY.read_bytes())
            os.chmod(binary_target, ssh_toggle.STOCK_BINARY_MODE)

            stock_resource = STOCK_ROOTFS / "usr/resource"
            for language in ssh_toggle.TRANSLATIONS:
                source = ssh_toggle.translation_path(stock_resource, language)
                target = ssh_toggle.translation_path(
                    rootfs / "usr/resource", language
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                os.chmod(target, ssh_toggle.TRANSLATION_MODE)

            original_mtime = 1_650_000_123_000_000_000
            os.utime(binary_target, ns=(original_mtime, original_mtime))
            ssh_toggle.apply_rootfs(rootfs)
            ssh_toggle.verify_rootfs(rootfs)
            self.assertEqual(
                ssh_toggle.PATCHED_BINARY_SHA256,
                ssh_toggle.sha256_bytes(binary_target.read_bytes()),
            )
            self.assertEqual(
                ssh_toggle.STOCK_BINARY_MODE,
                stat.S_IMODE(binary_target.stat().st_mode),
            )
            self.assertEqual(original_mtime, binary_target.stat().st_mtime_ns)

    @unittest.skipUnless(STOCK_ROOTFS.is_dir(), "extracted stock firmware unavailable")
    def test_validation_failure_does_not_modify_any_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rootfs = Path(temporary)
            binary_target = rootfs / "usr/bin/hiby_player"
            binary_target.parent.mkdir(parents=True)
            binary_target.write_bytes(STOCK_BINARY.read_bytes())
            os.chmod(binary_target, ssh_toggle.STOCK_BINARY_MODE)

            stock_resource = STOCK_ROOTFS / "usr/resource"
            snapshots: dict[Path, bytes] = {binary_target: binary_target.read_bytes()}
            for language in ssh_toggle.TRANSLATIONS:
                source = ssh_toggle.translation_path(stock_resource, language)
                target = ssh_toggle.translation_path(
                    rootfs / "usr/resource", language
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                os.chmod(target, ssh_toggle.TRANSLATION_MODE)
                snapshots[target] = target.read_bytes()

            bad = ssh_toggle.translation_path(
                rootfs / "usr/resource", "ukrainian"
            )
            bad.write_bytes(bad.read_bytes() + b"\0\0")
            snapshots[bad] = bad.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "ukrainian.*not exact stock"):
                ssh_toggle.apply_rootfs(rootfs)
            for path, expected in snapshots.items():
                self.assertEqual(expected, path.read_bytes(), str(path))


if __name__ == "__main__":
    unittest.main()
