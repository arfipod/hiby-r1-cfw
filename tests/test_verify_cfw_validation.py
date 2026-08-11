from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    component_hashes = {
        "busybox": "b" * 64,
        "controller": "c" * 64,
        "dropbear": "d" * 64,
    }

    def test_requires_exact_six_tile_release_contract(self) -> None:
        self.assertEqual(24, len(validation.REQUIRED_CHECKS))
        self.assertTrue(
            {
                "launcher.persistence.77",
                "launcher.six-tile-routes",
                "launcher.maximum-rejected",
                "launcher.drag-no-activation",
                "launcher.ebook-swap",
                "launcher.alternate-six-persistence",
                "launcher.restart-position",
            }.issubset(validation.REQUIRED_CHECKS)
        )
        self.assertTrue(
            {
                "launcher.persistence.7f",
                "launcher.all-enabled-routes",
                "launcher.scroll-no-activation",
                "launcher.tap-after-scroll",
            }.isdisjoint(validation.REQUIRED_CHECKS)
        )
        self.assertEqual(
            "six-tile-launcher-77",
            validation.FEATURED_SCREENSHOTS["six-tile-launcher.png"],
        )

    def write(self, root: Path, document: object) -> Path:
        path = root / "validation-manifest.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def verify(
        self, path: Path, expected_digest: str | None = None
    ) -> dict[str, object]:
        with mock.patch.object(
            validation,
            "squashfs_component_hashes",
            return_value=self.component_hashes,
        ):
            return validation.verify_manifest(
                path,
                expected_digest or self.digest,
                path.parent / "candidate-rootfs.squashfs",
            )

    def complete_manifest(self, root: Path) -> dict[str, object]:
        screenshots: list[dict[str, object]] = []
        featured: dict[str, dict[str, object]] = {}
        captures: dict[str, str] = {}
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
            captures[label] = source_relative
            featured[canonical_name] = {
                "path": canonical_name,
                "source": source_relative,
                "label": label,
                "png_sha256": digest,
                "width": 480,
                "height": 800,
            }

        def add_capture(label: str) -> str:
            index = len(screenshots)
            relative = f"unit-run/screens/{index:03d}_{label}.png"
            Image.new(
                "RGB",
                (480, 800),
                ((30 + index * 17) % 255, (70 + index * 13) % 255, 120),
            ).save(root / relative, format="PNG", optimize=False)
            screenshots.append(
                {
                    "path": relative,
                    "session": "unit",
                    "label": label,
                    "content_sha256": f"{index + 1:064x}",
                    "frame_sha256": f"{index + 101:064x}",
                    "frame_sequence": index + 1,
                    "yoffset": 0,
                }
            )
            captures[label] = relative
            return relative

        for label in (
            "launcher-settings-71",
            "launcher-settings-77",
            "launcher-maximum-six-rejected",
            "route-six-77-music",
            "route-six-77-stream",
            "route-six-77-wireless",
            "route-six-77-system",
            "route-six-77-about",
            "six-77-cfw-open",
            "six-77-cfw-restored",
            "launcher-settings-alternate-six-7d",
            "alternate-six-launcher-7d",
            "route-alternate-7d-ebook",
            "alternate-six-after-second-restart",
        ):
            add_capture(label)
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
                "execution_adapters": {
                    "root_backend_requested": "bwrap",
                    "sys_server_stub_skipped": True,
                    "sys_server_stub_skip_reason": (
                        "managed sandbox denies AF_UNIX socket creation"
                    ),
                    "mandatory_checks_relaxed": False,
                },
            }
        )

        def check(check_id: str) -> dict[str, object]:
            return next(item for item in checks if item["id"] == check_id)

        check("default.launcher.71").update(
            {"screenshot": captures["default-launcher-71"], "mask": "71"}
        )
        check("cfw.launcher-settings").update(
            {
                "masks": ["71", "73", "77"],
                "six_tile_mask": "77",
                "maximum_tiles": 6,
                "screenshots": [
                    captures["launcher-settings-71"],
                    captures["launcher-settings-77"],
                    captures["launcher-maximum-six-rejected"],
                ],
                "seventh_tile_rejected": True,
                "cfw_tile_locked": True,
                "drag_did_not_toggle": True,
            }
        )
        check("launcher.maximum-rejected").update(
            {
                "attempted_tile": "ebook",
                "mask": "77",
                "config": "launcher_mask=77\n",
                "action": 7,
                "expected_message": "Maximum 6 launcher tiles. Disable one first.",
                "framebuffer_changed": True,
                "screenshot": captures["launcher-maximum-six-rejected"],
            }
        )
        check("launcher.drag-no-activation").update(
            {
                "mask": "77",
                "config": "launcher_mask=77\n",
                "locked_cfw_action": 7,
                "drag_action": 8,
                "cfw_tile_locked": True,
                "drag_did_not_toggle": True,
            }
        )
        check("launcher.persistence.77").update(
            {
                "screenshot": captures["six-tile-launcher-77"],
                "mask": "77",
                "config": "launcher_mask=77\n",
                "tile_count": 6,
                "persisted_across_restart": True,
            }
        )
        visible_77 = ["music", "stream", "wireless", "system", "cfw", "about"]
        route_labels = {
            "music": "route-six-77-music",
            "stream": "route-six-77-stream",
            "wireless": "route-six-77-wireless",
            "system": "route-six-77-system",
            "cfw": "six-77-cfw-open",
            "about": "route-six-77-about",
        }
        check("launcher.six-tile-routes").update(
            {
                "mask": "77",
                "tiles": visible_77,
                "screenshots": {
                    tile: captures[label] for tile, label in route_labels.items()
                },
                "cfw_restored_screenshot": captures["six-77-cfw-restored"],
                "all_routes_restored": True,
            }
        )
        check("launcher.ebook-swap").update(
            {
                "transitions": ["77", "75", "7d"],
                "disabled_tile": "stream",
                "enabled_tile": "ebook",
                "final_mask": "7d",
                "config": "launcher_mask=7d\n",
                "screenshot": captures["launcher-settings-alternate-six-7d"],
                "applies_on_restart": True,
            }
        )
        check("launcher.alternate-six-persistence").update(
            {
                "mask": "7d",
                "config": "launcher_mask=7d\n",
                "tile_count": 6,
                "disabled_tile": "stream",
                "enabled_tile": "ebook",
                "launcher_screenshot": captures["alternate-six-launcher-7d"],
                "ebook_route_screenshot": captures["route-alternate-7d-ebook"],
                "ebook_route_restored": True,
                "persisted_across_restart": True,
            }
        )
        check("launcher.restart-position").update(
            {
                "screenshot": captures["alternate-six-after-second-restart"],
                "mask": "7d",
                "config": "launcher_mask=7d\n",
                "deterministic": True,
                "fixed_position": True,
            }
        )
        log_dir = root / "unit-run/logs"
        log_dir.mkdir(parents=True)
        controller_relative = "unit-run/logs/ssh-controller.log"
        controller_text = (
            "SSH is disabled\n"
            "disabled\n"
            "SSH is enabled; Dropbear will follow the wlan0 IPv4 address\n"
            "enabled\n"
            "SSH toggle is enabled\n"
            "Dropbear is stopped\n"
        )
        (root / controller_relative).write_text(controller_text, encoding="utf-8")
        auth_results: dict[str, dict[str, object]] = {}
        for trial, method, accepted, server_marker in (
            ("publickey", "publickey", True, "Pubkey auth succeeded for 'root'"),
            ("password", "password", True, "Password auth succeeded for 'root'"),
            ("unknown-key", "publickey", False, "Child connection"),
            ("wrong-password", "password", False, "Bad password attempt"),
        ):
            server_relative = f"unit-run/logs/ssh-{trial}-server.log"
            client_relative = f"unit-run/logs/ssh-{trial}-client.log"
            server_path = root / server_relative
            client_path = root / client_relative
            server_path.write_text(server_marker + "\n", encoding="utf-8")
            if accepted:
                proof = f"r1-cfw-{trial}-root-session"
                client_path.write_text(
                    f'Authenticated to test using "{method}"\n'
                    f"R1_UID=0\nR1_FS={proof}\nExit status 0\n",
                    encoding="utf-8",
                )
                result: dict[str, object] = {
                    "server_authenticated": True,
                    "client_authenticated": True,
                    "root_command_executed": True,
                    "root_uid": 0,
                    "filesystem_proof": proof,
                }
            else:
                client_path.write_text(
                    "root@test: Permission denied (publickey,password).\n",
                    encoding="utf-8",
                )
                result = {
                    "server_authenticated": False,
                    "client_authenticated": False,
                    "client_rejected": True,
                }
            result.update(
                {
                    "trial": trial,
                    "method": method,
                    "expected_authenticated": accepted,
                    "run_id": "unit-run",
                    "server_log": server_relative,
                    "server_log_sha256": validation.sha256_file(server_path),
                    "client_log": client_relative,
                    "client_log_sha256": validation.sha256_file(client_path),
                }
            )
            auth_results[trial] = result
        ssh_runtime = next(
            item for item in checks if item["id"] == "ssh.runtime-auth"
        )
        ssh_runtime.update(
            {
                "candidate_components": {
                    name: {
                        "path": validation.CANDIDATE_SSH_COMPONENTS[name],
                        "sha256": digest,
                    }
                    for name, digest in self.component_hashes.items()
                },
                "host_key_generated": True,
                "controller": {
                    "target_busybox_qemu": True,
                    "commands": [
                        "disable",
                        "is-enabled(disabled)",
                        "toggle(enabled)",
                        "is-enabled(enabled)",
                        "toggle(disabled)",
                        "is-enabled(disabled)",
                        "enable",
                        "is-enabled(enabled)",
                        "status(stopped)",
                        "disable",
                    ],
                    "enable_disable_persisted": True,
                    "direct_toggle": True,
                    "self_reexec_adapter": False,
                    "log": controller_relative,
                    "log_sha256": validation.sha256_file(root / controller_relative),
                    "run_id": "unit-run",
                },
                "publickey": auth_results["publickey"],
                "password": auth_results["password"],
                "unknown_key_rejection": auth_results["unknown-key"],
                "wrong_password_rejection": auth_results["wrong-password"],
                "hardware_operation_claimed": False,
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
            document = self.verify(path)
            self.assertEqual("PASS", document["status"])

    def test_rejects_missing_malformed_failed_and_stale_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing.json"
            with self.assertRaisesRegex(validation.ValidationError, "missing"):
                self.verify(missing)

            malformed = root / "malformed.json"
            malformed.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "invalid"):
                self.verify(malformed)

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
                        self.verify(path)

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
                        self.verify(path)

    def test_rejects_missing_tampered_or_misidentified_framebuffer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)

            missing = copy.deepcopy(baseline)
            missing["featured_screenshots"].pop("cfw-main.png")
            path = root / "missing-featured.json"
            path.write_text(json.dumps(missing), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "featured"):
                self.verify(path)

            wrong_label = copy.deepcopy(baseline)
            wrong_label["featured_screenshots"]["cfw-main.png"]["label"] = "wrong"
            path = root / "wrong-label.json"
            path.write_text(json.dumps(wrong_label), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "identity"):
                self.verify(path)

            canonical = root / "default-compact-launcher.png"
            canonical.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
            path = root / "tampered-png.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "framebuffer PNG"):
                self.verify(path)

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
                    self.verify(path)

            relaxed = copy.deepcopy(baseline)
            preflight = next(
                item
                for item in relaxed["checks"]
                if item["id"] == "preflight.exact-candidate"
            )
            preflight["execution_adapters"]["mandatory_checks_relaxed"] = True
            path = root / "relaxed-adapter.json"
            path.write_text(json.dumps(relaxed), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "adapter"):
                self.verify(path)

    def test_rejects_weakened_six_tile_launcher_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)

            def checked(document: dict[str, object], check_id: str) -> dict[str, object]:
                return next(
                    item for item in document["checks"] if item["id"] == check_id
                )

            mutations: list[tuple[dict[str, object], str]] = []
            wrong_message = copy.deepcopy(baseline)
            checked(wrong_message, "launcher.maximum-rejected")[
                "expected_message"
            ] = "Maximum"
            mutations.append((wrong_message, "maximum-six"))

            wrong_capture = copy.deepcopy(baseline)
            checked(wrong_capture, "launcher.maximum-rejected")["screenshot"] = checked(
                wrong_capture, "cfw.launcher-settings"
            )["screenshots"][1]
            mutations.append((wrong_capture, "wrong framebuffer"))

            incomplete_routes = copy.deepcopy(baseline)
            checked(incomplete_routes, "launcher.six-tile-routes")["screenshots"].pop(
                "about"
            )
            mutations.append((incomplete_routes, "route evidence"))

            unpersisted_77 = copy.deepcopy(baseline)
            checked(unpersisted_77, "launcher.persistence.77")[
                "persisted_across_restart"
            ] = False
            mutations.append((unpersisted_77, "persistence evidence"))

            missing_ebook = copy.deepcopy(baseline)
            checked(missing_ebook, "launcher.alternate-six-persistence")[
                "ebook_route_restored"
            ] = False
            mutations.append((missing_ebook, "alternate six-tile"))

            stale_restart_config = copy.deepcopy(baseline)
            checked(stale_restart_config, "launcher.restart-position")[
                "config"
            ] = "launcher_mask=77\n"
            mutations.append((stale_restart_config, "restart evidence"))

            for index, (document, message) in enumerate(mutations):
                with self.subTest(index=index):
                    path = root / f"six-tile-{index}.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaisesRegex(validation.ValidationError, message):
                        self.verify(path)

    def test_rejects_unproved_or_tampered_exact_candidate_ssh_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = self.complete_manifest(root)
            runtime = next(
                item for item in baseline["checks"] if item["id"] == "ssh.runtime-auth"
            )

            unproved = copy.deepcopy(baseline)
            unproved_runtime = next(
                item for item in unproved["checks"] if item["id"] == "ssh.runtime-auth"
            )
            unproved_runtime["password"]["client_authenticated"] = False
            path = root / "unproved-ssh.json"
            path.write_text(json.dumps(unproved), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "password"):
                self.verify(path)

            password_server = root / runtime["password"]["server_log"]
            password_server.write_text("authentication missing\n", encoding="utf-8")
            path = root / "tampered-ssh-log.json"
            path.write_text(json.dumps(baseline), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "log digest"):
                self.verify(path)
            password_server.write_text(
                "Password auth succeeded for 'root'\n", encoding="utf-8"
            )

            wrong_component = copy.deepcopy(baseline)
            wrong_runtime = next(
                item
                for item in wrong_component["checks"]
                if item["id"] == "ssh.runtime-auth"
            )
            wrong_runtime["candidate_components"]["dropbear"]["sha256"] = "0" * 64
            path = root / "wrong-component.json"
            path.write_text(json.dumps(wrong_component), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "exact candidate"):
                self.verify(path)

            false_rejection = copy.deepcopy(baseline)
            false_runtime = next(
                item
                for item in false_rejection["checks"]
                if item["id"] == "ssh.runtime-auth"
            )
            false_runtime["unknown_key_rejection"]["client_rejected"] = False
            path = root / "false-rejection.json"
            path.write_text(json.dumps(false_rejection), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "unknown-key"):
                self.verify(path)

            stale_toggle_adapter = copy.deepcopy(baseline)
            stale_toggle_runtime = next(
                item
                for item in stale_toggle_adapter["checks"]
                if item["id"] == "ssh.runtime-auth"
            )
            stale_toggle_runtime["controller"]["direct_toggle"] = False
            stale_toggle_runtime["controller"]["self_reexec_adapter"] = True
            path = root / "stale-toggle-adapter.json"
            path.write_text(json.dumps(stale_toggle_adapter), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "target-runtime"):
                self.verify(path)

            stale_run = copy.deepcopy(baseline)
            stale_runtime = next(
                item
                for item in stale_run["checks"]
                if item["id"] == "ssh.runtime-auth"
            )
            stale_runtime["publickey"]["server_log"] = (
                "old-run/logs/ssh-publickey-server.log"
            )
            path = root / "stale-run-log.json"
            path.write_text(json.dumps(stale_run), encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "current run"):
                self.verify(path)

    def test_rejects_invalid_expected_digest_before_reading_manifest(self) -> None:
        with self.assertRaisesRegex(validation.ValidationError, "expected"):
            validation.verify_manifest(
                Path("does-not-exist"), "A" * 64, Path("missing.squashfs")
            )

    def test_component_hashes_are_recomputed_from_the_exact_squashfs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "candidate.squashfs"
            image.write_bytes(b"exact image")
            unsquashfs = root / "work/host-tools/usr/bin/unsquashfs"
            unsquashfs.parent.mkdir(parents=True)
            unsquashfs.write_bytes(b"tool")
            payloads = {
                member: f"exact:{member}".encode()
                for member in validation.CANDIDATE_SSH_COMPONENTS.values()
            }

            def fake_run(command: list[str], **_kwargs: object) -> object:
                member = command[-1]
                return type(
                    "Result",
                    (),
                    {"returncode": 0, "stdout": payloads[member], "stderr": b""},
                )()

            with (
                mock.patch.object(validation, "REPO_ROOT", root),
                mock.patch.object(validation.subprocess, "run", side_effect=fake_run),
            ):
                hashes = validation.squashfs_component_hashes(
                    image, validation.sha256_file(image)
                )
            self.assertEqual(
                {
                    name: validation.hashlib.sha256(payloads[member]).hexdigest()
                    for name, member in validation.CANDIDATE_SSH_COMPONENTS.items()
                },
                hashes,
            )
            calls = 0

            def replace_during_read(command: list[str], **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                result = fake_run(command, **kwargs)
                if calls == len(validation.CANDIDATE_SSH_COMPONENTS):
                    image.write_bytes(b"replaced during verification")
                return result

            with (
                mock.patch.object(validation, "REPO_ROOT", root),
                mock.patch.object(
                    validation.subprocess, "run", side_effect=replace_during_read
                ),
                self.assertRaisesRegex(validation.ValidationError, "changed"),
            ):
                validation.squashfs_component_hashes(
                    image, validation.hashlib.sha256(b"exact image").hexdigest()
                )
            with self.assertRaisesRegex(validation.ValidationError, "expected digest"):
                validation.squashfs_component_hashes(image, "0" * 64)


if __name__ == "__main__":
    unittest.main()
