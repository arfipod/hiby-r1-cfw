from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import select
import struct
import subprocess
import sys
import tempfile
import threading
import time
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
            "ssh.runtime-auth",
            "cfw.wifi-route",
            "cfw.bluetooth-route",
            "launcher.persistence.77",
            "launcher.six-tile-routes",
            "launcher.maximum-rejected",
            "launcher.drag-no-activation",
            "launcher.ebook-swap",
            "launcher.alternate-six-persistence",
            "launcher.restart-position",
            "storage.sd-present",
            "storage.sd-absent",
        ):
            self.assertIn(check, required)
        self.assertEqual(24, len(required))
        self.assertEqual(
            {
                "default-compact-launcher.png": "default-launcher-71",
                "cfw-main.png": "cfw-main-sd-present",
                "six-tile-launcher.png": "six-tile-launcher-77",
            },
            validation.FEATURED_SCREENSHOTS,
        )

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
            validation.SIX_TILE_MASK,
            validation.ACTION_LAUNCHER_TOGGLE,
            validation.ROUTE_BACK,
        )
        state = validation.SemanticState.parse(data)
        self.assertEqual(9, state.sequence)
        self.assertEqual("77", state.as_dict()["launcher_mask"])

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
                "usr/bin/r1-ssh-control",
                "usr/bin/dropbearkey",
                "usr/lib/libr1-cfw-hook.so",
                "usr/sbin/dropbearmulti",
                "usr/resource/r1-cfw/launcher/theme1/71.view",
                "usr/resource/r1-cfw/launcher/theme1/77.view",
                "usr/resource/r1-cfw/launcher/theme1/7d.view",
                "usr/resource/r1-cfw/launcher/theme2/71.view",
                "usr/resource/r1-cfw/launcher/theme2/77.view",
                "usr/resource/r1-cfw/launcher/theme2/7d.view",
                "usr/resource/r1-cfw/launcher/midi-theme1/71.view",
                "usr/resource/r1-cfw/launcher/midi-theme1/77.view",
                "usr/resource/r1-cfw/launcher/midi-theme1/7d.view",
            ):
                path = candidate / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"candidate")
            (candidate / "usr/bin/r1-cfw-ui").write_bytes(
                b"Maximum 6 launcher tiles. Disable one first."
            )
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

            ui_binary = candidate / "usr/bin/r1-cfw-ui"
            ui_binary.write_bytes(b"candidate without the release notice")
            with self.assertRaisesRegex(validation.ValidationError, "maximum-six"):
                validation.preflight(args)
            ui_binary.write_bytes(b"Maximum 6 launcher tiles. Disable one first.")

            alternate = (
                candidate
                / "usr/resource/r1-cfw/launcher/midi-theme1/7d.view"
            )
            alternate.unlink()
            with self.assertRaisesRegex(validation.ValidationError, "missing required"):
                validation.preflight(args)
            alternate.write_bytes(b"candidate")

            forbidden = candidate / "usr/resource/r1-cfw/launcher/theme1/7f.view"
            forbidden.write_bytes(b"unsafe")
            with self.assertRaisesRegex(validation.ValidationError, "seven-tile"):
                validation.preflight(args)
            forbidden.unlink()

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
                    "usr/bin/r1-ssh-control",
                    "usr/bin/dropbearkey",
                    "usr/lib/libr1-cfw-hook.so",
                    "usr/sbin/dropbearmulti",
                    "usr/resource/r1-cfw/launcher/theme1/71.view",
                    "usr/resource/r1-cfw/launcher/theme1/77.view",
                    "usr/resource/r1-cfw/launcher/theme1/7d.view",
                    "usr/resource/r1-cfw/launcher/theme2/71.view",
                    "usr/resource/r1-cfw/launcher/theme2/77.view",
                    "usr/resource/r1-cfw/launcher/theme2/7d.view",
                    "usr/resource/r1-cfw/launcher/midi-theme1/71.view",
                    "usr/resource/r1-cfw/launcher/midi-theme1/77.view",
                    "usr/resource/r1-cfw/launcher/midi-theme1/7d.view",
                ):
                    path = destination / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"exact")
                (destination / "usr/bin/r1-cfw-ui").write_bytes(
                    b"Maximum 6 launcher tiles. Disable one first."
                )

            with mock.patch.object(validation.subprocess, "run", side_effect=materialize):
                validation.extract_exact_candidate(image, destination, log)
            self.assertTrue((destination / "usr/bin/r1-cfw-ui").is_file())

    def test_geometry_matches_the_sidecar_rows_and_safe_masks(self) -> None:
        self.assertEqual(116, validation.main_row_y(0))
        self.assertEqual(692, validation.main_row_y(8))
        self.assertEqual(188, validation.launcher_row_y(1))
        self.assertEqual((0x71, 0x77, 0x7D), (
            validation.DEFAULT_MASK,
            validation.SIX_TILE_MASK,
            validation.ALTERNATE_SIX_MASK,
        ))
        for mask in (0x71, 0x73, 0x77, 0x75, 0x7D):
            self.assertTrue(validation.safe_launcher_mask(mask))
        for mask in (0x20, 0x70, 0x7F):
            self.assertFalse(validation.safe_launcher_mask(mask))

    def test_route_restore_and_cfw_tap_require_fresh_stable_frames(self) -> None:
        launcher_hash = "a" * 64
        launcher = SimpleNamespace(content_sha256=launcher_hash)
        page = SimpleNamespace(content_sha256="b" * 64)
        route_events: list[object] = []

        class RouteSession:
            name = "unit"
            evidence = SimpleNamespace(
                screenshot=lambda name, _label, _page: (
                    route_events.append(("screenshot", name)),
                    "unit/route.png",
                )[1]
            )

            def tap_change(self, x: int, y: int) -> object:
                route_events.append(("tap-change", x, y))
                return page

            def tap(self, x: int, y: int) -> None:
                route_events.append(("tap", x, y))

            def wait_hash(self, expected: str) -> object:
                route_events.append(("wait-hash", expected))
                return launcher

            def wait_stable_frame(self) -> object:
                route_events.append("wait-stable")
                return launcher

        result = validation.stock_tile_roundtrip(
            RouteSession(), launcher_hash, "about", (360, 665)
        )
        self.assertEqual("unit/route.png", result)
        self.assertEqual(
            [
                ("tap-change", 360, 665),
                ("screenshot", "unit"),
                ("tap", 45, 92),
                ("wait-hash", launcher_hash),
                "wait-stable",
            ],
            route_events,
        )

        cfw_events: list[object] = []
        cfw_state = validation.SemanticState(
            1,
            validation.SCREEN_MAIN,
            validation.DEFAULT_MASK,
            0,
            validation.ROUTE_BACK,
        )

        class CfwSession:
            def wait_stable_frame(self) -> object:
                cfw_events.append("wait-stable")
                return launcher

            def clear_semantic(self) -> None:
                cfw_events.append("clear-semantic")

            def tap(self, x: int, y: int) -> None:
                cfw_events.append(("tap", x, y))

            def wait_semantic(self, predicate: object, *, label: str) -> object:
                cfw_events.append(("wait-semantic", label))
                self_test.assertTrue(predicate(cfw_state))
                return cfw_state

            def wait_change(self, previous: str) -> object:
                cfw_events.append(("wait-change", previous))
                return page

        self_test = self
        opened, state = validation.open_cfw(CfwSession(), (120, 665))
        self.assertIs(launcher, opened)
        self.assertIs(cfw_state, state)
        self.assertEqual(
            [
                "wait-stable",
                "clear-semantic",
                ("tap", 120, 665),
                ("wait-semantic", "open CFW"),
                ("wait-change", launcher_hash),
            ],
            cfw_events,
        )
        self.assertEqual(1, sum(event == ("tap", 120, 665) for event in cfw_events))

    def test_player_readiness_marker_is_required_before_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = validation.Evidence(root / "artifacts", "readiness")
            session = validation.QemuSession(
                name="unit",
                runner=root / "runner",
                rootfs=root / "rootfs",
                profile=root / "profile",
                runtime=root / "runtime",
                sd_present=True,
                evidence=evidence,
                boot_timeout=0.5,
                transition_timeout=0.1,
            )
            session.process = SimpleNamespace(poll=lambda: None)
            session.log_path = evidence.logs_dir / "unit.log"
            session.log_path.write_text("frame rendered early\n", encoding="utf-8")

            timer = threading.Timer(
                0.08,
                session.log_path.write_text,
                args=(f"{validation.PLAYER_READY_MARKER}\n",),
                kwargs={"encoding": "utf-8"},
            )
            timer.start()
            started = time.monotonic()
            session.wait_player_ready()
            timer.join()
            self.assertGreaterEqual(time.monotonic() - started, 0.05)

            session.boot_timeout = 0.05
            session.log_path.write_text("stable but not ready\n", encoding="utf-8")
            with self.assertRaisesRegex(validation.ValidationError, "readiness"):
                session.wait_player_ready()

    def test_rendered_qemu_frame_sequence_is_a_readiness_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = validation.Evidence(root / "artifacts", "render-readiness")
            session = validation.QemuSession(
                name="unit",
                runner=root / "runner",
                rootfs=root / "rootfs",
                profile=root / "profile",
                runtime=root / "runtime",
                sd_present=True,
                evidence=evidence,
                boot_timeout=0.08,
                transition_timeout=0.05,
            )
            session.process = SimpleNamespace(poll=lambda: None)
            session.log_path = evidence.logs_dir / "unit.log"
            session.log_path.write_text(
                "renderer active but stdout buffered\n", encoding="utf-8"
            )

            with mock.patch.object(
                validation.nav, "read_frame_state", return_value=(0, 3)
            ):
                session.wait_player_ready()
            self.assertEqual("framebuffer-sequence", session.readiness_signal)

            session.boot_timeout = 0.05
            with mock.patch.object(
                validation.nav, "read_frame_state", return_value=(0, 2)
            ):
                with self.assertRaisesRegex(
                    validation.ValidationError, "render-loop fallback"
                ):
                    session.wait_player_ready()

    def test_start_discards_stale_frame_state_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = validation.Evidence(root / "artifacts", "restart-state")
            session = validation.QemuSession(
                name="restart",
                runner=root / "runner",
                rootfs=root / "rootfs",
                profile=root / "profile",
                runtime=root / "runtime",
                sd_present=True,
                evidence=evidence,
                boot_timeout=0.08,
                transition_timeout=0.05,
            )
            session.runtime.mkdir(parents=True)
            session.frame_state.write_bytes(b"stale previous process telemetry")
            observed: list[bool] = []

            def fake_popen(*_args: object, **_kwargs: object) -> object:
                observed.append(session.frame_state.exists())
                return SimpleNamespace(poll=lambda: None)

            with mock.patch.object(
                validation.subprocess, "Popen", side_effect=fake_popen
            ), mock.patch.object(session, "wait_player_ready"), mock.patch.object(
                session, "wait_stable_frame", return_value="ready"
            ):
                self.assertEqual("ready", session.start())

            self.assertEqual([False], observed)
            session.process = None
            if session.log_stream is not None:
                session.log_stream.close()
                session.log_stream = None

    def test_fifo_proxy_is_a_bidirectional_private_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            to_server = root / "to-server.fifo"
            from_server = root / "from-server.fifo"
            ready = root / "proxy-ready"
            os.mkfifo(to_server, 0o600)
            os.mkfifo(from_server, 0o600)
            to_descriptor = os.open(to_server, os.O_RDWR)
            from_descriptor = os.open(from_server, os.O_RDWR)
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(validation.SSH_FIFO_PROXY),
                    str(to_server),
                    str(from_server),
                    "--ready",
                    str(ready),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                assert process.stdin is not None and process.stdout is not None
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not ready.is_file():
                    time.sleep(0.01)
                self.assertTrue(ready.is_file())
                process.stdin.write(b"client-to-dropbear")
                process.stdin.flush()
                readable, _, _ = select.select([to_descriptor], [], [], 3)
                self.assertEqual([to_descriptor], readable)
                self.assertEqual(b"client-to-dropbear", os.read(to_descriptor, 64))

                os.write(from_descriptor, b"dropbear-to-client")
                readable, _, _ = select.select([process.stdout], [], [], 3)
                self.assertEqual([process.stdout], readable)
                self.assertEqual(b"dropbear-to-client", process.stdout.read(18))
            finally:
                process.terminate()
                process.wait(timeout=3)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                os.close(to_descriptor)
                os.close(from_descriptor)

    def test_ssh_guest_uses_private_binfmt_for_target_child_exec(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("rootfs", "usr-data", "run"):
                (root / relative).mkdir()
            shim = root / "shim.so"
            shim.write_bytes(b"shim")
            command, environment = validation.ssh_guest_command(
                root / "rootfs",
                root / "usr-data",
                root / "run",
                shim,
                ("/bin/sh", "-c", "id -u"),
                preload_shim=False,
            )
            self.assertIn("--mount", command)
            self.assertIn(str(validation.SSH_BINFMT_HELPER), command)
            self.assertNotIn("/tmp/r1-controller-reexec", command)
            self.assertEqual(["/bin/sh", "-c", "id -u"], command[-3:])
            self.assertEqual("XBurstR2", environment["QEMU_CPU"])

    def test_ssh_inetd_adapter_builds_as_a_mips_shared_object(self) -> None:
        zig = REPO_ROOT / "work/host-tools/zig-x86_64-linux-0.16.0/zig"
        if not zig.is_file():
            self.skipTest("local Zig bootstrap is not present")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = validation.build_ssh_socket_shim(root, root / "build.log")
            header = output.read_bytes()[:20]
            self.assertEqual(b"\x7fELF", header[:4])
            self.assertEqual(1, header[4])  # ELFCLASS32.
            self.assertEqual(1, header[5])  # Little endian.
            self.assertEqual(8, int.from_bytes(header[18:20], "little"))  # EM_MIPS.

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
