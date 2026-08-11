from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/cfw_validate.py"
SPEC = importlib.util.spec_from_file_location("cfw_validate", MODULE_PATH)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


class CfwQemuValidationTests(unittest.TestCase):
    def test_plan_covers_every_release_gate_without_hardware_writes(self) -> None:
        required = validation.REQUIRED_CHECKS
        for check in (
            "stock.launcher",
            "default.launcher.71",
            "cfw.lifecycle",
            "cfw.framebuffer-restore",
            "cfw.crash-recovery",
            "cfw.ssh",
            "cfw.wifi-route",
            "cfw.bluetooth-route",
            "launcher.persistence.7f",
            "launcher.all-enabled-routes",
            "launcher.scroll-no-activation",
            "launcher.restart-position",
            "storage.sd-present",
            "storage.sd-absent",
        ):
            self.assertIn(check, required)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, validation.main(["plan"]))
        document = json.loads(output.getvalue())
        self.assertEqual(1, document["schema_version"])
        self.assertFalse(document["destructive_actions"])
        self.assertFalse(document["hardware_writes"])
        self.assertEqual(sorted(required), document["required_checks"])

    def test_semantic_state_is_exact_and_rejects_short_or_wrong_headers(self) -> None:
        data = validation.CFW_STATE.pack(
            validation.CFW_STATE_MAGIC,
            validation.CFW_STATE_VERSION,
            9,
            validation.SCREEN_LAUNCHER,
            validation.ALL_MASK,
            validation.ACTION_LAUNCHER_TOGGLE,
            validation.ROUTE_BACK,
        )
        state = validation.SemanticState.parse(data)
        self.assertEqual(9, state.sequence)
        self.assertEqual("7f", state.as_dict()["launcher_mask"])

        with self.assertRaisesRegex(validation.ValidationError, "28"):
            validation.SemanticState.parse(data[:-1])
        wrong = bytearray(data)
        struct.pack_into("<I", wrong, 0, 0)
        with self.assertRaisesRegex(validation.ValidationError, "header"):
            validation.SemanticState.parse(bytes(wrong))

    def test_manifest_cannot_pass_with_a_missing_check_and_failure_has_no_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts"
            evidence = validation.Evidence(root, "unit-run")
            with self.assertRaisesRegex(validation.ValidationError, "mandatory"):
                evidence.finish("a" * 64, {})

            evidence.fail(validation.ValidationError("deliberate failure"))
            failed = json.loads(evidence.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("FAIL", failed["status"])
            self.assertNotIn("candidate_rootfs_sha256", failed)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts"
            evidence = validation.Evidence(root, "complete-run")
            for check in validation.REQUIRED_CHECKS:
                evidence.record(check)
            for index, (name, label) in enumerate(
                validation.FEATURED_SCREENSHOTS.items()
            ):
                snapshot = SimpleNamespace(
                    image=Image.new("RGB", (480, 800), (index * 50, 30, 90)),
                    content_sha256=f"{index + 1:064x}",
                    sha256=f"{index + 11:064x}",
                    sequence=index + 1,
                    yoffset=0,
                )
                screenshot = evidence.screenshot("unit", label, snapshot)
                evidence.promote(name, screenshot)
            evidence.finish("b" * 64, {"candidate_rootfs_image": "/candidate"})
            passed = json.loads(evidence.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(1, passed["schema_version"])
            self.assertEqual("PASS", passed["status"])
            self.assertEqual("b" * 64, passed["candidate_rootfs_sha256"])
            self.assertFalse(passed["hardware_tested"])
            self.assertEqual(
                set(validation.FEATURED_SCREENSHOTS),
                set(passed["featured_screenshots"]),
            )
            for name in passed["featured_screenshots"]:
                self.assertTrue((root / name).is_file())
                self.assertEqual(
                    name, passed["featured_screenshots"][name]["path"]
                )
            self.assertEqual(
                sorted(validation.REQUIRED_CHECKS),
                [item["id"] for item in passed["checks"]],
            )

    def test_preflight_hashes_the_image_and_rejects_a_stale_expected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate"
            stock = root / "stock"
            for relative in (
                "lib/ld.so.1",
                "usr/bin/hiby_player",
                "usr/bin/r1-cfw-ui",
                "usr/lib/libr1-cfw-hook.so",
                "usr/resource/r1-cfw/launcher/theme1/71.view",
                "usr/resource/r1-cfw/launcher/theme1/7f.view",
            ):
                path = candidate / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"candidate")
            for relative in ("lib/ld.so.1", "usr/bin/hiby_player"):
                path = stock / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"stock")
            profile = root / "user.ini"
            profile.write_bytes(bytes(2768))
            image = root / "rootfs.squashfs"
            image.write_bytes(b"exact candidate image")
            runner = root / "runner.sh"
            runner.write_text("#!/bin/sh\n", encoding="ascii")

            args = argparse.Namespace(
                candidate_rootfs_dir=candidate,
                candidate_rootfs_image=image,
                stock_rootfs_dir=stock,
                profile=profile,
                runner=runner,
                expected_rootfs_sha256="",
            )
            digest, inputs = validation.preflight(args)
            self.assertEqual(validation.sha256_file(image), digest)
            self.assertEqual(str(image.resolve()), inputs["candidate_rootfs_image"])

            args.expected_rootfs_sha256 = "0" * 64
            with self.assertRaisesRegex(validation.ValidationError, "digest mismatch"):
                validation.preflight(args)

    def test_exact_candidate_extraction_uses_the_hashed_image_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "candidate.squashfs"
            image.write_bytes(b"candidate")
            destination = root / "runtime/exact-candidate-rootfs"
            log = root / "extract.log"

            def materialize(command: list[str], **_kwargs: object) -> None:
                self.assertIn(str(image), command)
                self.assertIn(str(destination), command)
                for relative in (
                    "lib/ld.so.1",
                    "usr/bin/hiby_player",
                    "usr/bin/r1-cfw-ui",
                    "usr/lib/libr1-cfw-hook.so",
                    "usr/resource/r1-cfw/launcher/theme1/71.view",
                    "usr/resource/r1-cfw/launcher/theme1/7f.view",
                ):
                    path = destination / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"exact")

            with mock.patch.object(validation.subprocess, "run", side_effect=materialize):
                validation.extract_exact_candidate(image, destination, log)
            self.assertTrue((destination / "usr/bin/r1-cfw-ui").is_file())

    def test_geometry_matches_the_sidecar_rows_and_safe_masks(self) -> None:
        self.assertEqual(116, validation.main_row_y(0))
        self.assertEqual(692, validation.main_row_y(8))
        self.assertEqual(188, validation.launcher_row_y(1))
        self.assertEqual((0x71, 0x77, 0x7F), (
            validation.DEFAULT_MASK,
            validation.SIX_TILE_MASK,
            validation.ALL_MASK,
        ))

    def test_cli_rejects_noncanonical_expected_hash_before_running(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = validation.main(
                [
                    "run",
                    "--candidate-rootfs-dir", "/missing",
                    "--candidate-rootfs-image", "/missing",
                    "--stock-rootfs-dir", "/missing",
                    "--profile", "/missing",
                    "--expected-rootfs-sha256", "A" * 64,
                ]
            )
        self.assertEqual(2, result)
        self.assertIn("lowercase", output.getvalue())


if __name__ == "__main__":
    unittest.main()
