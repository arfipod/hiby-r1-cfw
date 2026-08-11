#!/usr/bin/env python3
"""Verify the QEMU evidence gate for an R1 CFW release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from pathlib import Path


SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAMEBUFFER_WIDTH = 480
FRAMEBUFFER_HEIGHT = 800
QEMU_SCOPE = "qemu-user application harness; not X1600 board emulation"

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


class ValidationError(RuntimeError):
    """The evidence is absent, malformed, unsuccessful, or stale."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def validate_checks(document: dict[str, object], expected_digest: str) -> None:
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


def validate_screenshots(document: dict[str, object], evidence_root: Path) -> None:
    screenshots = document.get("screenshots")
    if not isinstance(screenshots, list):
        raise ValidationError("QEMU validation screenshots must be an array")
    records: dict[str, dict[str, object]] = {}
    screenshot_digests: dict[str, str] = {}
    for index, item in enumerate(screenshots):
        if not isinstance(item, dict):
            raise ValidationError(f"screenshot record {index} must be an object")
        relative = item.get("path")
        if not isinstance(relative, str) or relative in records:
            raise ValidationError(f"screenshot record {index} has a missing or duplicate path")
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


def verify_manifest(path: Path, expected_rootfs_sha256: str) -> dict[str, object]:
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
    validate_checks(document, expected_rootfs_sha256)
    validate_screenshots(document, path.parent.resolve())
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("expected_rootfs_sha256")
    args = parser.parse_args(argv)
    try:
        verify_manifest(args.manifest, args.expected_rootfs_sha256)
    except ValidationError as error:
        print(f"validation gate failed: {error}", file=sys.stderr)
        return 1
    print(f"verified QEMU validation gate: {args.manifest}")
    print(f"candidate rootfs sha256: {args.expected_rootfs_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
