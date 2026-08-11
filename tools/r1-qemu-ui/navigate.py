#!/usr/bin/env python3
"""Run deterministic, non-destructive UI smoke navigation against the R1 bridge.

The runner intentionally does not crawl or guess.  Every input belongs to a
reviewed plan, every transition must change the framebuffer, and launcher
transitions are checked using the stock six-tile grid geometry.  A dry run
writes the complete plan and coverage boundary without requiring firmware,
QEMU, Pillow, a framebuffer, or a touch FIFO.

The live smoke plan assumes an isolated SD fixture with this exact structure::

    /usr/data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3

No restore, update, delete, remove, or format action is part of the plan.
Hardware- and service-dependent states are reported as expected blockers rather
than being counted as UI coverage.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Sequence


def _load_sibling_bridge() -> ModuleType:
    """Load the sibling bridge without relying on the hyphenated directory name.

    ``bridge.py`` has a guarded main function, so executing its module spec only
    defines its reusable framebuffer and input helpers.  The private module name
    also avoids accidentally importing an unrelated top-level ``bridge`` module.
    """

    bridge_path = Path(__file__).resolve().with_name("bridge.py")
    module_name = "_hiby_r1_qemu_bridge"
    existing = sys.modules.get(module_name)
    if existing is not None:
        existing_path = Path(str(getattr(existing, "__file__", ""))).resolve()
        if existing_path != bridge_path:
            raise RuntimeError(
                f"refusing conflicting {module_name} import from {existing_path}"
            )
        return existing
    spec = importlib.util.spec_from_file_location(module_name, bridge_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load QEMU UI bridge from {bridge_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load_sibling_bridge()

WIDTH = int(bridge.WIDTH)
HEIGHT = int(bridge.HEIGHT)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class Point:
    x: int
    y: int

    def __post_init__(self) -> None:
        if not (0 <= self.x < WIDTH and 0 <= self.y < HEIGHT):
            raise ValueError(f"touch point is outside {WIDTH}x{HEIGHT}: {self}")

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}


# Absolute coordinates derived from the stock 1.6 theme1 layout resources.
COORDINATES: dict[str, Point] = {
    "launcher.music": Point(120, 173),
    "launcher.stream": Point(360, 173),
    "launcher.wireless": Point(120, 419),
    "launcher.ebook": Point(360, 419),
    "launcher.system": Point(120, 676),
    "launcher.about": Point(360, 676),
    "standard.back": Point(45, 92),
    "now_playing.back": Point(50, 40),
    "music.explorer": Point(355, 229),
    "list.row1": Point(240, 197),
    "system.language": Point(240, 197),
    "system.backlight": Point(240, 323),
    "system.theme_color": Point(240, 449),
    "system.font_size": Point(240, 575),
    "system.ui_theme": Point(240, 701),
}

# Button sides are not consistent across vendor dialogs.  These coordinates are
# deliberately keyed by dialog identity and are documentation/extension points,
# not blind fallback actions in the smoke plan.
SAFE_CANCEL_COORDINATES: dict[str, Point] = {
    "standard_confirmation.cancel": Point(119, 755),
    "restore_confirmation.cancel": Point(119, 755),
    "output_select.cancel": Point(360, 755),
    "music_scan.cancel": Point(360, 755),
}

SAFETY_EXCLUSIONS: tuple[str, ...] = (
    "restore_factory_settings",
    "firmware_or_ota_update",
    "music_database_update",
    "delete_file_or_folder",
    "remove_library_item",
    "format_sd_card",
    "factory_test_destructive_actions",
)

# These tokens are rejected in executable step identifiers, labels, and target
# states.  Coverage documentation is kept separately and may describe them.
FORBIDDEN_ACTION_TOKENS: tuple[str, ...] = (
    "restore",
    "update",
    "upgrade",
    "delete",
    "remove",
    "format",
    "factory_reset",
)

EXPECTED_BLOCKED: tuple[dict[str, str], ...] = (
    {
        "state": "stream.tidal.online",
        "boundary": "stream hub renders",
        "reason": "Tidal login and catalogue require external network service access",
    },
    {
        "state": "stream.qobuz.online",
        "boundary": "stream hub renders",
        "reason": "Qobuz login and catalogue require external network service access",
    },
    {
        "state": "wireless.bluetooth_peer",
        "boundary": "wireless hub renders",
        "reason": "qemu-user does not emulate the R1 Bluetooth controller or a peer",
    },
    {
        "state": "wireless.wifi_association",
        "boundary": "wireless hub renders",
        "reason": "the stock Wi-Fi driver and radio are not present in qemu-user",
    },
    {
        "state": "wireless.hibylink_peer",
        "boundary": "wireless hub renders",
        "reason": "HiByLink requires Bluetooth/Wi-Fi hardware and a peer device",
    },
    {
        "state": "wireless.import_server",
        "boundary": "wireless hub renders",
        "reason": "Wi-Fi import requires a configured network interface",
    },
    {
        "state": "wireless.dlna_session",
        "boundary": "wireless hub renders",
        "reason": "DLNA discovery requires a working network and another endpoint",
    },
    {
        "state": "wireless.airplay_session",
        "boundary": "wireless hub renders",
        "reason": "AirPlay requires a working network and a sender",
    },
    {
        "state": "audio.physical_dac_output",
        "boundary": "test MP3 can exercise player UI and progress with a null sink",
        "reason": "qemu-user does not emulate the CS43131 DAC or analogue output",
    },
    {
        "state": "usb.device_or_otg_hardware",
        "boundary": "safe System pages render",
        "reason": "USB gadget, DAC, and OTG hardware are outside qemu-user scope",
    },
)


@dataclasses.dataclass(frozen=True)
class NavigationStep:
    id: str
    scenario: str
    operation: str
    label: str
    expected_state: str
    point: Point | None = None
    end: Point | None = None
    duration_ms: int = 320
    frames: int = 9
    require_change: bool = True
    classifier: str | None = None
    requires_classifier: str | None = None
    risk: str = "read-only"

    def as_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["point"] = self.point.as_dict() if self.point else None
        value["end"] = self.end.as_dict() if self.end else None
        return value


@dataclasses.dataclass(frozen=True)
class NavigationPlan:
    id: str
    description: str
    fixture: str
    steps: tuple[NavigationStep, ...]
    safety_exclusions: tuple[str, ...] = SAFETY_EXCLUSIONS

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "fixture": self.fixture,
            "safety_exclusions": list(self.safety_exclusions),
            "steps": [step.as_dict() for step in self.steps],
        }


def _tap(
    step_id: str,
    scenario: str,
    coordinate: str,
    label: str,
    expected_state: str,
    *,
    classifier: str | None = None,
    requires_classifier: str | None = None,
) -> NavigationStep:
    return NavigationStep(
        id=step_id,
        scenario=scenario,
        operation="tap",
        label=label,
        expected_state=expected_state,
        point=COORDINATES[coordinate],
        classifier=classifier,
        requires_classifier=requires_classifier,
    )


def _smoke_steps() -> tuple[NavigationStep, ...]:
    steps: list[NavigationStep] = [
        NavigationStep(
            id="boot.wait_for_launcher",
            scenario="boot_launcher",
            operation="wait_launcher",
            label="Observe boot and wait for the stock six-tile launcher",
            expected_state="launcher",
            duration_ms=0,
            frames=0,
            require_change=False,
            classifier="launcher",
        ),
        _tap(
            "launcher.music.open",
            "music_fixture",
            "launcher.music",
            "Open Music",
            "music.category_grid",
            requires_classifier="launcher",
        ),
        _tap(
            "music.explorer.open",
            "music_fixture",
            "music.explorer",
            "Open Files / Explorer",
            "music.explorer.sd_root",
        ),
        _tap(
            "music.fixture.enter_music",
            "music_fixture",
            "list.row1",
            "Open the fixture Music directory",
            "music.explorer.music_directory",
        ),
        _tap(
            "music.fixture.enter_artist",
            "music_fixture",
            "list.row1",
            "Open the fixture Lukrembo directory",
            "music.explorer.artist_directory",
        ),
        _tap(
            "music.fixture.enter_album",
            "music_fixture",
            "list.row1",
            "Open the fixture Jay directory",
            "music.explorer.album_directory",
        ),
        _tap(
            "music.fixture.open_track",
            "music_fixture",
            "list.row1",
            "Open Jay.mp3 from the isolated test-audio fixture",
            "music.playback_or_decoder_notice",
        ),
        _tap(
            "music.fixture.leave_playback",
            "music_fixture",
            "now_playing.back",
            "Leave Now Playing or dismiss a playback notice",
            "music.explorer.album_directory",
        ),
    ]

    # The isolated fixture has SD root/Music/Lukrembo/Jay/Jay.mp3.  Returning
    # from the album therefore takes five standard Back actions to the launcher.
    music_return_states = (
        "music.explorer.artist_directory",
        "music.explorer.music_directory",
        "music.explorer.sd_root",
        "music.category_grid",
        "launcher",
    )
    for index, state in enumerate(music_return_states, start=1):
        steps.append(
            _tap(
                f"music.fixture.back_{index}",
                "music_fixture",
                "standard.back",
                f"Return from the fixture, level {index}",
                state,
                classifier="launcher" if state == "launcher" else None,
            )
        )

    for branch, coordinate, state in (
        ("stream", "launcher.stream", "stream.hub"),
        ("wireless", "launcher.wireless", "wireless.hub"),
        ("ebook", "launcher.ebook", "ebook.root"),
    ):
        steps.extend(
            (
                _tap(
                    f"launcher.{branch}.open",
                    f"launcher_{branch}",
                    coordinate,
                    f"Open {branch.title()}",
                    state,
                    requires_classifier="launcher",
                ),
                _tap(
                    f"launcher.{branch}.back",
                    f"launcher_{branch}",
                    "standard.back",
                    f"Return from {branch.title()}",
                    "launcher",
                    classifier="launcher",
                ),
            )
        )

    steps.append(
        _tap(
            "launcher.system.open",
            "system_safe",
            "launcher.system",
            "Open System",
            "system.root",
            requires_classifier="launcher",
        )
    )
    for name, coordinate, label in (
        ("language", "system.language", "Language"),
        ("backlight", "system.backlight", "Backlight settings"),
        ("theme_color", "system.theme_color", "Theme color"),
        ("font_size", "system.font_size", "Font size"),
        ("ui_theme", "system.ui_theme", "UI themes"),
    ):
        steps.extend(
            (
                _tap(
                    f"system.safe.{name}.open",
                    "system_safe",
                    coordinate,
                    f"Open safe System view: {label}",
                    f"system.safe.{name}",
                ),
                _tap(
                    f"system.safe.{name}.back",
                    "system_safe",
                    "standard.back",
                    f"Return from safe System view: {label}",
                    "system.root",
                ),
            )
        )
    steps.append(
        _tap(
            "system.safe.back_to_launcher",
            "system_safe",
            "standard.back",
            "Return from System without scrolling to unsafe rows",
            "launcher",
            classifier="launcher",
        )
    )

    steps.extend(
        (
            _tap(
                "launcher.about.open",
                "about_safe",
                "launcher.about",
                "Open About without toggling developer state",
                "about.root",
                requires_classifier="launcher",
            ),
            _tap(
                "launcher.about.back",
                "about_safe",
                "standard.back",
                "Return from About",
                "launcher",
                classifier="launcher",
            ),
        )
    )
    return tuple(steps)


SMOKE_PLAN = NavigationPlan(
    id="smoke",
    description=(
        "Deterministic stock UI smoke route: boot/launcher, every launcher tile, "
        "isolated Explorer audio fixture, and the first five read-only System views"
    ),
    fixture="/usr/data/mnt/sd_0/Music/Lukrembo/Jay/Jay.mp3",
    steps=_smoke_steps(),
)
PLANS: dict[str, NavigationPlan] = {SMOKE_PLAN.id: SMOKE_PLAN}


def validate_plan(plan: NavigationPlan) -> None:
    """Reject out-of-bounds, ambiguous, or potentially destructive plan inputs."""

    if len({step.id for step in plan.steps}) != len(plan.steps):
        raise ValueError(f"plan {plan.id!r} contains duplicate step identifiers")
    required_exclusions = set(SAFETY_EXCLUSIONS)
    if not required_exclusions.issubset(plan.safety_exclusions):
        missing = sorted(required_exclusions.difference(plan.safety_exclusions))
        raise ValueError(f"plan {plan.id!r} is missing safety exclusions: {missing}")
    for step in plan.steps:
        if step.risk != "read-only":
            raise ValueError(f"step {step.id!r} is not marked read-only")
        if step.operation not in {"tap", "drag", "wait_launcher"}:
            raise ValueError(f"step {step.id!r} has unsupported operation {step.operation!r}")
        if step.operation in {"tap", "drag"} and step.point is None:
            raise ValueError(f"step {step.id!r} has no start point")
        if step.operation == "drag" and step.end is None:
            raise ValueError(f"drag step {step.id!r} has no end point")
        if step.operation != "drag" and step.end is not None:
            raise ValueError(f"non-drag step {step.id!r} unexpectedly has an end point")
        executable_text = " ".join((step.id, step.label, step.expected_state)).lower()
        forbidden = [token for token in FORBIDDEN_ACTION_TOKENS if token in executable_text]
        if forbidden:
            raise ValueError(f"step {step.id!r} contains forbidden action token(s): {forbidden}")


@dataclasses.dataclass
class FrameSnapshot:
    image: Any
    yoffset: int
    sequence: int
    sha256: str
    content_sha256: str
    captured_at: str

    def metadata(self) -> dict[str, Any]:
        return {
            "yoffset": self.yoffset,
            "sequence": self.sequence,
            "sha256": self.sha256,
            "content_sha256": self.content_sha256,
            "captured_at": self.captured_at,
        }


def _hash_image(image: Any) -> tuple[str, str]:
    full = hashlib.sha256(image.tobytes()).hexdigest()
    # The top bar contains clock and battery state.  Excluding it prevents an
    # unrelated clock tick from satisfying a navigation transition.
    content = hashlib.sha256(image.crop((0, 50, WIDTH, HEIGHT)).tobytes()).hexdigest()
    return full, content


def read_frame_state(state: Path) -> tuple[int, int]:
    """Read and strictly validate the fbshim active-page state record."""

    try:
        data = state.read_bytes()[: bridge.FRAME_STATE.size]
    except OSError as error:
        raise RuntimeError(f"cannot read frame state {state}: {error}") from error
    if len(data) != bridge.FRAME_STATE.size:
        raise RuntimeError(
            f"short frame state {state}: {len(data)} != {bridge.FRAME_STATE.size}"
        )
    magic, version, yoffset, sequence = bridge.FRAME_STATE.unpack(data)
    if magic != bridge.FRAME_STATE_MAGIC or version != 1:
        raise RuntimeError(
            f"invalid frame state header: magic=0x{magic:08x}, version={version}"
        )
    if yoffset not in {0, HEIGHT}:
        raise RuntimeError(f"invalid active framebuffer yoffset: {yoffset}")
    return int(yoffset), int(sequence)


def capture_coherent_frame(
    framebuffer: Path,
    state: Path,
    *,
    attempts: int = 20,
    retry_interval: float = 0.01,
    sleep: Callable[[float], None] = time.sleep,
) -> FrameSnapshot:
    """Capture an active page whose frame state is unchanged during decoding."""

    last_before = (0, 0)
    last_after = (0, 0)
    for _attempt in range(attempts):
        last_before = read_frame_state(state)
        image = bridge.decode_framebuffer(framebuffer, last_before[0])
        last_after = read_frame_state(state)
        if last_before == last_after:
            full_hash, content_hash = _hash_image(image)
            return FrameSnapshot(
                image=image,
                yoffset=last_after[0],
                sequence=last_after[1],
                sha256=full_hash,
                content_sha256=content_hash,
                captured_at=utc_now(),
            )
        sleep(retry_interval)
    raise RuntimeError(
        "frame state changed during every capture attempt: "
        f"before={last_before}, after={last_after}"
    )


def timed_tap(
    touch_fifo: Path,
    point: Point,
    *,
    interval_ms: int = 40,
    move_frames: int = 7,
    injector: Callable[[Path, int, int, str], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Inject a held tap matching the event cadence accepted by LiteGUI."""

    if interval_ms < 0 or move_frames < 1:
        raise ValueError("tap interval must be non-negative and move_frames positive")
    inject = injector or bridge.inject_touch
    phases = ("down",) + ("move",) * move_frames + ("up",)
    total = 0
    for index, phase in enumerate(phases):
        total += inject(touch_fifo, point.x, point.y, phase)
        if index + 1 < len(phases) and interval_ms:
            sleep(interval_ms / 1000.0)
    return total


