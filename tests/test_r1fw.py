from __future__ import annotations

import argparse
import binascii
import hashlib
import importlib.util
import os
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location("r1fw", REPO_ROOT / "tools/r1fw.py")
assert MODULE_SPEC and MODULE_SPEC.loader
r1fw = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(r1fw)


def make_uimage(payload: bytes, name: bytes = b"CI synthetic kernel") -> bytes:
    name_field = name[:32].ljust(32, b"\0")
    data_crc = binascii.crc32(payload) & 0xFFFFFFFF
    header = struct.pack(
        ">7I4B32s",
        0x27051956,
        0,
        1_700_000_000,
        len(payload),
        0x80F00000,
        0x80F00000,
        data_crc,
        5,
        5,
        2,
        0,
        name_field,
    )
    header_crc = binascii.crc32(header) & 0xFFFFFFFF
    return header[:4] + struct.pack(">I", header_crc) + header[8:] + payload


class R1FirmwareUnitTests(unittest.TestCase):
    def test_rock_ridge_timestamp_encoding_is_utc_and_bounded(self) -> None:
        short, long = r1fw._rock_ridge_timestamps(1_767_003_664)
        self.assertEqual(bytes((125, 12, 29, 10, 21, 4, 0)), short)
        self.assertEqual(b"2025122910210400\x00", long)
        with self.assertRaisesRegex(RuntimeError, "ISO9660 year range"):
            r1fw._rock_ridge_timestamps(-2_208_988_801)

    def test_verify_file_accepts_exact_digest_and_size_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.upt"
            path.write_bytes(b"exact firmware fixture")
            expected = hashlib.sha256(path.read_bytes()).hexdigest()

            self.assertEqual(
                (path.stat().st_size, expected),
                r1fw.verify_file(
                    path,
                    expected_sha256=expected,
                    max_size=path.stat().st_size,
                ),
            )

    def test_verify_file_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "firmware.upt"
            path.write_bytes(b"unexpected firmware")

            with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
                r1fw.verify_file(path, expected_sha256="0" * 64)

    def test_verify_file_rejects_one_byte_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rootfs.squashfs"
            path.write_bytes(b"12345")

            with self.assertRaisesRegex(RuntimeError, "5 > 4 bytes"):
                r1fw.verify_file(path, max_size=4)

    def test_verify_file_rejects_invalid_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.bin"
            path.write_bytes(b"fixture")

            with self.assertRaisesRegex(RuntimeError, "requires"):
                r1fw.verify_file(path)
            with self.assertRaisesRegex(RuntimeError, "64 lowercase hex"):
                r1fw.verify_file(path, expected_sha256="not-a-digest")
            with self.assertRaisesRegex(RuntimeError, "must not be negative"):
                r1fw.verify_file(path, max_size=-1)

    def test_chunk_chain_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"A" * r1fw.CHUNK_SIZE + b"tail")
            ota = root / "ota"
            ota.mkdir()

            size, digest = r1fw.split_image(source, ota, "rootfs.squashfs")
            chunks = r1fw.find_images(ota)["rootfs.squashfs"]
            output = root / "rebuilt.bin"
            rebuilt = r1fw.rebuild_image("rootfs.squashfs", chunks, output)

            self.assertEqual((size, digest), rebuilt)
            self.assertEqual(source.read_bytes(), output.read_bytes())

            chunks[1][1].write_bytes(b"corrupt")
            with self.assertRaisesRegex(RuntimeError, "chunk 1"):
                r1fw.rebuild_image("rootfs.squashfs", chunks, root / "bad.bin")

    def test_metadata_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            metadata = Path(temporary) / "ota_update.in"
            digest = hashlib.md5(b"sample").hexdigest()
            metadata.write_text(
                "img_type=kernel\n"
                "img_name=xImage\n"
                "img_size=6\n"
                f"img_md5={digest}\n\n",
                encoding="ascii",
            )
            self.assertEqual({"xImage": (6, digest)}, r1fw.parse_ota_metadata(metadata))

            metadata.write_text("img_type=kernel\nimg_name=xImage\n", encoding="ascii")
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                r1fw.parse_ota_metadata(metadata)

    def test_uimage_crc_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "xImage"
            image.write_bytes(make_uimage(b"synthetic payload"))
            with redirect_stdout(StringIO()) as output:
                r1fw.inspect_uimage(image)
            self.assertIn("header CRC32", output.getvalue())
            self.assertIn("(ok)", output.getvalue())

            corrupted = bytearray(image.read_bytes())
            corrupted[-1] ^= 0xFF
            image.write_bytes(corrupted)
            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "CRC"):
                    r1fw.inspect_uimage(image)

    def test_jpeg_geometry_parser(self) -> None:
        # The parser only needs SOI and a valid baseline SOF segment.
        jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08\x03\x20\x01\xe0" + b"\0" * 12
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "splash.jpg"
            path.write_bytes(jpeg)
            self.assertEqual((480, 800, 8), r1fw.jpeg_dimensions(path))

    def test_png_geometry_parser(self) -> None:
        png_header = (
            b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 17, 29)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sprite.png"
            path.write_bytes(png_header)
            self.assertEqual((17, 29), r1fw.png_dimensions(path))

    def test_overlay_copies_files_and_preserves_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            overlay = base / "overlay"
            root.mkdir()
            (overlay / "usr/bin").mkdir(parents=True)
            script = overlay / "usr/bin/example"
            script.write_text("#!/bin/sh\n", encoding="ascii")
            script.chmod(0o755)
            (overlay / "usr/bin").chmod(0o750)
            (overlay / "usr/bin/example-link").symlink_to("example")

            with redirect_stdout(StringIO()):
                r1fw.command_apply_overlay(argparse.Namespace(root=root, overlay=overlay))

            self.assertEqual(0o755, (root / "usr/bin/example").stat().st_mode & 0o777)
            self.assertEqual(0o750, (root / "usr/bin").stat().st_mode & 0o777)
            self.assertEqual(
                Path("example"), (root / "usr/bin/example-link").readlink()
            )

    def test_rootfs_diff_strict_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            before = base / "before"
            after = base / "after"
            before.mkdir()
            after.mkdir()
            (before / "changed").write_text("old", encoding="ascii")
            (after / "changed").write_text("new", encoding="ascii")
            (after / "added").write_text("new", encoding="ascii")

            args = argparse.Namespace(
                before=before,
                after=after,
                strict=True,
                expect_added=["added"],
                expect_removed=[],
                expect_changed=["changed"],
            )
            with redirect_stdout(StringIO()):
                r1fw.command_diff_rootfs(args)

            args.expect_added = []
            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(RuntimeError, "strict expected"):
                    r1fw.command_diff_rootfs(args)


