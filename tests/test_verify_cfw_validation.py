from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/verify_cfw_validation.py"
SPEC = importlib.util.spec_from_file_location("verify_cfw_validation", MODULE_PATH)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


class CfwValidationGateTests(unittest.TestCase):
    digest = "a" * 64

    def write(self, root: Path, document: object) -> Path:
        path = root / "validation-manifest.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def complete_manifest(self, root: Path) -> dict[str, object]:
        screenshots: list[dict[str, object]] = []
        featured: dict[str, dict[str, object]] = {}
        screens = root / "unit-run/screens"
        screens.mkdir(parents=True)
        for index, (canonical_name, label) in enumerate(
            validation.FEATURED_SCREENSHOTS.items()
        ):
            source_relative = f"unit-run/screens/{index:03d}_{label}.png"
            source = root / source_relative
            Image.new("RGB", (480, 800), (20 + index * 40, 50, 100)).save(
                source, format="PNG", optimize=False
            )
            destination = root / canonical_name
            shutil.copyfile(source, destination)
            digest = validation.sha256_file(destination)
            screenshots.append(
                {
                    "path": source_relative,
                    "session": "unit",
                    "label": label,
                    "content_sha256": f"{index + 1:064x}",
                    "frame_sha256": f"{index + 11:064x}",
                    "frame_sequence": index + 1,
                    "yoffset": 0,
                }
            )
            featured[canonical_name] = {
                "path": canonical_name,
                "source": source_relative,
                "label": label,
                "png_sha256": digest,
                "width": 480,
                "height": 800,
            }
        checks = [
            {"id": check, "status": "PASS"}
            for check in sorted(validation.REQUIRED_CHECKS)
        ]
        preflight = next(
            item for item in checks if item["id"] == "preflight.exact-candidate"
        )
        preflight.update(
            {
                "candidate_rootfs_sha256": self.digest,
                "candidate_rootfs_bytes": 1234,
                "execution_tree": "independently extracted from candidate_rootfs_image",
            }
        )
        return {
            "schema_version": 1,
            "status": "PASS",
            "candidate_rootfs_sha256": self.digest,
            "tool": "tools/cfw_validate.py",
            "qemu_scope": validation.QEMU_SCOPE,
            "hardware_tested": False,
            "run_id": "unit-run",
            "checks": checks,
            "screenshots": screenshots,
            "featured_screenshots": featured,
        }

    def test_accepts_pass_manifest_bound_to_exact_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.write(root, self.complete_manifest(root))
            document = validation.verify_manifest(path, self.digest)
            self.assertEqual("PASS", document["status"])

    def test_rejects_missing_malformed_failed_and_stale_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.json"
            with self.assertRaisesRegex(validation.ValidationError, "missing"):
                validation.verify_manifest(missing, self.digest)

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "invalid"):
                validation.verify_manifest(malformed, self.digest)

            cases = (
                ({"schema_version": True, "status": "PASS", "candidate_rootfs_sha256": self.digest}, "schema_version"),
                ({"schema_version": 2, "status": "PASS", "candidate_rootfs_sha256": self.digest}, "schema_version"),
                ({"schema_version": 1, "status": "pass", "candidate_rootfs_sha256": self.digest}, "not PASS"),
                ({"schema_version": 1, "status": "PASS", "candidate_rootfs_sha256": "A" * 64}, "lowercase"),
                ({"schema_version": 1, "status": "PASS", "candidate_rootfs_sha256": "b" * 64}, "different"),
            )
            for index, (document, message) in enumerate(cases):
                with self.subTest(index=index):
                    path = root / f"case-{index}.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(validation.ValidationError, message):
                        validation.verify_manifest(path, self.digest)

    def test_rejects_incomplete_duplicate_failed_or_unexpected_check_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)
            mutations = []

            missing = copy.deepcopy(baseline)
            missing["checks"] = missing["checks"][:-1]
            mutations.append((missing, "check set mismatch"))

            duplicate = copy.deepcopy(baseline)
            duplicate["checks"].append(copy.deepcopy(duplicate["checks"][0]))
            mutations.append((duplicate, "duplicate"))

            failed = copy.deepcopy(baseline)
            failed["checks"][0]["status"] = "FAIL"
            mutations.append((failed, "not PASS"))

            unexpected = copy.deepcopy(baseline)
            unexpected["checks"].append({"id": "unreviewed.check", "status": "PASS"})
            mutations.append((unexpected, "check set mismatch"))

            for index, (document, message) in enumerate(mutations):
                with self.subTest(index=index):
                    path = root / f"checks-{index}.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(validation.ValidationError, message):
                        validation.verify_manifest(path, self.digest)

    def test_rejects_missing_tampered_or_misidentified_framebuffer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)

            missing = copy.deepcopy(baseline)
            missing["featured_screenshots"].pop("cfw-main.png")
            path = root / "missing-featured.json"
            path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "featured"):
                validation.verify_manifest(path, self.digest)

            wrong_label = copy.deepcopy(baseline)
            wrong_label["featured_screenshots"]["cfw-main.png"]["label"] = "wrong"
            path = root / "wrong-label.json"
            path.write_text(json.dumps(wrong_label), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "identity"):
                validation.verify_manifest(path, self.digest)

            canonical = root / "default-compact-launcher.png"
            canonical.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
            path = root / "tampered-png.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "framebuffer PNG"):
                validation.verify_manifest(path, self.digest)

    def test_rejects_wrong_tool_scope_or_hardware_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)
            for index, (field, value, message) in enumerate(
                (
                    ("tool", "another-tool", "tool identity"),
                    ("qemu_scope", "full board", "scope"),
                    ("hardware_tested", True, "hardware_tested"),
                )
            ):
                document = copy.deepcopy(baseline)
                document[field] = value
                path = root / f"identity-{index}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(validation.ValidationError, message):
                    validation.verify_manifest(path, self.digest)

    def test_rejects_invalid_expected_digest_before_reading_manifest(self) -> None:
        with self.assertRaisesRegex(validation.ValidationError, "expected"):
            validation.verify_manifest(Path("does-not-exist"), "A" * 64)


if __name__ == "__main__":
    unittest.main()