def timed_drag(
    touch_fifo: Path,
    start: Point,
    end: Point,
    *,
    duration_ms: int = 320,
    move_frames: int = 12,
    injector: Callable[[Path, int, int, str], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Inject a deterministic linear drag with intermediate move reports."""

    if duration_ms < 0 or move_frames < 2:
        raise ValueError("drag duration must be non-negative and move_frames at least two")
    inject = injector or bridge.inject_touch
    positions = [
        Point(
            round(start.x + (end.x - start.x) * index / move_frames),
            round(start.y + (end.y - start.y) * index / move_frames),
        )
        for index in range(1, move_frames + 1)
    ]
    interval = duration_ms / 1000.0 / (move_frames + 1)
    total = inject(touch_fifo, start.x, start.y, "down")
    if interval:
        sleep(interval)
    for position in positions:
        total += inject(touch_fifo, position.x, position.y, "move")
        if interval:
            sleep(interval)
    total += inject(touch_fifo, end.x, end.y, "up")
    return total


@dataclasses.dataclass(frozen=True)
class Classification:
    name: str
    matched: bool
    confidence: float
    evidence: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def classify_launcher(image: Any) -> Classification:
    """Recognize the launcher's one vertical and two horizontal divider lines."""

    if image.size != (WIDTH, HEIGHT):
        return Classification(
            "launcher", False, 0.0, {"reason": f"unexpected image size {image.size}"}
        )
    rgb = image.convert("RGB")
    pixels = rgb.load()

    def difference(left: tuple[int, ...], right: tuple[int, ...]) -> float:
        return sum(abs(int(a) - int(b)) for a, b in zip(left, right)) / 3.0

    vertical = sum(
        (
            difference(pixels[240, y], pixels[239, y])
            + difference(pixels[240, y], pixels[241, y])
        )
        / 2.0
        for y in range(50, 780)
    ) / 730.0
    horizontal_scores: list[float] = []
    for y in (296, 543):
        score = sum(
            (
                difference(pixels[x, y], pixels[x, y - 1])
                + difference(pixels[x, y], pixels[x, y + 1])
            )
            / 2.0
            for x in range(WIDTH)
        ) / WIDTH
        horizontal_scores.append(score)
    minimum = min(vertical, *horizontal_scores)
    threshold = 8.0
    return Classification(
        name="launcher",
        matched=minimum >= threshold,
        confidence=min(1.0, minimum / 20.0),
        evidence={
            "vertical_divider_score": round(vertical, 3),
            "horizontal_divider_scores": [round(value, 3) for value in horizontal_scores],
            "minimum_score": round(minimum, 3),
            "threshold": threshold,
        },
    )


CLASSIFIERS: dict[str, Callable[[Any], Classification]] = {
    "launcher": classify_launcher,
}


def classify(name: str, image: Any) -> Classification:
    try:
        classifier = CLASSIFIERS[name]
    except KeyError as error:
        raise RuntimeError(f"unknown screen classifier: {name}") from error
    return classifier(image)


class RunArtifacts:
    """Own one immutable run directory and its JSON/PNG event trail."""

    def __init__(
        self,
        root: Path,
        run_id: str,
        plan: NavigationPlan,
        *,
        dry_run: bool,
        inputs: dict[str, str | None],
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
            raise ValueError(f"unsafe run id: {run_id!r}")
        self.run_dir = root / run_id
        if self.run_dir.exists():
            raise RuntimeError(f"artifact run already exists: {self.run_dir}")
        self.frames_dir = self.run_dir / "frames"
        self.frames_dir.mkdir(parents=True)
        self.events_path = self.run_dir / "ui-events.jsonl"
        self.events_path.touch()
        self.frame_index = 0
        self.manifest: dict[str, Any] = {
            "schema_version": 1,
            "tool": "tools/r1-qemu-ui/navigate.py",
            "run_id": run_id,
            "dry_run": dry_run,
            "status": "running",
            "started_at": utc_now(),
            "finished_at": None,
            "full_coverage_claim": False,
            "inputs": inputs,
            "plan": plan.as_dict(),
            "steps": [],
            "frames": [],
        }
        self.coverage: dict[str, Any] = {
            "schema_version": 1,
            "plan_id": plan.id,
            "scope": (
                "curated smoke coverage only; a frame change is not semantic screen proof"
            ),
            "full_coverage": False,
            "safety_exclusions": list(plan.safety_exclusions),
            "dialog_policy": {
                "unexpected_dialog": "capture-and-stop; never guess a button",
                "known_safe_cancels": {
                    name: point.as_dict() for name, point in SAFE_CANCEL_COORDINATES.items()
                },
            },
            "expected_blocked": [
                {**entry, "status": "expected-blocked"} for entry in EXPECTED_BLOCKED
            ],
            "planned_states": [step.expected_state for step in plan.steps],
            "attempted_states": [],
            "observed_frame_changes": [],
            "verified_states": [],
            "failures": [],
            "summary": {},
        }

    def write_event(self, event: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")

    def save_frame(self, snapshot: FrameSnapshot, label: str) -> str:
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.") or "frame"
        relative = Path("frames") / f"{self.frame_index:04d}_{safe_label}.png"
        target = self.run_dir / relative
        snapshot.image.save(target, format="PNG", optimize=False)
        record = {"path": relative.as_posix(), **snapshot.metadata()}
        self.manifest["frames"].append(record)
        self.frame_index += 1
        return relative.as_posix()

    def record_step(self, record: dict[str, Any]) -> None:
        self.manifest["steps"].append(record)
        expected_state = str(record["expected_state"])
        status = str(record["status"])
        if status != "planned":
            self.coverage["attempted_states"].append(expected_state)
        if record.get("frame_changed"):
            self.coverage["observed_frame_changes"].append(expected_state)
        classification = record.get("classification")
        if isinstance(classification, dict) and classification.get("matched"):
            self.coverage["verified_states"].append(expected_state)
        if status in {"failed", "no-frame-change", "classifier-mismatch"}:
            self.coverage["failures"].append(
                {"step": record["id"], "state": expected_state, "status": status}
            )

    def finish(self, status: str, *, fatal_error: str | None = None) -> None:
        self.manifest["status"] = status
        self.manifest["finished_at"] = utc_now()
        if fatal_error:
            self.manifest["fatal_error"] = fatal_error
        records = self.manifest["steps"]
        self.coverage["summary"] = {
            "planned_steps": len(self.manifest["plan"]["steps"]),
            "recorded_steps": len(records),
            "executed_steps": sum(record.get("status") != "planned" for record in records),
            "frame_changes": sum(bool(record.get("frame_changed")) for record in records),
            "verified_states": len(self.coverage["verified_states"]),
            "failures": len(self.coverage["failures"]),
        }
        self._write_json("manifest.json", self.manifest)
        self._write_json("coverage.json", self.coverage)

    def _write_json(self, name: str, value: dict[str, Any]) -> None:
        target = self.run_dir / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, target)


class FrameChangeTimeout(RuntimeError):
    def __init__(self, message: str, last_snapshot: FrameSnapshot) -> None:
        super().__init__(message)
        self.last_snapshot = last_snapshot


class Navigator:
    def __init__(
        self,
        plan: NavigationPlan,
        artifacts: RunArtifacts,
        framebuffer: Path,
        state: Path,
        touch_fifo: Path,
        *,
        boot_timeout: float,
        transition_timeout: float,
        poll_interval: float,
        tap_interval_ms: int,
    ) -> None:
        self.plan = plan
        self.artifacts = artifacts
        self.framebuffer = framebuffer
        self.state = state
        self.touch_fifo = touch_fifo
        self.boot_timeout = boot_timeout
        self.transition_timeout = transition_timeout
        self.poll_interval = poll_interval
        self.tap_interval_ms = tap_interval_ms

    def capture(self) -> FrameSnapshot:
        return capture_coherent_frame(self.framebuffer, self.state)

    def wait_for_change(self, previous: FrameSnapshot) -> FrameSnapshot:
        deadline = time.monotonic() + self.transition_timeout
        last = previous
        while time.monotonic() < deadline:
            candidate = self.capture()
            last = candidate
            if candidate.content_sha256 != previous.content_sha256:
                return candidate
            time.sleep(self.poll_interval)
        raise FrameChangeTimeout(
            f"no content-frame change within {self.transition_timeout:.2f}s", last
        )

    def wait_for_launcher(self) -> tuple[FrameSnapshot, Classification, str]:
        first = self.capture()
        first_path = self.artifacts.save_frame(first, "boot-or-current")
        deadline = time.monotonic() + self.boot_timeout
        last = first
        consecutive_matches = 0
        last_classification = classify_launcher(first.image)
        while True:
            current_classification = classify_launcher(last.image)
            if current_classification.matched:
                consecutive_matches += 1
                last_classification = current_classification
                if consecutive_matches >= 2:
                    return last, last_classification, first_path
            else:
                consecutive_matches = 0
                last_classification = current_classification
            if time.monotonic() >= deadline:
                raise FrameChangeTimeout(
                    "launcher was not recognized; no touch input was injected", last
                )
            time.sleep(self.poll_interval)
            last = self.capture()

    def _check_required_classifier(
        self, step: NavigationStep, snapshot: FrameSnapshot
    ) -> Classification | None:
        if step.requires_classifier is None:
            return None
        result = classify(step.requires_classifier, snapshot.image)
        if not result.matched:
            raise RuntimeError(
                f"precondition classifier {step.requires_classifier!r} failed for {step.id}"
            )
        return result

    def _inject(self, step: NavigationStep) -> int:
        if step.point is None:
            raise RuntimeError(f"step {step.id} has no touch coordinate")
        if step.operation == "tap":
            return timed_tap(
                self.touch_fifo,
                step.point,
                interval_ms=self.tap_interval_ms,
                move_frames=max(1, step.frames - 2),
            )
        if step.operation == "drag":
            if step.end is None:
                raise RuntimeError(f"drag step {step.id} has no end coordinate")
            return timed_drag(
                self.touch_fifo,
                step.point,
                step.end,
                duration_ms=step.duration_ms,
                move_frames=max(2, step.frames - 2),
            )
        raise RuntimeError(f"cannot inject operation {step.operation!r}")

    def run(self) -> None:
        current: FrameSnapshot | None = None
        for step in self.plan.steps:
            if step.operation == "wait_launcher":
                try:
                    current, result, first_path = self.wait_for_launcher()
                    final_path = self.artifacts.save_frame(current, step.id)
                    self.artifacts.record_step(
                        {
                            "id": step.id,
                            "scenario": step.scenario,
                            "operation": step.operation,
                            "expected_state": step.expected_state,
                            "status": "verified",
                            "frame_changed": None,
                            "classification": result.as_dict(),
                            "initial_frame": first_path,
                            "frame": final_path,
                            "after": current.metadata(),
                        }
                    )
                    continue
                except FrameChangeTimeout as error:
                    failed_path = self.artifacts.save_frame(error.last_snapshot, step.id + "-failed")
                    self.artifacts.record_step(
                        {
                            "id": step.id,
                            "scenario": step.scenario,
                            "operation": step.operation,
                            "expected_state": step.expected_state,
                            "status": "classifier-mismatch",
                            "frame_changed": None,
                            "classification": classify_launcher(
                                error.last_snapshot.image
                            ).as_dict(),
                            "frame": failed_path,
                            "error": str(error),
                        }
                    )
                    raise

            before = self.capture()
            try:
                precondition = self._check_required_classifier(step, before)
            except RuntimeError as error:
                self.artifacts.record_step(
                    {
                        "id": step.id,
                        "scenario": step.scenario,
                        "operation": step.operation,
                        "expected_state": step.expected_state,
                        "status": "classifier-mismatch",
                        "frame_changed": False,
                        "classification": None,
                        "error": str(error),
                    }
                )
                raise

            event: dict[str, Any] = {
                "timestamp": utc_now(),
                "step": step.id,
                "operation": step.operation,
                "point": step.point.as_dict() if step.point else None,
                "end": step.end.as_dict() if step.end else None,
                "duration_ms": step.duration_ms,
                "frames": step.frames,
                "dry_run": False,
            }
            try:
                written = self._inject(step)
                event["written_bytes"] = written
            except Exception as error:
                event["result"] = "injection-failed"
                event["error"] = str(error)
                self.artifacts.write_event(event)
                failed_path = self.artifacts.save_frame(before, step.id + "-input-failed")
                self.artifacts.record_step(
                    {
                        "id": step.id,
                        "scenario": step.scenario,
                        "operation": step.operation,
                        "expected_state": step.expected_state,
                        "status": "failed",
                        "frame_changed": False,
                        "classification": None,
                        "frame": failed_path,
                        "before": before.metadata(),
                        "after": before.metadata(),
                        "error": str(error),
                    }
                )
                raise
            try:
                after = self.wait_for_change(before) if step.require_change else self.capture()
            except FrameChangeTimeout as error:
                event["result"] = "no-frame-change"
                self.artifacts.write_event(event)
                failed_path = self.artifacts.save_frame(error.last_snapshot, step.id + "-unchanged")
                self.artifacts.record_step(
                    {
                        "id": step.id,
                        "scenario": step.scenario,
                        "operation": step.operation,
                        "expected_state": step.expected_state,
                        "status": "no-frame-change",
                        "frame_changed": False,
                        "classification": None,
                        "frame": failed_path,
                        "before": before.metadata(),
                        "after": error.last_snapshot.metadata(),
                        "error": str(error),
                    }
                )
                raise
            event["result"] = "frame-changed"
            self.artifacts.write_event(event)

            result = classify(step.classifier, after.image) if step.classifier else None
            status = "changed-unclassified"
            if result is not None:
                status = "verified" if result.matched else "classifier-mismatch"
            frame_path = self.artifacts.save_frame(after, step.id)
            record = {
                "id": step.id,
                "scenario": step.scenario,
                "operation": step.operation,
                "expected_state": step.expected_state,
                "status": status,
                "frame_changed": after.content_sha256 != before.content_sha256,
                "sequence_changed": after.sequence != before.sequence,
                "classification": result.as_dict() if result else None,
                "precondition": precondition.as_dict() if precondition else None,
                "frame": frame_path,
                "before": before.metadata(),
                "after": after.metadata(),
            }
            self.artifacts.record_step(record)
            if result is not None and not result.matched:
                raise RuntimeError(f"screen classifier {result.name!r} failed after {step.id}")
            current = after


def run_dry(plan: NavigationPlan, artifacts: RunArtifacts) -> None:
    """Serialize and validate a plan without fabricating framebuffer evidence."""

    for step in plan.steps:
        if step.operation in {"tap", "drag"}:
            artifacts.write_event(
                {
                    "timestamp": utc_now(),
                    "step": step.id,
                    "operation": step.operation,
                    "point": step.point.as_dict() if step.point else None,
                    "end": step.end.as_dict() if step.end else None,
                    "duration_ms": step.duration_ms,
                    "frames": step.frames,
                    "dry_run": True,
                    "result": "planned-only",
                }
            )
        artifacts.record_step(
            {
                "id": step.id,
                "scenario": step.scenario,
                "operation": step.operation,
                "expected_state": step.expected_state,
                "status": "planned",
                "frame_changed": None,
                "classification": None,
                "frame": None,
            }
        )


def _default_run_id(plan_id: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{plan_id}-{stamp}-{os.getpid()}"


def _touch_endpoint_exists(path: Path) -> bool:
    if path.exists():
        return True
    return any(candidate.is_fifo() for candidate in path.parent.glob(path.name + ".*"))


@contextlib.contextmanager
def runtime_lock(path: Path) -> Any:
    """Hold a non-blocking advisory lock for one live navigation run."""

    try:
        import fcntl
    except ImportError as error:  # pragma: no cover - the R1 tooling is Linux-only.
        raise RuntimeError("runtime locking requires fcntl") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"another navigator holds runtime lock {path}") from error
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps({"pid": os.getpid(), "started_at": utc_now()}) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", choices=sorted(PLANS), help="curated navigation plan")
    parser.add_argument("--dry-run", action="store_true", help="validate and write plan artifacts only")
    parser.add_argument("--framebuffer", type=Path, help="raw double-buffer framebuffer from fbshim")
    parser.add_argument("--state", type=Path, help="frame-state.bin from fbshim")
    parser.add_argument("--touch-fifo", type=Path, help="evdev touch FIFO base path")
    parser.add_argument(
        "--lock-file",
        type=Path,
        help="advisory runtime lock (default: .navigate.lock beside the touch FIFO)",
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=Path("artifacts/ui"),
        help="artifact parent directory (default: artifacts/ui)",
    )
    parser.add_argument("--run-id", help="safe unique artifact directory name")
    parser.add_argument("--boot-timeout", type=float, default=30.0)
    parser.add_argument("--transition-timeout", type=float, default=3.0)
    parser.add_argument("--poll-ms", type=int, default=50)
    parser.add_argument("--tap-interval-ms", type=int, default=40)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    plan = PLANS[args.plan]
    try:
        validate_plan(plan)
        if args.boot_timeout <= 0 or args.transition_timeout <= 0:
            raise ValueError("timeouts must be positive")
        if args.poll_ms <= 0 or args.tap_interval_ms < 0:
            raise ValueError("poll interval must be positive and tap interval non-negative")
        if not args.dry_run:
            missing_options = [
                option
                for option, value in (
                    ("--framebuffer", args.framebuffer),
                    ("--state", args.state),
                    ("--touch-fifo", args.touch_fifo),
                )
                if value is None
            ]
            if missing_options:
                raise ValueError("live navigation requires " + ", ".join(missing_options))
            if not args.framebuffer.is_file():
                raise ValueError(f"framebuffer does not exist: {args.framebuffer}")
            if not args.state.is_file():
                raise ValueError(f"frame state does not exist: {args.state}")
            if not _touch_endpoint_exists(args.touch_fifo):
                raise ValueError(f"touch FIFO has no live endpoint: {args.touch_fifo}")
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))

    run_id = args.run_id or _default_run_id(plan.id)
    lock_file = args.lock_file
    if lock_file is None and args.touch_fifo is not None:
        lock_file = args.touch_fifo.parent / ".navigate.lock"
    inputs = {
        "framebuffer": str(args.framebuffer) if args.framebuffer else None,
        "state": str(args.state) if args.state else None,
        "touch_fifo": str(args.touch_fifo) if args.touch_fifo else None,
        "lock_file": str(lock_file) if lock_file else None,
    }
    try:
        artifacts = RunArtifacts(
            args.artifacts_root, run_id, plan, dry_run=args.dry_run, inputs=inputs
        )
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))

    if args.dry_run:
        run_dry(plan, artifacts)
        artifacts.finish("dry-run")
        print(f"dry-run artifacts: {artifacts.run_dir}")
        return 0

    navigator = Navigator(
        plan,
        artifacts,
        args.framebuffer,
        args.state,
        args.touch_fifo,
        boot_timeout=args.boot_timeout,
        transition_timeout=args.transition_timeout,
        poll_interval=args.poll_ms / 1000.0,
        tap_interval_ms=args.tap_interval_ms,
    )
    try:
        if lock_file is None:
            raise RuntimeError("live navigation has no runtime lock path")
        with runtime_lock(lock_file):
            navigator.run()
    except Exception as error:  # Preserve evidence for any live transition failure.
        artifacts.finish("failed", fatal_error=str(error))
        print(f"navigation failed; artifacts: {artifacts.run_dir}: {error}", file=sys.stderr)
        return 1
    artifacts.finish("completed-smoke")
    print(f"navigation artifacts: {artifacts.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