@unittest.skipUnless(
    all(shutil.which(program) for program in ("7z", "genisoimage", "mksquashfs")),
    "firmware round-trip tools are not installed",
)
class R1FirmwareIntegrationTests(unittest.TestCase):
    def test_synthetic_firmware_pack_and_unpack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ximage = root / "xImage"
            ximage.write_bytes(make_uimage(b"K" * (r1fw.CHUNK_SIZE + 17)))

            squash_root = root / "squash-root"
            squash_root.mkdir()
            (squash_root / "fixture.txt").write_text("CI fixture\n", encoding="ascii")
            squashfs = root / "rootfs.squashfs"
            subprocess.run(
                [
                    "mksquashfs",
                    str(squash_root),
                    str(squashfs),
                    "-noappend",
                    "-comp",
                    "lzo",
                    "-all-root",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )

            firmware = root / "synthetic.upt"
            repeated_firmware = root / "synthetic-repeated.upt"
            with mock.patch.dict(
                os.environ, {"SOURCE_DATE_EPOCH": "1767003664"}
            ):
                with redirect_stdout(StringIO()):
                    r1fw.command_pack(
                        argparse.Namespace(
                            output=firmware,
                            force=False,
                            ota_version=0,
                            ximage=ximage,
                            rootfs=squashfs,
                            genisoimage=None,
                        )
                    )
                    time.sleep(1.05)
                    r1fw.command_pack(
                        argparse.Namespace(
                            output=repeated_firmware,
                            force=False,
                            ota_version=0,
                            ximage=ximage,
                            rootfs=squashfs,
                            genisoimage=None,
                        )
                    )
                    extracted = root / "extracted"
                    r1fw.command_unpack(
                        argparse.Namespace(
                            firmware=firmware, output=extracted, force=False
                        )
                    )

            self.assertEqual(firmware.read_bytes(), repeated_firmware.read_bytes())
            self.assertEqual(ximage.read_bytes(), (extracted / "images/xImage").read_bytes())
            self.assertEqual(
                squashfs.read_bytes(),
                (extracted / "images/rootfs.squashfs").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
