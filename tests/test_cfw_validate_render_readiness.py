from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools/cfw_validate.py"
SPEC = importlib.util.spec_from_file_location("cfw_validate_render_readiness", MODULE_PATH)
assert SPEC and SPEC.loader
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


class RenderReadinessTests(unittest.TestCase):
    def make_session(self, root: Path) -> object:
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
        session.log_path.write_text("renderer active but stdio buffered\n", encoding="utf-8")
        return session

    def test_three_fbdev_presents_are_an_accepted_readiness_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = self.make_session(Path(temporary))
            with mock.patch.object(validation.nav, "read_frame_state", return_value=(0, 3)):
                session.wait_player_ready()

    def test_incomplete_render_sequence_does_not_claim_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = self.make_session(Path(temporary))
            with mock.patch.object(validation.nav, "read_frame_state", return_value=(0, 2)):
                with self.assertRaisesRegex(validation.ValidationError, "readiness"):
                    session.wait_player_ready()

    def test_start_discards_previous_session_frame_state_before_spawning(self) -> None:
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
            observed = []

            def fake_popen(*_args, **_kwargs):
                observed.append(session.frame_state.exists())
                return SimpleNamespace(poll=lambda: None)

            with mock.patch.object(validation.subprocess, "Popen", side_effect=fake_popen), \
                mock.patch.object(session, "wait_player_ready"), \
                mock.patch.object(session, "wait_stable_frame", return_value="ready"):
                self.assertEqual("ready", session.start())

            self.assertEqual([False], observed)
            session.process = None
            if session.log_stream is not None:
                session.log_stream.close()
                session.log_stream = None


if __name__ == "__main__":
    unittest.main()
