#!/usr/bin/env python3
"""Inspect and unpack HiBy R1 .upt firmware containers.

The format observed in firmware 1.6 is an ISO9660 image whose payload files are
split into 512 KiB pieces.  The MD5 of every piece is part of its filename.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


CHUNK_RE = re.compile(r"^(?P<image>.+)\.(?P<index>\d{4})\.(?P<md5>[0-9a-f]{32})$")
CHUNK_SIZE = 512 * 1024


def source_date_epoch() -> int | None:
    value = os.environ.get("SOURCE_DATE_EPOCH")
    if value is None:
        return None
    try:
        epoch = int(value)
    except ValueError as error:
        raise RuntimeError("SOURCE_DATE_EPOCH must be an integer") from error
    if epoch < 0:
        raise RuntimeError("SOURCE_DATE_EPOCH must not be negative")
    return epoch


def md5_file(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - this is the vendor's integrity format
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_program(explicit: Path | None, name: str) -> str:
    if explicit is not None:
        if not explicit.is_file():
            raise RuntimeError(f"{name} not found: {explicit}")
        return str(explicit.resolve())
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"{name} is required (or pass --{name})")
    return found


def refuse_overwrite(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise RuntimeError(f"output exists (use --force): {path}")


def extract_iso(source: Path, destination: Path) -> None:
    seven_zip = shutil.which("7z")
    if seven_zip is None:
        raise RuntimeError("7z is required to extract the ISO9660 .upt container")
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [seven_zip, "x", "-y", f"-o{destination}", str(source)], check=True
    )


def find_images(ota_dir: Path) -> dict[str, list[tuple[int, Path, str]]]:
    images: dict[str, list[tuple[int, Path, str]]] = {}
    for path in ota_dir.iterdir():
        match = CHUNK_RE.match(path.name)
        if match:
            images.setdefault(match["image"], []).append(
                (int(match["index"]), path, match["md5"])
            )
    for chunks in images.values():
        chunks.sort(key=lambda item: item[0])
    return images


def rebuild_image(
    name: str, chunks: list[tuple[int, Path, str]], destination: Path
) -> tuple[int, str]:
    expected_indexes = list(range(len(chunks)))
    actual_indexes = [item[0] for item in chunks]
    if actual_indexes != expected_indexes:
        raise RuntimeError(f"{name}: non-contiguous chunks: {actual_indexes}")

    manifest_paths = sorted(chunks[0][1].parent.glob(f"ota_md5_{name}.*"))
    if len(manifest_paths) != 1:
        raise RuntimeError(f"{name}: expected one ota_md5 manifest")
    manifest = manifest_paths[0]
    overall_expected = manifest.name.rsplit(".", 1)[-1]
    chunk_hashes = manifest.read_text(encoding="ascii").splitlines()
    if len(chunk_hashes) != len(chunks):
        raise RuntimeError(
            f"{name}: {len(chunk_hashes)} manifest hashes for {len(chunks)} chunks"
        )

    overall = hashlib.md5()  # noqa: S324 - this is the vendor's integrity format
    with destination.open("wb") as output:
        previous_md5 = overall_expected
        for (index, path, filename_md5), expected_md5 in zip(chunks, chunk_hashes):
            if filename_md5 != previous_md5:
                raise RuntimeError(
                    f"{name} chunk {index}: filename chain {filename_md5} "
                    f"!= {previous_md5}"
                )
            data = path.read_bytes()
            actual_md5 = hashlib.md5(data).hexdigest()  # noqa: S324
            if actual_md5 != expected_md5:
                raise RuntimeError(
                    f"{name} chunk {index}: {actual_md5} != {expected_md5}"
                )
            output.write(data)
            overall.update(data)
            previous_md5 = actual_md5
    overall_actual = overall.hexdigest()
    if overall_actual != overall_expected:
        raise RuntimeError(f"{name}: overall {overall_actual} != {overall_expected}")
    print(
        f"rebuilt {name}: {destination.stat().st_size} bytes, "
        f"md5={overall_actual}, chunks={len(chunks)}"
    )
    return destination.stat().st_size, overall_actual


def parse_ota_metadata(path: Path) -> dict[str, tuple[int, str]]:
    images: dict[str, tuple[int, str]] = {}
    current: dict[str, str] = {}
    for raw_line in path.read_text(encoding="ascii").splitlines() + [""]:
        line = raw_line.strip()
        if line and "=" in line:
            key, value = line.split("=", 1)
            if key == "img_type":
                if current:
                    raise RuntimeError(f"incomplete image entry in {path}")
                current[key] = value
            elif current:
                current[key] = value
            continue
        if current:
            required = {"img_type", "img_name", "img_size", "img_md5"}
            if not required.issubset(current):
                raise RuntimeError(f"incomplete image entry in {path}: {current}")
            name = current["img_name"]
            if name in images:
                raise RuntimeError(f"duplicate image entry in {path}: {name}")
            try:
                size = int(current["img_size"])
            except ValueError as error:
                raise RuntimeError(f"invalid image size in {path}: {current}") from error
            digest = current["img_md5"]
            if not re.fullmatch(r"[0-9a-f]{32}", digest):
                raise RuntimeError(f"invalid image MD5 in {path}: {digest}")
            images[name] = (size, digest)
            current = {}
    if not images:
        raise RuntimeError(f"no image metadata in {path}")
    return images


def split_image(image: Path, ota_dir: Path, name: str) -> tuple[int, str]:
    overall_md5 = md5_file(image)
    manifest = ota_dir / f"ota_md5_{name}.{overall_md5}"
    previous_md5 = overall_md5
    hashes: list[str] = []
    with image.open("rb") as source:
        index = 0
        while chunk := source.read(CHUNK_SIZE):
            chunk_md5 = hashlib.md5(chunk).hexdigest()  # noqa: S324
            (ota_dir / f"{name}.{index:04d}.{previous_md5}").write_bytes(chunk)
            hashes.append(chunk_md5)
            previous_md5 = chunk_md5
            index += 1
    manifest.write_text("\n".join(hashes) + "\n", encoding="ascii", newline="\n")
    return image.stat().st_size, overall_md5


def prepare_ota_tree(
    ximage: Path, rootfs: Path, destination: Path, ota_version: int
) -> None:
    ota_dir = destination / f"ota_v{ota_version}"
    ota_dir.mkdir(parents=True)
    x_size, x_md5 = split_image(ximage, ota_dir, "xImage")
    r_size, r_md5 = split_image(rootfs, ota_dir, "rootfs.squashfs")
    (ota_dir / "ota_update.in").write_text(
        "\n".join(
            [
                f"ota_version={ota_version}",
                "",
                "img_type=kernel",
                "img_name=xImage",
                f"img_size={x_size}",
                f"img_md5={x_md5}",
                "",
                "img_type=rootfs",
                "img_name=rootfs.squashfs",
                f"img_size={r_size}",
                f"img_md5={r_md5}",
                "",
                "",
            ]
        ),
        encoding="ascii",
        newline="\n",
    )
    (ota_dir / f"ota_v{ota_version}.ok").write_text(
        "\n", encoding="ascii", newline="\n"
    )
    (destination / "ota_config.in").write_text(
        f"current_version={ota_version}\n", encoding="ascii", newline="\n"
    )


def inspect_uimage(path: Path) -> None:
    raw = path.read_bytes()
    if len(raw) < 64:
        raise RuntimeError(f"uImage is too small: {path}")
    fields = struct.unpack(">7I4B32s", raw[:64])
    magic, header_crc, timestamp, size, load, entry, data_crc = fields[:7]
    os_id, arch_id, type_id, compression_id, name_raw = fields[7:]
    if magic != 0x27051956:
        raise RuntimeError(f"bad uImage magic: 0x{magic:08x}")
    header_for_crc = bytearray(raw[:64])
    header_for_crc[4:8] = b"\0\0\0\0"
    actual_header_crc = binascii.crc32(header_for_crc) & 0xFFFFFFFF
    payload = raw[64 : 64 + size]
    if len(payload) != size:
        raise RuntimeError(f"truncated uImage payload: {len(payload)} != {size}")
    actual_data_crc = binascii.crc32(payload) & 0xFFFFFFFF
    name = name_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
    print(f"uImage name: {name}")
    print(f"timestamp: {timestamp}")
    print(f"payload: {size} bytes")
    print(f"load/entry: 0x{load:08x}/0x{entry:08x}")
    print(f"os/arch/type/compression: {os_id}/{arch_id}/{type_id}/{compression_id}")
    print(
        f"header CRC32: {actual_header_crc:08x} "
        f"({'ok' if actual_header_crc == header_crc else 'BAD'})"
    )
    print(
        f"data CRC32: {actual_data_crc:08x} "
        f"({'ok' if actual_data_crc == data_crc else 'BAD'})"
    )
    if actual_header_crc != header_crc or actual_data_crc != data_crc:
        raise RuntimeError("uImage CRC check failed")


def jpeg_dimensions(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError(f"not a JPEG: {path}")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            break
        segment_size = struct.unpack(">H", data[position : position + 2])[0]
        if segment_size < 2 or position + segment_size > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2}:
            precision = data[position + 2]
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            return width, height, precision
        position += segment_size
    raise RuntimeError(f"JPEG has no supported SOF marker: {path}")


def png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise RuntimeError(f"invalid PNG header: {path}")
    return struct.unpack(">II", header[16:24])


def command_unpack(args: argparse.Namespace) -> None:
    source = args.firmware.resolve()
    output = args.output.resolve()
    if not source.is_file():
        raise RuntimeError(f"firmware not found: {source}")
    refuse_overwrite(output, args.force)
    if output.exists():
        shutil.rmtree(output)
    iso_dir = output / "iso"
    images_dir = output / "images"
    extract_iso(source, iso_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    ota_dirs = sorted(path for path in iso_dir.glob("ota_v*") if path.is_dir())
    if not ota_dirs:
        raise RuntimeError("no ota_v* payload directory found")
    for ota_dir in ota_dirs:
        print(f"payload: {ota_dir.name}")
        metadata = parse_ota_metadata(ota_dir / "ota_update.in")
        chunked_images = find_images(ota_dir)
        if set(chunked_images) != set(metadata):
            raise RuntimeError(
                f"payload/metadata image mismatch: "
                f"{sorted(chunked_images)} != {sorted(metadata)}"
            )
        for name, chunks in sorted(chunked_images.items()):
            actual = rebuild_image(name, chunks, images_dir / name)
            if actual != metadata[name]:
                raise RuntimeError(
                    f"{name}: rebuilt size/MD5 {actual} != metadata {metadata[name]}"
                )


def command_extract_rootfs(args: argparse.Namespace) -> None:
    destination = args.output.resolve()
    refuse_overwrite(destination, args.force)
    if destination.exists():
        shutil.rmtree(destination)
    unsquashfs = find_program(args.unsquashfs, "unsquashfs")
    subprocess.run(
        [unsquashfs, "-d", str(destination), str(args.rootfs.resolve())], check=True
    )


def command_build_rootfs(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    refuse_overwrite(output, args.force)
    output.parent.mkdir(parents=True, exist_ok=True)
    mksquashfs = find_program(args.mksquashfs, "mksquashfs")
    subprocess.run(
        [
            mksquashfs,
            str(args.root.resolve()),
            str(output),
            "-noappend",
            "-comp",
            "lzo",
            "-b",
            "131072",
            "-all-root",
        ],
        check=True,
    )
    print(f"rootfs: {output.stat().st_size} bytes, md5={md5_file(output)}")


def command_pack(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    refuse_overwrite(output, args.force)
    if args.ota_version < 0:
        raise RuntimeError("ota-version must be zero or greater")
    for label, path in (("xImage", args.ximage), ("rootfs", args.rootfs)):
        if not path.is_file():
            raise RuntimeError(f"{label} not found: {path}")
    inspect_uimage(args.ximage.resolve())
    with args.rootfs.open("rb") as stream:
        if stream.read(4) != b"hsqs":
            raise RuntimeError(f"rootfs is not little-endian SquashFS: {args.rootfs}")
    output.parent.mkdir(parents=True, exist_ok=True)
    genisoimage = find_program(args.genisoimage, "genisoimage")
    with tempfile.TemporaryDirectory(prefix="r1fw-") as temporary:
        iso_root = Path(temporary) / "iso"
        prepare_ota_tree(
            args.ximage.resolve(), args.rootfs.resolve(), iso_root, args.ota_version
        )
        epoch = source_date_epoch()
        if epoch is not None:
            for path in [iso_root, *sorted(iso_root.rglob("*"))]:
                os.utime(path, (epoch, epoch), follow_symlinks=False)
        command = [
            genisoimage,
            "-quiet",
            "-f",
            "-no-cache-inodes",
            "-U",
            "-J",
            "-joliet-long",
            "-r",
            "-allow-lowercase",
            "-allow-multidot",
            "-V",
            "CDROM",
            "-o",
            str(output),
            str(iso_root),
        ]
        if epoch is not None:
            command[1:1] = ["-creation-date", str(epoch)]
        subprocess.run(command, check=True)
    print(f"firmware: {output.stat().st_size} bytes")
    print(f"md5: {md5_file(output)}")
    print(f"sha256: {sha256_file(output)}")


def command_inspect_kernel(args: argparse.Namespace) -> None:
    inspect_uimage(args.ximage.resolve())


def command_apply_overlay(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    overlay = args.overlay.resolve()
    if not root.is_dir() or not overlay.is_dir():
        raise RuntimeError("root and overlay must both be directories")
    copied = 0
    for source in sorted(overlay.rglob("*")):
        relative = source.relative_to(overlay)
        destination = root / relative
        if source.is_dir():
            created = not destination.exists()
            destination.mkdir(parents=True, exist_ok=True)
            if created:
                destination.chmod(source.stat().st_mode & 0o7777)
        elif source.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            destination.symlink_to(source.readlink())
            copied += 1
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
            print(relative)
    print(f"applied {copied} overlay files")


def rootfs_manifest(root: Path) -> dict[str, tuple[str, int, str]]:
    """Return stable type/mode/content records for an extracted rootfs."""
    manifest: dict[str, tuple[str, int, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            record = ("symlink", mode, str(path.readlink()))
        elif stat.S_ISREG(metadata.st_mode):
            record = ("file", mode, sha256_file(path))
        elif stat.S_ISDIR(metadata.st_mode):
            record = ("directory", mode, "")
        else:
            record = ("special", mode, f"rdev={metadata.st_rdev}")
        manifest[relative] = record
    return manifest


def command_diff_rootfs(args: argparse.Namespace) -> None:
    before = rootfs_manifest(args.before.resolve())
    after = rootfs_manifest(args.after.resolve())
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    changed = sorted(path for path in before.keys() & after.keys() if before[path] != after[path])

    for heading, prefix, paths, records in (
        ("added", "+", added, after),
        ("removed", "-", removed, before),
        ("changed", "~", changed, after),
    ):
        print(f"{heading}: {len(paths)}")
        for path in paths:
            kind, mode, detail = records[path]
            suffix = f" -> {detail}" if kind == "symlink" else ""
            print(f"  {prefix} {path} ({kind} {mode:04o}){suffix}")

    if args.strict:
        expected_added = set(args.expect_added)
        expected_removed = set(args.expect_removed)
        expected_changed = set(args.expect_changed)
        actual = (set(added), set(removed), set(changed))
        expected = (expected_added, expected_removed, expected_changed)
        if actual != expected:
            raise RuntimeError(
                "rootfs diff does not match the strict expected change set"
            )


def command_install_splash(args: argparse.Namespace) -> None:
    source = args.jpeg.resolve()
    root = args.root.resolve()
    width, height, precision = jpeg_dimensions(source)
    if (width, height) != (480, 800) or precision != 8:
        raise RuntimeError(
            f"splash must be an 8-bit 480x800 JPEG; got {width}x{height}, {precision}-bit"
        )
    etc = root / "etc"
    if not etc.is_dir():
        raise RuntimeError(f"not an extracted rootfs: {root}")
    for name in ("logo.jpeg", "logo1.jpeg", "logo2.jpeg"):
        destination = etc / name
        if not destination.exists():
            raise RuntimeError(f"expected stock splash is missing: {destination}")
        shutil.copy2(source, destination)
        destination.chmod(0o755)
        print(destination)


def command_export_assets(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    output = args.output.resolve()
    resource = root / "usr/resource"
    logos = [root / "etc" / name for name in ("logo.jpeg", "logo1.jpeg", "logo2.jpeg")]
    if not resource.is_dir() or not all(path.is_file() for path in logos):
        raise RuntimeError("rootfs is missing /usr/resource or the stock splash files")
    refuse_overwrite(output, args.force)
    if output.exists():
        shutil.rmtree(output)
    (output / "splash").mkdir(parents=True)
    for logo in logos:
        shutil.copy2(logo, output / "splash" / logo.name)
    shutil.copytree(resource, output / "resource", symlinks=True)

    lines = ["path\ttype\tsize\tgeometry\tsha256-or-target"]
    raster_count = 0
    for path in sorted(output.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(output).as_posix()
        if path.is_symlink():
            lines.append(f"{relative}\tsymlink\t0\t\t{path.readlink()}")
            continue
        geometry = ""
        suffix = path.suffix.lower()
        if suffix == ".png":
            width, height = png_dimensions(path)
            geometry = f"{width}x{height}"
            raster_count += 1
        elif suffix in {".jpg", ".jpeg"}:
            width, height, precision = jpeg_dimensions(path)
            geometry = f"{width}x{height}x{precision}"
            raster_count += 1
        lines.append(
            f"{relative}\tfile\t{path.stat().st_size}\t{geometry}\t{sha256_file(path)}"
        )
    (output / "manifest.tsv").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"exported {len(lines) - 1} files/links ({raster_count} raster images) to {output}")


def add_force(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--force", action="store_true", help="replace output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    unpack = subparsers.add_parser("unpack", help="extract ISO and rebuild images")
    unpack.add_argument("firmware", type=Path)
    unpack.add_argument("output", type=Path)
    add_force(unpack)
    unpack.set_defaults(func=command_unpack)

    rootfs_extract = subparsers.add_parser(
        "extract-rootfs", help="extract a SquashFS tree with metadata"
    )
    rootfs_extract.add_argument("rootfs", type=Path)
    rootfs_extract.add_argument("output", type=Path)
    rootfs_extract.add_argument("--unsquashfs", type=Path)
    add_force(rootfs_extract)
    rootfs_extract.set_defaults(func=command_extract_rootfs)

    rootfs_build = subparsers.add_parser(
        "build-rootfs", help="build the R1's LZO SquashFS image"
    )
    rootfs_build.add_argument("root", type=Path)
    rootfs_build.add_argument("output", type=Path)
    rootfs_build.add_argument("--mksquashfs", type=Path)
    add_force(rootfs_build)
    rootfs_build.set_defaults(func=command_build_rootfs)

    pack = subparsers.add_parser("pack", help="build a complete .upt container")
    pack.add_argument("--ximage", type=Path, required=True)
    pack.add_argument("--rootfs", type=Path, required=True)
    pack.add_argument("--output", "-o", type=Path, required=True)
    pack.add_argument("--ota-version", type=int, default=0)
    pack.add_argument("--genisoimage", type=Path)
    add_force(pack)
    pack.set_defaults(func=command_pack)

    kernel = subparsers.add_parser(
        "inspect-kernel", help="decode and verify the U-Boot uImage header"
    )
    kernel.add_argument("ximage", type=Path)
    kernel.set_defaults(func=command_inspect_kernel)

    overlay = subparsers.add_parser(
        "apply-overlay", help="copy a rootfs overlay into an extracted tree"
    )
    overlay.add_argument("root", type=Path)
    overlay.add_argument("overlay", type=Path)
    overlay.set_defaults(func=command_apply_overlay)

    diff = subparsers.add_parser(
        "diff-rootfs", help="compare two extracted rootfs trees"
    )
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--strict", action="store_true")
    diff.add_argument("--expect-added", action="append", default=[])
    diff.add_argument("--expect-removed", action="append", default=[])
    diff.add_argument("--expect-changed", action="append", default=[])
    diff.set_defaults(func=command_diff_rootfs)

    splash = subparsers.add_parser(
        "install-splash", help="replace all three boot splash variants"
    )
    splash.add_argument("root", type=Path)
    splash.add_argument("jpeg", type=Path)
    splash.set_defaults(func=command_install_splash)

    assets = subparsers.add_parser(
        "export-assets", help="export stock splash and UI resources with a manifest"
    )
    assets.add_argument("root", type=Path)
    assets.add_argument("output", type=Path)
    add_force(assets)
    assets.set_defaults(func=command_export_assets)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
