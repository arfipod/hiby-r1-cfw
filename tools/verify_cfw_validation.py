#!/usr/bin/env python3
"""Verify the QEMU evidence gate for an R1 CFW release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path


SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAMEBUFFER_WIDTH = 480
FRAMEBUFFER_HEIGHT = 800
QEMU_SCOPE = "qemu-user application harness; not X1600 board emulation"
REPO_ROOT = Path(__file__).resolve().parents[1]

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
        "ssh.runtime-auth",
        "cfw.information-pages",
        "cfw.launcher-settings",
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
        "storage.sd-state-distinct",
    }
)

FEATURED_SCREENSHOTS = {
    "default-compact-launcher.png": "default-launcher-71",
    "cfw-main.png": "cfw-main-sd-present",
    "six-tile-launcher.png": "six-tile-launcher-77",
}

CANDIDATE_SSH_COMPONENTS = {
    "busybox": "bin/busybox",
    "controller": "usr/bin/r1-ssh-control",
    "dropbear": "usr/sbin/dropbearmulti",
}


class ValidationError(RuntimeError):
    """The evidence is absent, malformed, unsuccessful, or stale."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def squashfs_component_hashes(image: Path, expected_digest: str) -> dict[str, str]:
    """Recompute release-critical component hashes from the exact SquashFS."""

    if image.is_symlink() or not image.is_file():
        raise ValidationError(f"candidate rootfs image is missing or a symlink: {image}")
    if sha256_file(image) != expected_digest:
        raise ValidationError("candidate rootfs image does not match the expected digest")
    unsquashfs = REPO_ROOT / "work/host-tools/usr/bin/unsquashfs"
    if not unsquashfs.is_file():
        located = shutil.which("unsquashfs")
        if not located:
            raise ValidationError("unsquashfs is required to verify candidate components")
        unsquashfs = Path(located)
    hashes: dict[str, str] = {}
    for name, member in CANDIDATE_SSH_COMPONENTS.items():
        result = subprocess.run(
            [str(unsquashfs), "-cat", str(image), member],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0 or not result.stdout:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValidationError(
                f"cannot read {member} from candidate SquashFS: {error}"
            )
        hashes[name] = hashlib.sha256(result.stdout).hexdigest()
    if sha256_file(image) != expected_digest:
        raise ValidationError("candidate rootfs image changed during component verification")
    return hashes


def evidence_file(root: Path, relative: object, *, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValidationError(f"{label} path must be a non-empty string")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValidationError(f"{label} path escapes the evidence directory: {relative}")
    unresolved = root / path
    resolved = unresolved.resolve()
    if resolved != unresolved.absolute() or root not in resolved.parents:
        raise ValidationError(f"{label} path uses a symlink or escapes evidence: {relative}")
    if unresolved.is_symlink() or not unresolved.is_file():
        raise ValidationError(f"{label} is not a regular file: {relative}")
    return unresolved


def current_run_log(
    evidence_root: Path, relative: object, run_id: str, *, label: str
) -> Path:
    if not isinstance(relative, str) or Path(relative).parent != Path(run_id) / "logs":
        raise ValidationError(f"{label} is not bound to current run {run_id}")
    return evidence_file(evidence_root, relative, label=label)


def validate_framebuffer_png(path: Path, *, label: str) -> tuple[int, int]:
    """Parse CRC-checked, non-interlaced RGB PNG evidence from the 480x800 FB."""

    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValidationError(f"cannot read {label}: {path}: {error}") from error
    if len(data) < 1024 or data[:8] != PNG_SIGNATURE:
        raise ValidationError(f"{label} is not a complete framebuffer PNG: {path}")

    offset = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValidationError(f"{label} has a truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValidationError(f"{label} has a truncated PNG payload: {path}")
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            raise ValidationError(f"{label} has an invalid PNG CRC: {path}")
        chunks.append((chunk_type, payload))
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(data) or not chunks or chunks[-1][0] != b"IEND":
        raise ValidationError(f"{label} has an invalid PNG terminator: {path}")

    chunk_type, ihdr = chunks[0]
    if chunk_type != b"IHDR" or len(ihdr) != 13:
        raise ValidationError(f"{label} has an invalid PNG IHDR: {path}")
    width, height = struct.unpack(">II", ihdr[:8])
    if (width, height) != (FRAMEBUFFER_WIDTH, FRAMEBUFFER_HEIGHT):
        raise ValidationError(
            f"{label} is {width}x{height}; expected "
            f"{FRAMEBUFFER_WIDTH}x{FRAMEBUFFER_HEIGHT}: {path}"
        )
    if ihdr[8:] != bytes((8, 2, 0, 0, 0)):
        raise ValidationError(f"{label} must be non-interlaced 8-bit RGB: {path}")

    compressed = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    if not compressed:
        raise ValidationError(f"{label} contains no PNG image data: {path}")
    expected_bytes = height * (1 + width * 3)
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(compressed, expected_bytes + 1)
        pixels += decompressor.flush()
    except zlib.error as error:
        raise ValidationError(f"{label} has invalid compressed pixels: {path}") from error
    if (
        len(pixels) != expected_bytes
        or not decompressor.eof
        or decompressor.unused_data
    ):
        raise ValidationError(f"{label} has an invalid RGB payload size: {path}")
    stride = 1 + width * 3
    if any(pixels[offset] > 4 for offset in range(0, len(pixels), stride)):
        raise ValidationError(f"{label} contains an invalid PNG row filter: {path}")
    return width, height


def validate_checks(
    document: dict[str, object],
    expected_digest: str,
    component_hashes: dict[str, str],
) -> None:
    checks = document.get("checks")
    if not isinstance(checks, list):
        raise ValidationError("QEMU validation checks must be an array")
    seen: dict[str, dict[str, object]] = {}
    for index, item in enumerate(checks):
        if not isinstance(item, dict):
            raise ValidationError(f"QEMU validation check {index} must be an object")
        check_id = item.get("id")
        if not isinstance(check_id, str):
            raise ValidationError(f"QEMU validation check {index} has no string id")
        if check_id in seen:
            raise ValidationError(f"duplicate QEMU validation check: {check_id}")
        if item.get("status") != "PASS":
            raise ValidationError(f"QEMU validation check is not PASS: {check_id}")
        seen[check_id] = item
    missing = sorted(REQUIRED_CHECKS.difference(seen))
    unexpected = sorted(set(seen).difference(REQUIRED_CHECKS))
    if missing or unexpected:
        raise ValidationError(
            f"QEMU validation check set mismatch: missing={missing}, unexpected={unexpected}"
        )
    preflight = seen["preflight.exact-candidate"]
    if preflight.get("candidate_rootfs_sha256") != expected_digest:
        raise ValidationError("preflight check is not bound to the candidate rootfs")
    if preflight.get("execution_tree") != (
        "independently extracted from candidate_rootfs_image"
    ):
        raise ValidationError("preflight check did not execute the exact SquashFS tree")
    if preflight.get("execution_adapters") != {
        "root_backend_requested": "bwrap",
        "sys_server_stub_skipped": True,
        "sys_server_stub_skip_reason": (
            "managed sandbox denies AF_UNIX socket creation"
        ),
        "mandatory_checks_relaxed": False,
    }:
        raise ValidationError("QEMU execution adapter evidence is invalid")
    ssh_runtime = seen["ssh.runtime-auth"]
    components = ssh_runtime.get("candidate_components")
    if not isinstance(components, dict) or set(components) != set(CANDIDATE_SSH_COMPONENTS):
        raise ValidationError("SSH runtime candidate component set is invalid")
    for name, member in CANDIDATE_SSH_COMPONENTS.items():
        component = components.get(name)
        if not isinstance(component, dict) or (
            component.get("path") != member
            or component.get("sha256") != component_hashes[name]
        ):
            raise ValidationError(
                f"SSH runtime {name} hash is not bound to the exact candidate SquashFS"
            )
    if ssh_runtime.get("host_key_generated") is not True:
        raise ValidationError("SSH runtime check did not prove host-key generation")
    if ssh_runtime.get("hardware_operation_claimed") is not False:
        raise ValidationError("SSH runtime check must not claim a hardware operation")

    settings = seen["cfw.launcher-settings"]
    if (
        settings.get("masks") != ["71", "73", "77"]
        or settings.get("six_tile_mask") != "77"
        or settings.get("maximum_tiles") != 6
        or settings.get("seventh_tile_rejected") is not True
        or settings.get("cfw_tile_locked") is not True
        or settings.get("drag_did_not_toggle") is not True
    ):
        raise ValidationError("launcher settings transition evidence is invalid")
    maximum = seen["launcher.maximum-rejected"]
    if (
        maximum.get("attempted_tile") != "ebook"
        or maximum.get("mask") != "77"
        or maximum.get("config") != "launcher_mask=77\n"
        or maximum.get("action") != 7
        or maximum.get("expected_message")
        != "Maximum 6 launcher tiles. Disable one first."
        or maximum.get("framebuffer_changed") is not True
    ):
        raise ValidationError("maximum-six launcher rejection evidence is invalid")
    drag = seen["launcher.drag-no-activation"]
    if (
        drag.get("mask") != "77"
        or drag.get("config") != "launcher_mask=77\n"
        or drag.get("locked_cfw_action") != 7
        or drag.get("drag_action") != 8
        or drag.get("cfw_tile_locked") is not True
        or drag.get("drag_did_not_toggle") is not True
    ):
        raise ValidationError("locked-CFW or launcher-drag evidence is invalid")
    persistence = seen["launcher.persistence.77"]
    if (
        persistence.get("mask") != "77"
        or persistence.get("config") != "launcher_mask=77\n"
        or persistence.get("tile_count") != 6
        or persistence.get("persisted_across_restart") is not True
    ):
        raise ValidationError("six-tile mask 0x77 persistence evidence is invalid")
    routes = seen["launcher.six-tile-routes"]
    visible_77 = ["music", "stream", "wireless", "system", "cfw", "about"]
    if (
        routes.get("mask") != "77"
        or routes.get("tiles") != visible_77
        or routes.get("all_routes_restored") is not True
        or not isinstance(routes.get("screenshots"), dict)
        or set(routes["screenshots"]) != set(visible_77)
    ):
        raise ValidationError("six-tile route evidence is invalid")
    swap = seen["launcher.ebook-swap"]
    if (
        swap.get("transitions") != ["77", "75", "7d"]
        or swap.get("disabled_tile") != "stream"
        or swap.get("enabled_tile") != "ebook"
        or swap.get("final_mask") != "7d"
        or swap.get("config") != "launcher_mask=7d\n"
        or swap.get("applies_on_restart") is not True
    ):
        raise ValidationError("eBook six-tile swap evidence is invalid")
    alternate = seen["launcher.alternate-six-persistence"]
    if (
        alternate.get("mask") != "7d"
        or alternate.get("config") != "launcher_mask=7d\n"
        or alternate.get("tile_count") != 6
        or alternate.get("disabled_tile") != "stream"
        or alternate.get("enabled_tile") != "ebook"
        or alternate.get("ebook_route_restored") is not True
        or alternate.get("persisted_across_restart") is not True
    ):
        raise ValidationError("alternate six-tile persistence evidence is invalid")
    restart = seen["launcher.restart-position"]
    if (
        restart.get("mask") != "7d"
        or restart.get("config") != "launcher_mask=7d\n"
        or restart.get("deterministic") is not True
        or restart.get("fixed_position") is not True
    ):
        raise ValidationError("alternate six-tile restart evidence is invalid")


def validate_screenshots(document: dict[str, object], evidence_root: Path) -> None:
    screenshots = document.get("screenshots")
    if not isinstance(screenshots, list):
        raise ValidationError("QEMU validation screenshots must be an array")
    records: dict[str, dict[str, object]] = {}
    screenshot_digests: dict[str, str] = {}
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not SAFE_NAME_RE.fullmatch(run_id):
        raise ValidationError("QEMU validation run_id is invalid")
    for index, item in enumerate(screenshots):
        if not isinstance(item, dict):
            raise ValidationError(f"screenshot record {index} must be an object")
        relative = item.get("path")
        if not isinstance(relative, str) or relative in records:
            raise ValidationError(f"screenshot record {index} has a missing or duplicate path")
        if Path(relative).parent != Path(run_id) / "screens":
            raise ValidationError(f"screenshot record {index} is not bound to the current run")
        if not isinstance(item.get("label"), str) or not isinstance(item.get("session"), str):
            raise ValidationError(f"screenshot record {index} lacks a label or session")
        if not SAFE_NAME_RE.fullmatch(str(item["session"])):
            raise ValidationError(f"screenshot record {index} has an unsafe session")
        for digest_name in ("content_sha256", "frame_sha256"):
            if not isinstance(item.get(digest_name), str) or not SHA256_RE.fullmatch(
                str(item[digest_name])
            ):
                raise ValidationError(
                    f"screenshot record {index} has an invalid {digest_name}"
                )
        source = evidence_file(evidence_root, relative, label="screenshot evidence")
        validate_framebuffer_png(source, label="screenshot evidence")
        records[relative] = item
        screenshot_digests[relative] = sha256_file(source)

    checks = document.get("checks")
    if not isinstance(checks, list):
        raise ValidationError("launcher screenshot evidence has no check array")
    by_id = {
        item.get("id"): item
        for item in checks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    def require_capture(relative: object, expected_label: str, *, field: str) -> None:
        if not isinstance(relative, str) or relative not in records:
            raise ValidationError(f"{field} does not reference current-run screenshot evidence")
        if records[relative].get("label") != expected_label:
            raise ValidationError(f"{field} references the wrong framebuffer capture")

    settings = by_id["cfw.launcher-settings"]
    settings_paths = settings.get("screenshots")
    if not isinstance(settings_paths, list) or len(settings_paths) != 3:
        raise ValidationError("launcher settings screenshot set is invalid")
    for relative, label in zip(
        settings_paths,
        (
            "launcher-settings-71",
            "launcher-settings-77",
            "launcher-maximum-six-rejected",
        ),
        strict=True,
    ):
        require_capture(relative, label, field="launcher settings")
    maximum = by_id["launcher.maximum-rejected"]
    require_capture(
        maximum.get("screenshot"),
        "launcher-maximum-six-rejected",
        field="maximum-six rejection",
    )
    persistence = by_id["launcher.persistence.77"]
    require_capture(
        persistence.get("screenshot"),
        "six-tile-launcher-77",
        field="six-tile persistence",
    )
    routes = by_id["launcher.six-tile-routes"]
    route_paths = routes.get("screenshots")
    if not isinstance(route_paths, dict):
        raise ValidationError("six-tile route screenshot map is invalid")
    for tile in ("music", "stream", "wireless", "system", "about"):
        require_capture(
            route_paths.get(tile),
            f"route-six-77-{tile}",
            field=f"six-tile {tile} route",
        )
    require_capture(
        route_paths.get("cfw"), "six-77-cfw-open", field="six-tile CFW route"
    )
    require_capture(
        routes.get("cfw_restored_screenshot"),
        "six-77-cfw-restored",
        field="six-tile CFW restoration",
    )
    swap = by_id["launcher.ebook-swap"]
    require_capture(
        swap.get("screenshot"),
        "launcher-settings-alternate-six-7d",
        field="eBook launcher swap",
    )
    alternate = by_id["launcher.alternate-six-persistence"]
    require_capture(
        alternate.get("launcher_screenshot"),
        "alternate-six-launcher-7d",
        field="alternate six-tile persistence",
    )
    require_capture(
        alternate.get("ebook_route_screenshot"),
        "route-alternate-7d-ebook",
        field="alternate eBook route",
    )
    restart = by_id["launcher.restart-position"]
    require_capture(
        restart.get("screenshot"),
        "alternate-six-after-second-restart",
        field="alternate six-tile deterministic restart",
    )

    featured = document.get("featured_screenshots")
    if not isinstance(featured, dict) or set(featured) != set(FEATURED_SCREENSHOTS):
        raise ValidationError("featured screenshot set is incomplete or unexpected")
    for canonical_name, expected_label in FEATURED_SCREENSHOTS.items():
        item = featured.get(canonical_name)
        if not isinstance(item, dict):
            raise ValidationError(f"featured screenshot metadata is invalid: {canonical_name}")
        if item.get("path") != canonical_name or item.get("label") != expected_label:
            raise ValidationError(f"featured screenshot identity is invalid: {canonical_name}")
        if item.get("width") != FRAMEBUFFER_WIDTH or item.get("height") != FRAMEBUFFER_HEIGHT:
            raise ValidationError(f"featured screenshot geometry is invalid: {canonical_name}")
        source_relative = item.get("source")
        if not isinstance(source_relative, str) or source_relative not in records:
            raise ValidationError(f"featured screenshot source is missing: {canonical_name}")
        if records[source_relative].get("label") != expected_label:
            raise ValidationError(f"featured screenshot label is stale: {canonical_name}")
        if canonical_name == "default-compact-launcher.png" and source_relative != by_id[
            "default.launcher.71"
        ].get("screenshot"):
            raise ValidationError("featured default launcher is not bound to its PASS check")
        if canonical_name == "six-tile-launcher.png" and source_relative != persistence.get(
            "screenshot"
        ):
            raise ValidationError("featured six-tile launcher is not bound to its PASS check")
        canonical = evidence_file(
            evidence_root, item.get("path"), label="featured screenshot"
        )
        validate_framebuffer_png(canonical, label="featured screenshot")
        actual_digest = sha256_file(canonical)
        if (
            item.get("png_sha256") != actual_digest
            or screenshot_digests[source_relative] != actual_digest
        ):
            raise ValidationError(f"featured screenshot digest mismatch: {canonical_name}")


def validate_ssh_logs(document: dict[str, object], evidence_root: Path) -> None:
    checks = document.get("checks")
    run_id = document.get("run_id")
    if not isinstance(checks, list) or not isinstance(run_id, str):
        raise ValidationError("SSH evidence lacks checks or a run identity")
    matching = [
        item
        for item in checks
        if isinstance(item, dict) and item.get("id") == "ssh.runtime-auth"
    ]
    if len(matching) != 1:
        raise ValidationError("SSH runtime evidence is missing or duplicated")
    runtime = matching[0]

    controller = runtime.get("controller")
    expected_commands = [
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
    ]
    if not isinstance(controller, dict) or (
        controller.get("target_busybox_qemu") is not True
        or controller.get("enable_disable_persisted") is not True
        or controller.get("direct_toggle") is not True
        or controller.get("self_reexec_adapter") is not False
        or controller.get("commands") != expected_commands
        or controller.get("run_id") != run_id
    ):
        raise ValidationError("SSH controller target-runtime proof is invalid")
    controller_log = current_run_log(
        evidence_root,
        controller.get("log"),
        run_id,
        label="SSH controller log",
    )
    if controller.get("log_sha256") != sha256_file(controller_log):
        raise ValidationError("SSH controller log digest mismatch")
    controller_text = controller_log.read_text(encoding="utf-8", errors="replace")
    for marker in (
        "SSH is disabled",
        "disabled",
        "SSH is enabled; Dropbear will follow the wlan0 IPv4 address",
        "enabled",
        "SSH toggle is enabled",
        "Dropbear is stopped",
    ):
        if marker not in controller_text:
            raise ValidationError("SSH controller log lacks target-runtime markers")
    trials = {
        "publickey": ("publickey", True, "Pubkey auth succeeded for 'root'"),
        "password": ("password", True, "Password auth succeeded for 'root'"),
        "unknown_key_rejection": ("publickey", False, "Pubkey auth succeeded for 'root'"),
        "wrong_password_rejection": ("password", False, "Password auth succeeded for 'root'"),
    }
    expected_trial_names = {
        "publickey": "publickey",
        "password": "password",
        "unknown_key_rejection": "unknown-key",
        "wrong_password_rejection": "wrong-password",
    }
    for field, (method, expected_authenticated, server_marker) in trials.items():
        result = runtime.get(field)
        trial = expected_trial_names[field]
        if not isinstance(result, dict) or (
            result.get("trial") != trial
            or result.get("method") != method
            or result.get("expected_authenticated") is not expected_authenticated
            or result.get("run_id") != run_id
        ):
            raise ValidationError(f"SSH {trial} trial metadata is invalid")
        server = current_run_log(
            evidence_root,
            result.get("server_log"),
            run_id,
            label=f"SSH {trial} server log",
        )
        client = current_run_log(
            evidence_root,
            result.get("client_log"),
            run_id,
            label=f"SSH {trial} client log",
        )
        if (
            result.get("server_log_sha256") != sha256_file(server)
            or result.get("client_log_sha256") != sha256_file(client)
        ):
            raise ValidationError(f"SSH {trial} log digest mismatch")
        server_text = server.read_text(encoding="utf-8", errors="replace")
        client_text = client.read_text(encoding="utf-8", errors="replace")
        client_marker = f'using "{method}"'
        if expected_authenticated:
            proof_token = f"r1-cfw-{trial}-root-session"
            if (
                result.get("server_authenticated") is not True
                or result.get("client_authenticated") is not True
                or result.get("root_command_executed") is not True
                or result.get("root_uid") != 0
                or result.get("filesystem_proof") != proof_token
                or server_marker not in server_text
                or client_marker not in client_text
                or "R1_UID=0" not in client_text
                or f"R1_FS={proof_token}" not in client_text
                or "Exit status 0" not in client_text
            ):
                raise ValidationError(f"SSH {trial} root-session evidence is invalid")
        elif (
            result.get("server_authenticated") is not False
            or result.get("client_authenticated") is not False
            or result.get("client_rejected") is not True
            or server_marker in server_text
            or client_marker in client_text
            or "Permission denied" not in client_text
        ):
            raise ValidationError(f"SSH {trial} rejection evidence is invalid")


def verify_manifest(
    path: Path,
    expected_rootfs_sha256: str,
    candidate_rootfs_image: Path,
) -> dict[str, object]:
    """Return a valid manifest after binding it to the candidate rootfs."""

    if not SHA256_RE.fullmatch(expected_rootfs_sha256):
        raise ValidationError(
            "expected candidate rootfs SHA-256 must be 64 lowercase hex digits"
        )
    if path.is_symlink() or not path.is_file():
        raise ValidationError(f"QEMU validation manifest is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"invalid QEMU validation manifest: {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValidationError("QEMU validation manifest root must be an object")

    schema_version = document.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValidationError("QEMU validation manifest schema_version must be integer 1")
    if document.get("status") != "PASS":
        raise ValidationError("QEMU validation status is not PASS")

    actual_rootfs_sha256 = document.get("candidate_rootfs_sha256")
    if not isinstance(actual_rootfs_sha256, str) or not SHA256_RE.fullmatch(
        actual_rootfs_sha256
    ):
        raise ValidationError(
            "candidate_rootfs_sha256 must be 64 lowercase hex digits"
        )
    if actual_rootfs_sha256 != expected_rootfs_sha256:
        raise ValidationError(
            "QEMU evidence belongs to a different candidate rootfs: "
            f"manifest={actual_rootfs_sha256}, candidate={expected_rootfs_sha256}"
        )
    if document.get("tool") != "tools/cfw_validate.py":
        raise ValidationError("QEMU validation manifest has an unexpected tool identity")
    if document.get("qemu_scope") != QEMU_SCOPE:
        raise ValidationError("QEMU validation manifest has an unexpected scope")
    if document.get("hardware_tested") is not False:
        raise ValidationError("QEMU validation manifest must set hardware_tested to false")
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not SAFE_NAME_RE.fullmatch(run_id):
        raise ValidationError("QEMU validation manifest has an invalid run_id")
    component_hashes = squashfs_component_hashes(
        candidate_rootfs_image, expected_rootfs_sha256
    )
    validate_checks(document, expected_rootfs_sha256, component_hashes)
    evidence_root = path.parent.resolve()
    validate_screenshots(document, evidence_root)
    validate_ssh_logs(document, evidence_root)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("expected_rootfs_sha256")
    parser.add_argument("candidate_rootfs_image", type=Path)
    args = parser.parse_args(argv)
    try:
        verify_manifest(
            args.manifest,
            args.expected_rootfs_sha256,
            args.candidate_rootfs_image,
        )
    except ValidationError as error:
        print(f"validation gate failed: {error}", file=sys.stderr)
        return 1
    print(f"verified QEMU validation gate: {args.manifest}")
    print(f"candidate rootfs sha256: {args.expected_rootfs_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
