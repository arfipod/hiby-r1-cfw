from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "r1_qemu_navigate", REPO_ROOT / "tools/r1-qemu-ui/navigate.py"
)
assert MODULE_SPEC and MODULE_SPEC.loader
navigate = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = navigate
MODULE_SPEC.loader.exec_module(navigate)


class R1QemuNavigateTests(unittest.TestCase):
    def test_smoke_plan_is_curated_and_excludes_destructive_actions(self) -> None:
        plan = navigate.SMOKE_PLAN
        navigate.validate_plan(plan)

        required_branches = {
            "launcher.music.open",
            "launcher.stream.open",
            "launcher.wireless.open",
            "launcher.ebook.open",
            "launcher.system.open",
            "launcher.about.open",
        }
        step_ids = {step.id for step in plan.steps}
        self.assertIn("boot.wait_for_launcher", step_ids)
        self.assertTrue(required_branches.issubset(step_ids))

        exclusions = set(plan.safety_exclusions)
        self.assertIn("restore_factory_settings", exclusions)
        self.assertIn("firmware_or_ota_update", exclusions)
        self.assertIn("music_database_update", exclusions)
        self.assertIn("delete_file_or_folder", exclusions)
        self.assertIn("format_sd_card", exclusions)

        for step in plan.steps:
            self.assertEqual("read-only", step.risk)
            executable = " ".join((step.id, step.label, step.expected_state)).lower()
            self.assertFalse(
                any(token in executable for token in navigate.FORBIDDEN_ACTION_TOKENS),
                step.id,
            )
            if step.point:
                self.assertGreaterEqual(step.point.x, 0)
                self.assertLess(step.point.x, navigate.WIDTH)
                self.assertGreaterEqual(step.point.y, 0)
                self.assertLess(step.point.y, navigate.HEIGHT)

    def test_stock_coordinates_and_isolated_audio_fixture_route(self) -> None:
        expected_launcher = {
            "launcher.music": (120, 173),
            "launcher.stream": (360, 173),
            "launcher.wireless": (120, 419),
            "launcher.ebook": (360, 419),
            "launcher.system": (120, 676),
            "launcher.about": (360, 676),
        }
        for name, expected in expected_launcher.items():
            point = navigate.COORDINATES[name]
            self.assertEqual(expected, (point.x, point.y))

        self.assertEqual((45, 92), tuple(navigate.COORDINATES["standard.back"].as_dict().values()))
        self.assertEqual((50, 40), tuple(navigate.COORDINATES["now_playing.back"].as_dict().values()))
        self.assertEqual((355, 229), tuple(navigate.COORDINATES["music.explorer"].as_dict().values()))
        self.assertEqual((240, 197), tuple(navigate.COORDINATES["list.row1"].as_dict().values()))
        self.assertEqual(
            (119, 755),
            tuple(navigate.SAFE_CANCEL_COORDINATES["standard_confirmation.cancel"].as_dict().values()),
        )
        self.assertEqual(
            (360, 755),
            tuple(navigate.SAFE_CANCEL_COORDINATES["output_select.cancel"].as_dict().values()),
        )

        system_y = [
            navigate.COORDINATES[name].y
            for name in (
                "system.language",
                "system.backlight",
                "system.theme_color",
                "system.font_size",
                "system.ui_theme",
            )
        ]
        self.assertEqual([197, 323, 449, 575, 701], system_y)
        self.assertEqual(
            "/usr/data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3",
            navigate.SMOKE_PLAN.fixture,
        )
        fixture_steps = [
            step
            for step in navigate.SMOKE_PLAN.steps
            if step.id.startswith("music.fixture.enter_") or step.id == "music.fixture.open_track"
        ]
        self.assertEqual(4, len(fixture_steps))
        self.assertTrue(all(step.point == navigate.COORDINATES["list.row1"] for step in fixture_steps))

    def test_dry_run_writes_honest_manifest_events_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifacts" / "ui"
            with contextlib.redirect_stdout(io.StringIO()):
                result = navigate.main(
                    [
                        "smoke",
                        "--dry-run",
                        "--artifacts-root",
                        str(root),
                        "--run-id",
                        "unit-dry-run",
                    ]
                )
            self.assertEqual(0, result)

            run = root / "unit-dry-run"
            manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
            coverage = json.loads((run / "coverage.json").read_text(encoding="utf-8"))
            events = [
                json.loads(line)
                for line in (run / "ui-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertTrue((run / "frames").is_dir())
            self.assertEqual([], list((run / "frames").iterdir()))
            self.assertEqual("dry-run", manifest["status"])
            self.assertTrue(manifest["dry_run"])
            self.assertFalse(manifest["full_coverage_claim"])
            self.assertEqual([], manifest["frames"])
            self.assertEqual(len(navigate.SMOKE_PLAN.steps), len(manifest["steps"]))
            self.assertTrue(all(step["status"] == "planned" for step in manifest["steps"]))

            input_steps = [
                step for step in navigate.SMOKE_PLAN.steps if step.operation in {"tap", "drag"}
            ]
            self.assertEqual(len(input_steps), len(events))
            self.assertTrue(all(event["dry_run"] for event in events))
            self.assertTrue(all(event["result"] == "planned-only" for event in events))

            self.assertFalse(coverage["full_coverage"])
            self.assertEqual([], coverage["attempted_states"])
            self.assertEqual([], coverage["verified_states"])
            blocked = {entry["state"] for entry in coverage["expected_blocked"]}
            self.assertIn("wireless.bluetooth_peer", blocked)
            self.assertIn("audio.physical_dac_output", blocked)
            self.assertIn("usb.device_or_otg_hardware", blocked)
            self.assertEqual(
                "capture-and-stop; never guess a button",
                coverage["dialog_policy"]["unexpected_dialog"],
            )

    def test_runtime_lock_rejects_a_second_navigator(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "runtime" / ".navigate.lock"
            with navigate.runtime_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "another navigator"):
                    with navigate.runtime_lock(lock_path):
                        self.fail("a second runtime lock was unexpectedly acquired")

    def test_coherent_capture_uses_active_double_buffer_page(self) -> None:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            framebuffer = root / "framebuffer.raw"
            state = root / "frame-state.bin"

            red_line = bytearray(navigate.bridge.FRAME_STRIDE)
            blue_line = bytearray(navigate.bridge.FRAME_STRIDE)
            red_line[: navigate.bridge.ACTIVE_LINE_BYTES] = struct.pack("<H", 0xF800) * navigate.WIDTH
            blue_line[: navigate.bridge.ACTIVE_LINE_BYTES] = struct.pack("<H", 0x001F) * navigate.WIDTH
            framebuffer.write_bytes(
                bytes(red_line) * navigate.HEIGHT + bytes(blue_line) * navigate.HEIGHT
            )
            state.write_bytes(
                navigate.bridge.FRAME_STATE.pack(
                    navigate.bridge.FRAME_STATE_MAGIC, 1, navigate.HEIGHT, 17
                )
            )

            snapshot = navigate.capture_coherent_frame(framebuffer, state)
            self.assertEqual(navigate.HEIGHT, snapshot.yoffset)
            self.assertEqual(17, snapshot.sequence)
            self.assertEqual((0, 0, 255), snapshot.image.getpixel((0, 0)))
            self.assertEqual(64, len(snapshot.sha256))
            self.assertEqual(64, len(snapshot.content_sha256))

            state.write_bytes(navigate.bridge.FRAME_STATE.pack(0, 1, 0, 18))
            with self.assertRaisesRegex(RuntimeError, "invalid frame state header"):
                navigate.capture_coherent_frame(framebuffer, state)

    def test_timed_tap_and_drag_emit_ordered_touch_phases(self) -> None:
        events: list[tuple[int, int, str]] = []

        def inject(_path: Path, x: int, y: int, phase: str) -> int:
            events.append((x, y, phase))
            return 1

        written = navigate.timed_tap(
            Path("unused"),
            navigate.Point(10, 20),
            interval_ms=0,
            move_frames=2,
            injector=inject,
        )
        self.assertEqual(4, written)
        self.assertEqual(["down", "move", "move", "up"], [event[2] for event in events])

        events.clear()
        written = navigate.timed_drag(
            Path("unused"),
            navigate.Point(10, 20),
            navigate.Point(40, 50),
            duration_ms=0,
            move_frames=3,
            injector=inject,
        )
        self.assertEqual(5, written)
        self.assertEqual("down", events[0][2])
        self.assertEqual((40, 50, "move"), events[-2])
        self.assertEqual((40, 50, "up"), events[-1])


if __name__ == "__main__":
    unittest.main()
