#!/usr/bin/env python3
"""Run the fail-closed qemu-user validation matrix for R1 CFW v0.1.

This tool launches only disposable qemu-user/PRoot sessions.  It never writes
to an R1, never flashes firmware, and does not claim X1600 board emulation.
The release manifest is changed to ``PASS`` only after every mandatory check
has completed against the exact candidate SquashFS digest.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNNER = REPO_ROOT / "tools/run-r1-ui-qemu.sh"
DEFAULT_ARTIFACTS = REPO_ROOT / "artifacts/ui/cfw-v0.1"
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "work/cfw-qemu-validation"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAMEBUFFER_WIDTH = 480
FRAMEBUFFER_HEIGHT = 800
QEMU_SCOPE = "qemu-user application harness; not X1600 board emulation"

CFW_STATE = struct.Struct("<7I")
CFW_STATE_MAGIC = 0x52314346
CFW_STATE_VERSION = 1

SCREEN_MAIN = 1
SCREEN_LAUNCHER = 2
SCREEN_INTERNAL = 3
SCREEN_SD = 4
SCREEN_MEMORY = 5
SCREEN_SYSTEM = 6
SCREEN_ABOUT = 7

ACTION_BACK = 1
ACTION_SSH_TOGGLE = 2
ACTION_WIFI_ROUTE = 3
ACTION_BLUETOOTH_ROUTE = 4
ACTION_OPEN_PAGE = 5
ACTION_LAUNCHER_TOGGLE = 6
ACTION_LAUNCHER_REJECTED = 7
ACTION_DRAG_IGNORED = 8
ROUTE_BACK = 0
ROUTE_WIFI = 21
ROUTE_BLUETOOTH = 22

DEFAULT_MASK = 0x71
SIX_TILE_MASK = 0x77
ALL_MASK = 0x7F

CFW_MAIN_ROWS = {
    "ssh": 0,
    "wifi": 1,
    "bluetooth": 2,
    "launcher": 3,
    "internal": 4,
    "sd": 5,
    "memory": 6,
    "system": 7,
    "about": 8,
}

REQUIRED_CHECKS = frozenset(
    {
        "preflight.exact-candidate",
        "stock.launcher",
        "stock.mp3-fixture",
        "default.launcher.71",
        "default.stock-routes",
        "cfw.lifecycle",
        "cfw.framebuffer-restore",
        "cfw.crash-recovery",
        "cfw.ssh",
        "cfw.information-pages",
        "cfw.launcher-settings",
        "cfw.wifi-route",
        "cfw.bluetooth-route",
        "launcher.persistence.7f",
        "launcher.all-enabled-routes",
        "launcher.scroll-up",
        "launcher.scroll-no-activation",
        "launcher.tap-after-scroll",
        "launcher.scroll-down",
        "launcher.restart-position",
        "storage.sd-present",
        "storage.sd-absent",
        "storage.sd-state-distinct",
    }
)

FEATURED_SCREENSHOTS = {
    "default-compact-launcher.png": "default-launcher-71",
    "cfw-main.png": "cfw-main-sd-present",
    "all-tiles-scrolled.png": "all-enabled-scrolled",
}

PLAN = (
    "Preflight exact candidate SquashFS and extracted-tree integration",
    "Boot and capture the stock six-tile launcher",
    "Navigate the stock Music/Files microSD MP3 fixture",
    "Boot the candidate with default launcher mask 0x71",
    "Exercise Music, System, About, and CFW default hitboxes",
    "Open/back the CFW sidecar three times and prove framebuffer restoration",
    "Force one sidecar SIGABRT and prove player, framebuffer, and touch recovery",
    "Toggle SSH off/on, reopen CFW, then toggle off",
    "Capture Internal, microSD, Memory, System information, and About pages",
    "Change launcher masks 0x71 -> 0x73 -> 0x77 -> 0x7f",
    "Route CFW Wi-Fi and Bluetooth rows to the stock Wireless hub",
    "Restart with persisted mask 0x7f and exercise every enabled tile",
    "Swipe up/down, reject activation during drag, and tap after scrolling",
    "Restart again and prove deterministic initial scroll position",
    "Boot a separate SD-absent session and compare the microSD page",
    "Write PASS only if every mandatory check succeeded",
)


class ValidationError(RuntimeError):
    """A prerequisite or runtime observation did not match the safe plan."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def framebuffer_png_dimensions(path: Path) -> tuple[int, int]:
    """Validate the fixed framebuffer PNG header and return its dimensions."""

    try:
        header = path.read_bytes()[:33]
    except OSError as error:
        raise ValidationError(f"cannot read framebuffer PNG: {path}: {error}") from error
    if (
        len(header) < 33
        or header[:8] != PNG_SIGNATURE
        or struct.unpack(">I", header[8:12])[0] != 13
        or header[12:16] != b"IHDR"
    ):
        raise ValidationError(f"invalid framebuffer PNG header: {path}")
    width, height = struct.unpack(">II", header[16:24])
    if (width, height) != (FRAMEBUFFER_WIDTH, FRAMEBUFFER_HEIGHT):
        raise ValidationError(
            f"framebuffer PNG is {width}x{height}; expected "
            f"{FRAMEBUFFER_WIDTH}x{FRAMEBUFFER_HEIGHT}: {path}"
        )
    if header[24:29] != bytes((8, 2, 0, 0, 0)):
        raise ValidationError(
            f"framebuffer PNG must be non-interlaced 8-bit RGB: {path}"
        )
    return width, height


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_navigator() -> ModuleType:
    path = REPO_ROOT / "tools/r1-qemu-ui/navigate.py"
    name = "_r1_cfw_validation_navigator"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValidationError(f"cannot load QEMU navigation helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


nav = load_navigator()


@dataclasses.dataclass(frozen=True)
class SemanticState:
    sequence: int
    screen: int
    launcher_mask: int
    action: int
    route: int

    @classmethod
    def parse(cls, data: bytes) -> "SemanticState":
        if len(data) != CFW_STATE.size:
            raise ValidationError(
                f"CFW semantic state is {len(data)} bytes; expected {CFW_STATE.size}"
            )
        magic, version, sequence, screen, mask, action, route = CFW_STATE.unpack(data)
        if magic != CFW_STATE_MAGIC or version != CFW_STATE_VERSION:
            raise ValidationError(
                "invalid CFW semantic state header: "
                f"magic=0x{magic:08x}, version={version}"
            )
        return cls(sequence, screen, mask, action, route)

    def as_dict(self) -> dict[str, int | str]:
        return {
            "sequence": self.sequence,
            "screen": self.screen,
            "launcher_mask": f"{self.launcher_mask:02x}",
            "action": self.action,
            "route": self.route,
        }


class Evidence:
    def __init__(self, artifacts: Path, run_id: str) -> None:
        if not SAFE_NAME_RE.fullmatch(run_id):
            raise ValidationError(f"unsafe run id: {run_id!r}")
        self.artifacts = artifacts.resolve()
        self.manifest_path = self.artifacts / "validation-manifest.json"
        self.run_dir = self.artifacts / run_id
        if self.run_dir.exists():
            raise ValidationError(f"evidence run already exists: {self.run_dir}")
        self.screens_dir = self.run_dir / "screens"
        self.logs_dir = self.run_dir / "logs"
        self.screens_dir.mkdir(parents=True)
        self.logs_dir.mkdir(parents=True)
        self.started_at = utc_now()
        self.checks: dict[str, dict[str, Any]] = {}
        self.screenshots: list[dict[str, Any]] = []
        self.promotions: dict[str, Path] = {}
        self.counter = 0
        atomic_json(
            self.manifest_path,
            {
                "schema_version": 1,
                "status": "RUNNING",
                "tool": "tools/cfw_validate.py",
                "run_id": run_id,
                "started_at": self.started_at,
            },
        )

    def record(self, check: str, **details: Any) -> None:
        if check not in REQUIRED_CHECKS:
            raise ValidationError(f"unknown evidence check: {check}")
        if check in self.checks:
            raise ValidationError(f"duplicate evidence check: {check}")
        if "id" in details or "status" in details:
            raise ValidationError("evidence details cannot override id or status")
        self.checks[check] = {"id": check, "status": "PASS", **details}

    def screenshot(self, session: str, label: str, snapshot: Any) -> str:
        if not SAFE_NAME_RE.fullmatch(session):
            raise ValidationError(f"unsafe screenshot session: {session!r}")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "frame"
        relative = Path(self.run_dir.name) / "screens" / (
            f"{self.counter:03d}_{session}_{safe}.png"
        )
        target = self.artifacts / relative
        snapshot.image.save(target, format="PNG", optimize=False)
        record = {
            "path": relative.as_posix(),
            "session": session,
            "label": label,
            "content_sha256": snapshot.content_sha256,
            "frame_sha256": snapshot.sha256,
            "frame_sequence": snapshot.sequence,
            "yoffset": snapshot.yoffset,
        }
        self.screenshots.append(record)
        self.counter += 1
        return relative.as_posix()

    def fail(self, error: BaseException) -> None:
        atomic_json(
            self.manifest_path,
            {
                "schema_version": 1,
                "status": "FAIL",
                "tool": "tools/cfw_validate.py",
                "run_id": self.run_dir.name,
                "started_at": self.started_at,
                "finished_at": utc_now(),
                "error": str(error),
                "completed_checks": sorted(self.checks),
            },
        )

    def promote(self, canonical_name: str, screenshot_path: str) -> None:
        if canonical_name not in FEATURED_SCREENSHOTS:
            raise ValidationError(f"unsupported canonical screenshot: {canonical_name}")
        source = (self.artifacts / screenshot_path).resolve()
        if self.artifacts not in source.parents or not source.is_file():
            raise ValidationError(f"canonical screenshot source is invalid: {source}")
        expected_label = FEATURED_SCREENSHOTS[canonical_name]
        source_record = next(
            (item for item in self.screenshots if item["path"] == screenshot_path),
            None,
        )
        if source_record is None or source_record["label"] != expected_label:
            raise ValidationError(
                f"canonical screenshot {canonical_name} must come from "
                f"the {expected_label!r} framebuffer capture"
            )
        framebuffer_png_dimensions(source)
        self.promotions[canonical_name] = source

    def finish(self, candidate_digest: str, inputs: dict[str, str]) -> None:
        missing = sorted(REQUIRED_CHECKS.difference(self.checks))
        if missing:
            raise ValidationError(f"mandatory checks were not completed: {missing}")
        unexpected = sorted(set(self.checks).difference(REQUIRED_CHECKS))
        if unexpected:
            raise ValidationError(f"unexpected evidence checks were recorded: {unexpected}")
        if not SHA256_RE.fullmatch(candidate_digest):
            raise ValidationError("candidate digest is not canonical lowercase SHA-256")
        required_promotions = set(FEATURED_SCREENSHOTS)
        missing_promotions = sorted(required_promotions.difference(self.promotions))
        if missing_promotions:
            raise ValidationError(
                f"canonical screenshots were not captured: {missing_promotions}"
            )
        featured: dict[str, dict[str, Any]] = {}
        for name in sorted(required_promotions):
            source = self.promotions[name]
            width, height = framebuffer_png_dimensions(source)
            destination = self.artifacts / name
            temporary = destination.with_name(f".{name}.{os.getpid()}.tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
            source_relative = source.relative_to(self.artifacts).as_posix()
            featured[name] = {
                "path": name,
                "source": source_relative,
                "label": FEATURED_SCREENSHOTS[name],
                "png_sha256": sha256_file(destination),
                "width": width,
                "height": height,
            }
        atomic_json(
            self.manifest_path,
            {
                "schema_version": 1,
                "status": "PASS",
                "candidate_rootfs_sha256": candidate_digest,
                "tool": "tools/cfw_validate.py",
                "run_id": self.run_dir.name,
                "started_at": self.started_at,
                "finished_at": utc_now(),
                "qemu_scope": QEMU_SCOPE,
                "hardware_tested": False,
                "inputs": inputs,
                "checks": [self.checks[name] for name in sorted(self.checks)],
                "screenshots": self.screenshots,
                "featured_screenshots": featured,
                "limitations": [
                    "Wi-Fi association and Bluetooth scanning/pairing require hardware.",
                    "MP3 selection and player state are exercised; physical DAC output is not.",
                ],
            },
        )


class QemuSession:
    def __init__(
        self,
        *,
        name: str,
        runner: Path,
        rootfs: Path,
        profile: Path,
        runtime: Path,
        sd_present: bool,
        evidence: Evidence,
        boot_timeout: float,
        transition_timeout: float,
        extra_environment: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.runner = runner
        self.rootfs = rootfs
        self.profile = profile
        self.runtime = runtime
        self.sd_present = sd_present
        self.evidence = evidence
        self.boot_timeout = boot_timeout
        self.transition_timeout = transition_timeout
        self.extra_environment = dict(extra_environment or {})
        self.process: subprocess.Popen[str] | None = None
        self.log_stream: Any = None

    @property
    def framebuffer(self) -> Path:
        return self.runtime / "framebuffer.raw"

    @property
    def frame_state(self) -> Path:
        return self.runtime / "frame-state.bin"

    @property
    def touch_fifo(self) -> Path:
        return self.runtime / "touch/event0"

    @property
    def semantic_path(self) -> Path:
        return self.runtime / "cfw-state.bin"

    @property
    def user_data(self) -> Path:
        return self.runtime / "usr-data"

    def start(self, *, stock_launcher: bool = False) -> Any:
        if self.process is not None:
            raise ValidationError(f"session {self.name} is already running")
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        log_path = self.evidence.logs_dir / f"{self.name}.log"
        self.log_stream = log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "R1_QEMU_UI_RUNTIME": str(self.runtime),
                "R1_QEMU_ROOTFS": str(self.rootfs),
                "R1_QEMU_USER_INI": str(self.profile),
                "R1_QEMU_SD_PRESENT": "1" if self.sd_present else "0",
                "R1_QEMU_ALLOW_NETWORK": "0",
            }
        )
        environment.update(self.extra_environment)
        self.process = subprocess.Popen(
            [str(self.runner)],
            cwd=REPO_ROOT,
            env=environment,
            stdout=self.log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        snapshot = self.wait_stable_frame()
        if stock_launcher:
            classification = nav.classify_launcher(snapshot.image)
            if not classification.matched:
                raise ValidationError(
                    "stock launcher classifier did not match: "
                    + json.dumps(classification.as_dict(), sort_keys=True)
                )
        return snapshot

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=4)
            except ProcessLookupError:
                pass
        if self.log_stream is not None:
            self.log_stream.close()
            self.log_stream = None

    def __enter__(self) -> "QemuSession":
        return self

    def __exit__(self, _kind: Any, _value: Any, _trace: Any) -> None:
        self.stop()

    def assert_alive(self) -> None:
        if self.process is None:
            raise ValidationError(f"session {self.name} is not running")
        result = self.process.poll()
        if result is not None:
            raise ValidationError(f"session {self.name} exited unexpectedly: {result}")

    def capture(self) -> Any:
        self.assert_alive()
        return nav.capture_coherent_frame(self.framebuffer, self.frame_state)

    def save(self, label: str, snapshot: Any | None = None) -> tuple[Any, str]:
        current = snapshot or self.capture()
        return current, self.evidence.screenshot(self.name, label, current)

    def wait_stable_frame(self) -> Any:
        deadline = time.monotonic() + self.boot_timeout
        last_hash: str | None = None
        stable = 0
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            self.assert_alive()
            try:
                snapshot = nav.capture_coherent_frame(self.framebuffer, self.frame_state)
                if snapshot.sequence >= 2:
                    if snapshot.content_sha256 == last_hash:
                        stable += 1
                    else:
                        stable = 0
                    last_hash = snapshot.content_sha256
                    if stable >= 2:
                        return snapshot
            except (OSError, RuntimeError) as error:
                last_error = error
            time.sleep(0.15)
        raise ValidationError(f"session {self.name} did not render a stable frame: {last_error}")

    def wait_change(self, previous_hash: str) -> Any:
        return self.wait_frame(lambda frame: frame.content_sha256 != previous_hash)

    def wait_hash(self, expected_hash: str) -> Any:
        return self.wait_frame(lambda frame: frame.content_sha256 == expected_hash)

    def wait_frame(self, predicate: Callable[[Any], bool]) -> Any:
        deadline = time.monotonic() + self.transition_timeout
        last: Any | None = None
        while time.monotonic() < deadline:
            self.assert_alive()
            try:
                last = self.capture()
                if predicate(last):
                    return last
            except (OSError, RuntimeError):
                pass
            time.sleep(0.05)
        detail = None if last is None else last.content_sha256
        raise ValidationError(f"session {self.name} framebuffer transition timed out; last={detail}")

    def tap(self, x: int, y: int) -> int:
        self.assert_alive()
        return int(
            nav.timed_tap(
                self.touch_fifo,
                nav.Point(x, y),
                interval_ms=40,
                move_frames=7,
            )
        )

    def tap_change(self, x: int, y: int) -> Any:
        before = self.capture()
        self.tap(x, y)
        return self.wait_change(before.content_sha256)

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> int:
        self.assert_alive()
        return int(
            nav.timed_drag(
                self.touch_fifo,
                nav.Point(*start),
                nav.Point(*end),
                duration_ms=450,
                move_frames=14,
            )
        )

    def clear_semantic(self) -> None:
        self.semantic_path.write_bytes(b"")

    def semantic(self) -> SemanticState:
        return SemanticState.parse(self.semantic_path.read_bytes())

    def wait_semantic(
        self, predicate: Callable[[SemanticState], bool], *, label: str
    ) -> SemanticState:
        deadline = time.monotonic() + self.transition_timeout
        last: SemanticState | None = None
        while time.monotonic() < deadline:
            self.assert_alive()
            try:
                last = self.semantic()
                if predicate(last):
                    return last
            except (OSError, ValidationError):
                pass
            time.sleep(0.04)
        raise ValidationError(f"semantic transition timed out for {label}; last={last}")


def main_row_y(row: int) -> int:
    return 80 + row * 72 + 36


def launcher_row_y(row: int) -> int:
    return 80 + row * 72 + 36


def open_cfw(session: QemuSession, point: tuple[int, int]) -> tuple[Any, SemanticState]:
    launcher = session.capture()
    session.clear_semantic()
    session.tap(*point)
    state = session.wait_semantic(
        lambda item: item.screen == SCREEN_MAIN and item.launcher_mask in {DEFAULT_MASK, ALL_MASK},
        label="open CFW",
    )
    session.wait_change(launcher.content_sha256)
    return launcher, state


def cfw_page(
    session: QemuSession, row_name: str, screen: int, label: str
) -> tuple[SemanticState, Any, str]:
    before = session.semantic()
    session.tap(220, main_row_y(CFW_MAIN_ROWS[row_name]))
    state = session.wait_semantic(
        lambda item: item.sequence > before.sequence and item.screen == screen,
        label=label,
    )
    snapshot, path = session.save(label)
    session.tap(40, 30)
    session.wait_semantic(
        lambda item: item.sequence > state.sequence
        and item.screen == SCREEN_MAIN
        and item.action == ACTION_BACK,
        label=f"back from {label}",
    )
    return state, snapshot, path


def leave_cfw(session: QemuSession, launcher_hash: str) -> tuple[SemanticState, Any]:
    before = session.semantic()
    session.tap(40, 30)
    final = session.wait_semantic(
        lambda item: item.sequence > before.sequence
        and item.screen == SCREEN_MAIN
        and item.action == ACTION_BACK
        and item.route == ROUTE_BACK,
        label="root Back",
    )
    restored = session.wait_hash(launcher_hash)
    return final, restored


def stock_tile_roundtrip(
    session: QemuSession,
    launcher_hash: str,
    name: str,
    point: tuple[int, int],
    *,
    capture: bool = True,
) -> str | None:
    page = session.tap_change(*point)
    path = session.evidence.screenshot(session.name, f"route-{name}", page) if capture else None
    session.tap(45, 92)
    session.wait_hash(launcher_hash)
    return path


def validate_stock(
    session: QemuSession, evidence: Evidence
) -> None:
    launcher = session.start(stock_launcher=True)
    _, screenshot = session.save("stock-launcher", launcher)
    evidence.record("stock.launcher", screenshot=screenshot)

    fixture = session.user_data / "mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3"
    if not fixture.is_file() or fixture.stat().st_size == 0:
        raise ValidationError(f"stock MP3 fixture is missing: {fixture}")
    session.tap_change(120, 173)  # Music.
    session.tap_change(355, 229)  # Files.
    for label in ("Music", "Lukrembo", "Jay"):
        frame = session.tap_change(240, 197)
        session.save(f"mp3-path-{label}", frame)
    playback = session.tap_change(240, 197)
    _, playback_path = session.save("mp3-selection-or-decoder-state", playback)
    evidence.record(
        "stock.mp3-fixture",
        fixture="/usr/data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3",
        bytes=fixture.stat().st_size,
        screenshot=playback_path,
        claim="selection/player state only; physical audio output is outside qemu-user",
    )


def validate_candidate_default(
    session: QemuSession, evidence: Evidence
) -> str:
    launcher = session.start()
    _, launcher_path = session.save("default-launcher-71", launcher)
    config = session.user_data / "r1-cfw/launcher.conf"
    if config.read_text(encoding="ascii") != "launcher_mask=71\n":
        raise ValidationError("default launcher configuration is not canonical 0x71")
    evidence.record("default.launcher.71", screenshot=launcher_path, mask="71")
    evidence.promote("default-compact-launcher.png", launcher_path)

    routes = {}
    routes["music"] = stock_tile_roundtrip(
        session, launcher.content_sha256, "default-music", (240, 173)
    )
    routes["system"] = stock_tile_roundtrip(
        session, launcher.content_sha256, "default-system", (120, 419)
    )
    routes["about"] = stock_tile_roundtrip(
        session, launcher.content_sha256, "default-about", (240, 676)
    )
    evidence.record(
        "default.stock-routes",
        screenshots=routes,
        geometry="layout-driven full/half/full default tiles",
    )

    restore_hashes = []
    for index in range(1, 4):
        baseline, _state = open_cfw(session, (360, 419))
        session.save(f"cfw-lifecycle-{index}-main")
        _final, restored = leave_cfw(session, baseline.content_sha256)
        restore_hashes.append(restored.content_sha256)
    evidence.record("cfw.lifecycle", cycles=3)
    evidence.record(
        "cfw.framebuffer-restore",
        launcher_content_sha256=launcher.content_sha256,
        restored_content_sha256=restore_hashes,
    )

    baseline, state = open_cfw(session, (360, 419))
    _main, main_path = session.save("cfw-main-sd-present")
    evidence.promote("cfw-main.png", main_path)
    enabled = session.user_data / "dropbear/enabled"
    disabled = session.user_data / "dropbear/disabled"
    if enabled.exists():
        raise ValidationError("SSH unexpectedly starts enabled in a fresh QEMU profile")
    session.save("ssh-off")
    session.tap(220, main_row_y(CFW_MAIN_ROWS["ssh"]))
    state = session.wait_semantic(
        lambda item: item.sequence > state.sequence and item.action == ACTION_SSH_TOGGLE,
        label="SSH enable",
    )
    if not enabled.is_file() or disabled.exists():
        raise ValidationError("SSH enable markers do not match the UI toggle")
    _, ssh_on_path = session.save("ssh-on-no-wifi")
    leave_cfw(session, baseline.content_sha256)

    baseline, state = open_cfw(session, (360, 419))
    _, ssh_persisted_path = session.save("ssh-on-after-reopen")
    session.tap(220, main_row_y(CFW_MAIN_ROWS["ssh"]))
    session.wait_semantic(
        lambda item: item.sequence > state.sequence and item.action == ACTION_SSH_TOGGLE,
        label="SSH disable",
    )
    if enabled.exists() or not disabled.is_file():
        raise ValidationError("SSH disable markers do not match the UI toggle")
    _, ssh_off_path = session.save("ssh-off-again")
    evidence.record(
        "cfw.ssh",
        screenshots=[ssh_on_path, ssh_persisted_path, ssh_off_path],
        persisted_across_reopen=True,
    )

    page_paths: dict[str, str] = {}
    sd_present_hash = ""
    for row_name, screen, label in (
        ("internal", SCREEN_INTERNAL, "internal-storage"),
        ("sd", SCREEN_SD, "microsd-present"),
        ("memory", SCREEN_MEMORY, "memory"),
        ("system", SCREEN_SYSTEM, "system-information"),
        ("about", SCREEN_ABOUT, "about-cfw"),
    ):
        _page_state, page_frame, page_path = cfw_page(session, row_name, screen, label)
        page_paths[row_name] = page_path
        if row_name == "sd":
            sd_present_hash = page_frame.content_sha256
    evidence.record("cfw.information-pages", screenshots=page_paths)
    evidence.record(
        "storage.sd-present",
        screenshot=page_paths["sd"],
        content_sha256=sd_present_hash,
        fixture_present=True,
    )

    before = session.semantic()
    session.tap(220, main_row_y(CFW_MAIN_ROWS["launcher"]))
    launch_state = session.wait_semantic(
        lambda item: item.sequence > before.sequence
        and item.screen == SCREEN_LAUNCHER
        and item.launcher_mask == DEFAULT_MASK,
        label="launcher settings",
    )
    _, settings_71 = session.save("launcher-settings-71")
    masks = []
    for row, expected in ((1, 0x73), (2, SIX_TILE_MASK), (3, ALL_MASK)):
        session.tap(220, launcher_row_y(row))
        launch_state = session.wait_semantic(
            lambda item, sequence=launch_state.sequence, mask=expected: (
                item.sequence > sequence
                and item.screen == SCREEN_LAUNCHER
                and item.action == ACTION_LAUNCHER_TOGGLE
                and item.launcher_mask == mask
            ),
            label=f"launcher mask {expected:02x}",
        )
        masks.append(f"{expected:02x}")
    _, settings_7f = session.save("launcher-settings-7f")
    session.tap(220, launcher_row_y(5))
    rejected = session.wait_semantic(
        lambda item: item.sequence > launch_state.sequence
        and item.action == ACTION_LAUNCHER_REJECTED
        and item.launcher_mask == ALL_MASK,
        label="locked CFW launcher tile",
    )
    session.drag((150, 250), (215, 250))
    session.wait_semantic(
        lambda item: item.sequence > rejected.sequence
        and item.action == ACTION_DRAG_IGNORED
        and item.launcher_mask == ALL_MASK,
        label="launcher-settings drag rejection",
    )
    if config.read_text(encoding="ascii") != "launcher_mask=7f\n":
        raise ValidationError("all-enabled launcher mask was not persisted canonically")
    session.tap(40, 30)
    session.wait_semantic(
        lambda item: item.screen == SCREEN_MAIN and item.action == ACTION_BACK,
        label="back from launcher settings",
    )
    evidence.record(
        "cfw.launcher-settings",
        masks=["71", *masks],
        six_tile_mask="77",
        screenshots=[settings_71, settings_7f],
        cfw_tile_locked=True,
        drag_did_not_toggle=True,
    )
    leave_cfw(session, baseline.content_sha256)

    for check, row_name, route, action in (
        ("cfw.wifi-route", "wifi", ROUTE_WIFI, ACTION_WIFI_ROUTE),
        ("cfw.bluetooth-route", "bluetooth", ROUTE_BLUETOOTH, ACTION_BLUETOOTH_ROUTE),
    ):
        route_launcher, route_state = open_cfw(session, (360, 419))
        cfw_frame = session.capture()
        session.tap(220, main_row_y(CFW_MAIN_ROWS[row_name]))
        routed = session.wait_semantic(
            lambda item, sequence=route_state.sequence, expected_route=route, expected_action=action: (
                item.sequence > sequence
                and item.route == expected_route
                and item.action == expected_action
            ),
            label=check,
        )
        stock_hub = session.wait_change(cfw_frame.content_sha256)
        _, hub_path = session.save(f"stock-wireless-from-cfw-{row_name}", stock_hub)
        session.tap(45, 92)
        session.wait_hash(route_launcher.content_sha256)
        evidence.record(
            check,
            route=routed.route,
            screenshot=hub_path,
            hardware_operation_claimed=False,
        )
    return sd_present_hash


def validate_all_enabled(session: QemuSession, evidence: Evidence) -> str:
    launcher = session.start()
    config = session.user_data / "r1-cfw/launcher.conf"
    if config.read_text(encoding="ascii") != "launcher_mask=7f\n":
        raise ValidationError("launcher mask 0x7f did not persist across QEMU restart")
    _, top_path = session.save("all-enabled-top", launcher)
    evidence.record("launcher.persistence.7f", screenshot=top_path, mask="7f")

    routes: dict[str, str | None] = {}
    for name, point in (
        ("music", (120, 173)),
        ("stream", (360, 173)),
        ("wireless", (120, 419)),
        ("ebook", (360, 419)),
        ("system", (120, 665)),
    ):
        routes[name] = stock_tile_roundtrip(
            session, launcher.content_sha256, f"all-{name}", point
        )

    before_scroll = session.capture()
    session.clear_semantic()
    session.drag((240, 700), (240, 300))
    scrolled = session.wait_change(before_scroll.content_sha256)
    _, scrolled_path = session.save("all-enabled-scrolled", scrolled)
    evidence.record("launcher.scroll-up", screenshot=scrolled_path)
    evidence.promote("all-tiles-scrolled.png", scrolled_path)
    if session.semantic_path.stat().st_size != 0:
        raise ValidationError("launcher drag unexpectedly activated the CFW tile")
    evidence.record("launcher.scroll-no-activation", semantic_state_bytes=0)

    about = session.tap_change(120, 700)
    _, about_path = session.save("all-about-after-scroll", about)
    routes["about"] = about_path
    evidence.record("launcher.tap-after-scroll", tile="about", screenshot=about_path)
    session.tap(45, 92)
    session.wait_hash(scrolled.content_sha256)

    session.drag((240, 220), (240, 700))
    restored_top = session.wait_hash(launcher.content_sha256)
    _, restored_path = session.save("all-enabled-scroll-restored", restored_top)
    evidence.record("launcher.scroll-down", screenshot=restored_path, bounded=True)

    cfw_launcher, _state = open_cfw(session, (360, 665))
    _final, restored = leave_cfw(session, cfw_launcher.content_sha256)
    routes["cfw"] = session.evidence.screenshot(
        session.name, "all-cfw-restored", restored
    )
    evidence.record(
        "launcher.all-enabled-routes",
        tiles=["music", "stream", "wireless", "ebook", "system", "cfw", "about"],
        screenshots=routes,
    )
    return launcher.content_sha256


def validate_crash_recovery(session: QemuSession, evidence: Evidence) -> None:
    launcher = session.start()
    session.clear_semantic()
    session.tap(360, 419)
    state = session.wait_semantic(
        lambda item: item.screen == SCREEN_MAIN and item.launcher_mask == DEFAULT_MASK,
        label="forced-crash sidecar open",
    )
    restored = session.wait_hash(launcher.content_sha256)
    session.assert_alive()

    # A stock route after the forced child exit proves that input ownership was
    # released as well as the framebuffer being restored.
    music = session.tap_change(240, 173)
    _, music_path = session.save("stock-touch-after-cfw-crash", music)
    session.tap(45, 92)
    session.wait_hash(launcher.content_sha256)
    evidence.record(
        "cfw.crash-recovery",
        forced_signal="SIGABRT",
        semantic_state=state.as_dict(),
        restored_content_sha256=restored.content_sha256,
        stock_player_alive=True,
        touch_route_after_crash="Music",
        screenshot=music_path,
    )


def validate_restart_position(session: QemuSession, evidence: Evidence, top_hash: str) -> None:
    launcher = session.start()
    if launcher.content_sha256 != top_hash:
        raise ValidationError(
            "all-enabled launcher did not restart at its deterministic top position"
        )
    _, path = session.save("all-enabled-after-second-restart", launcher)
    evidence.record("launcher.restart-position", screenshot=path, deterministic=True)


def validate_sd_absent(
    session: QemuSession, evidence: Evidence, present_hash: str
) -> None:
    launcher = session.start()
    _baseline, _state = open_cfw(session, (360, 419))
    _state, frame, path = cfw_page(session, "sd", SCREEN_SD, "microsd-absent")
    if frame.content_sha256 == present_hash:
        raise ValidationError("microSD present and absent pages rendered identically")
    if (session.user_data / "mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3").exists():
        raise ValidationError("SD-absent session unexpectedly contains the MP3 fixture")
    evidence.record(
        "storage.sd-absent",
        screenshot=path,
        content_sha256=frame.content_sha256,
        fixture_present=False,
    )
    evidence.record(
        "storage.sd-state-distinct",
        present_content_sha256=present_hash,
        absent_content_sha256=frame.content_sha256,
    )


def require_candidate_tree(root: Path, *, label: str) -> None:
    required = (
        root / "lib/ld.so.1",
        root / "usr/bin/hiby_player",
        root / "usr/bin/r1-cfw-ui",
        root / "usr/lib/libr1-cfw-hook.so",
        root / "usr/resource/r1-cfw/launcher/theme1/71.view",
        root / "usr/resource/r1-cfw/launcher/theme1/7f.view",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValidationError(f"{label} is missing required files: {missing}")


def preflight(args: argparse.Namespace) -> tuple[str, dict[str, str]]:
    if args.candidate_rootfs_image.is_symlink() or args.profile.is_symlink():
        raise ValidationError("candidate image and profile must be regular non-symlink files")
    candidate_dir = args.candidate_rootfs_dir.resolve()
    stock_dir = args.stock_rootfs_dir.resolve()
    image = args.candidate_rootfs_image.resolve()
    profile = args.profile.resolve()
    runner = args.runner.resolve()
    required = (
        stock_dir / "lib/ld.so.1",
        stock_dir / "usr/bin/hiby_player",
        profile,
        runner,
        image,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValidationError(f"validation prerequisites are missing: {missing}")
    require_candidate_tree(candidate_dir, label="supplied candidate rootfs tree")
    if profile.stat().st_size != 2768:
        raise ValidationError("stock profile must contain exactly 2768 bytes")
    digest = sha256_file(image)
    if args.expected_rootfs_sha256 and digest != args.expected_rootfs_sha256:
        raise ValidationError(
            "candidate rootfs digest mismatch: "
            f"expected={args.expected_rootfs_sha256}, actual={digest}"
        )
    inputs = {
        "candidate_rootfs_dir": str(candidate_dir),
        "candidate_rootfs_image": str(image),
        "stock_rootfs_dir": str(stock_dir),
        "profile": str(profile),
        "runner": str(runner),
    }
    return digest, inputs


def extract_exact_candidate(image: Path, destination: Path, log_path: Path) -> None:
    """Extract the hashed SquashFS used by QEMU; never trust a parallel tree."""

    if destination.exists():
        raise ValidationError(f"exact candidate extraction already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    unsquashfs = REPO_ROOT / "work/host-tools/usr/bin/unsquashfs"
    command = [
        sys.executable,
        str(REPO_ROOT / "tools/r1fw.py"),
        "extract-rootfs",
        str(image),
        str(destination),
    ]
    if unsquashfs.is_file():
        command.extend(("--unsquashfs", str(unsquashfs)))
    with log_path.open("w", encoding="utf-8") as stream:
        subprocess.run(
            command,
            cwd=REPO_ROOT,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            check=True,
        )
    require_candidate_tree(destination, label="exact candidate SquashFS extraction")


def run_validation(args: argparse.Namespace) -> int:
    run_id = args.run_id or dt.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    evidence: Evidence | None = None
    try:
        digest, inputs = preflight(args)
        runtime_base = args.runtime_root.resolve() / run_id
        if runtime_base.exists():
            raise ValidationError(f"runtime run already exists: {runtime_base}")
        evidence = Evidence(args.artifacts_dir, run_id)
        candidate_image = args.candidate_rootfs_image.resolve()
        exact_candidate_root = runtime_base / "exact-candidate-rootfs"
        extract_exact_candidate(
            candidate_image,
            exact_candidate_root,
            evidence.logs_dir / "extract-exact-candidate.log",
        )
        inputs["validated_candidate_rootfs_dir"] = str(exact_candidate_root)
        evidence.record(
            "preflight.exact-candidate",
            candidate_rootfs_sha256=digest,
            candidate_rootfs_bytes=candidate_image.stat().st_size,
            execution_tree="independently extracted from candidate_rootfs_image",
        )

        common = {
            "runner": args.runner.resolve(),
            "profile": args.profile.resolve(),
            "evidence": evidence,
            "boot_timeout": args.boot_timeout,
            "transition_timeout": args.transition_timeout,
        }
        with QemuSession(
            name="stock",
            rootfs=args.stock_rootfs_dir.resolve(),
            runtime=runtime_base / "stock",
            sd_present=True,
            **common,
        ) as stock:
            validate_stock(stock, evidence)

        candidate_runtime = runtime_base / "candidate-present"
        with QemuSession(
            name="candidate-default",
            rootfs=exact_candidate_root,
            runtime=candidate_runtime,
            sd_present=True,
            **common,
        ) as candidate:
            present_hash = validate_candidate_default(candidate, evidence)

        with QemuSession(
            name="candidate-crash",
            rootfs=exact_candidate_root,
            runtime=runtime_base / "candidate-crash",
            sd_present=True,
            extra_environment={"R1_QEMU_CFW_CRASH_AFTER_MS": "350"},
            **common,
        ) as crashed:
            validate_crash_recovery(crashed, evidence)

        with QemuSession(
            name="candidate-all",
            rootfs=exact_candidate_root,
            runtime=candidate_runtime,
            sd_present=True,
            **common,
        ) as all_enabled:
            top_hash = validate_all_enabled(all_enabled, evidence)

        with QemuSession(
            name="candidate-restart",
            rootfs=exact_candidate_root,
            runtime=candidate_runtime,
            sd_present=True,
            **common,
        ) as restarted:
            validate_restart_position(restarted, evidence, top_hash)

        with QemuSession(
            name="candidate-sd-absent",
            rootfs=exact_candidate_root,
            runtime=runtime_base / "candidate-sd-absent",
            sd_present=False,
            **common,
        ) as absent:
            validate_sd_absent(absent, evidence, present_hash)

        final_digest = sha256_file(candidate_image)
        if final_digest != digest:
            raise ValidationError(
                "candidate rootfs image changed during validation: "
                f"start={digest}, finish={final_digest}"
            )
        evidence.finish(final_digest, inputs)
        print(f"QEMU validation PASS: {evidence.manifest_path}")
        print(f"candidate rootfs sha256: {digest}")
        return 0
    except (OSError, subprocess.SubprocessError, ValidationError) as error:
        if evidence is not None:
            evidence.fail(error)
            print(f"QEMU validation FAIL: {evidence.manifest_path}", file=sys.stderr)
        print(f"cfw_validate.py: error: {error}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan", help="print the mandatory non-destructive matrix")
    run = subparsers.add_parser("run", help="run the matrix and write validation evidence")
    run.add_argument("--candidate-rootfs-dir", type=Path, required=True)
    run.add_argument("--candidate-rootfs-image", type=Path, required=True)
    run.add_argument("--stock-rootfs-dir", type=Path, required=True)
    run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--runner", type=Path, default=DEFAULT_RUNNER)
    run.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    run.add_argument("--artifacts-dir", type=Path, default=DEFAULT_ARTIFACTS)
    run.add_argument("--run-id")
    run.add_argument("--expected-rootfs-sha256", default="")
    run.add_argument("--boot-timeout", type=float, default=45.0)
    run.add_argument("--transition-timeout", type=float, default=10.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "destructive_actions": False,
                    "hardware_writes": False,
                    "required_checks": sorted(REQUIRED_CHECKS),
                    "steps": list(PLAN),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.expected_rootfs_sha256 and not SHA256_RE.fullmatch(
        args.expected_rootfs_sha256
    ):
        print(
            "cfw_validate.py: error: --expected-rootfs-sha256 must be 64 lowercase hex digits",
            file=sys.stderr,
        )
        return 2
    if args.boot_timeout <= 0 or args.transition_timeout <= 0:
        print("cfw_validate.py: error: timeouts must be positive", file=sys.stderr)
        return 2
    return run_validation(args)


if __name__ == "__main__":
    raise SystemExit(main())
