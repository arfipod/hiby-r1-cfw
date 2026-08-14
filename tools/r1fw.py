#!/usr/bin/env python3
"""Inspect and unpack HiBy R1 .upt firmware containers.

The format observed in firmware 1.6 is an ISO9660 image whose payload files are
split into 512 KiB pieces.  The MD5 of every piece is part of its filename.
"""

from __future__ import annotations

import argparse
import binascii
import datetime as dt
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
ISO_SECTOR_SIZE = 2048


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


def _both_endian_u32(data: bytes | bytearray, offset: int, label: str) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise RuntimeError(f"truncated ISO9660 {label}")
    little = int.from_bytes(data[offset : offset + 4], "little")
    big = int.from_bytes(data[offset + 4 : offset + 8], "big")
    if little != big:
        raise RuntimeError(f"inconsistent ISO9660 {label}")
    return little


def _rock_ridge_timestamps(epoch: int) -> tuple[bytes, bytes]:
    moment = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
    if not 1900 <= moment.year <= 2155:
        raise RuntimeError("SOURCE_DATE_EPOCH is outside the ISO9660 year range")
    short = bytes(
        (
            moment.year - 1900,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
            0,
        )
    )
    long = moment.strftime("%Y%m%d%H%M%S00").encode("ascii") + b"\x00"
    return short, long


def normalize_iso_rock_ridge_timestamps(path: Path, epoch: int) -> int:
    """Normalize ISO9660 directory and Rock Ridge timestamps atomically.

    cdrkit records the wall clock in every ISO9660 directory record, including
    the duplicate root record embedded in both the primary and Joliet volume
    descriptors.  It also records host ctime and the atime caused by its own
    reads in Rock Ridge ``TF`` entries.  ``os.utime`` cannot make either class
    reproducible.

    This parser follows only validated directory extents from the primary and
    supplementary volume descriptors.  Rock Ridge System Use areas (including
    SUSP continuations) are parsed only in the primary tree, so payload bytes
    that merely resemble a directory record or ``TF`` entry are never touched.
    """

    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"ISO9660 image is not a regular file: {path}")
    image = bytearray(path.read_bytes())
    short_timestamp, long_timestamp = _rock_ridge_timestamps(epoch)

    descriptors: list[tuple[int, int]] = []
    sector = 16
    saw_terminator = False
    while (sector + 1) * ISO_SECTOR_SIZE <= len(image):
        offset = sector * ISO_SECTOR_SIZE
        descriptor_type = image[offset]
        if image[offset + 1 : offset + 6] != b"CD001" or image[offset + 6] != 1:
            raise RuntimeError(f"invalid ISO9660 volume descriptor at sector {sector}")
        if descriptor_type in (1, 2):
            descriptors.append((descriptor_type, offset))
        if descriptor_type == 255:
            saw_terminator = True
            break
        sector += 1
    if not saw_terminator:
        raise RuntimeError("ISO9660 volume descriptor set is incomplete")
    if not any(kind == 1 for kind, _offset in descriptors):
        raise RuntimeError("ISO9660 primary volume descriptor is missing")

    visited_directories: set[tuple[int, int, int]] = set()
    visited_continuations: set[tuple[int, int]] = set()
    normalized_record_offsets: set[int] = set()
    normalized_tf_offsets: set[int] = set()

    def normalize_recording_date(record_offset: int, record_end: int) -> None:
        timestamp_start = record_offset + 18
        timestamp_end = timestamp_start + len(short_timestamp)
        if record_offset < 0 or timestamp_end > record_end or record_end > len(image):
            raise RuntimeError("truncated ISO9660 recording date")
        image[timestamp_start:timestamp_end] = short_timestamp
        normalized_record_offsets.add(record_offset)

    def normalize_tf(offset: int, end: int) -> None:
        length = image[offset + 2]
        if image[offset + 3] != 1 or length < 5 or offset + length > end:
            raise RuntimeError("invalid Rock Ridge TF entry")
        flags = image[offset + 4]
        timestamp = long_timestamp if flags & 0x80 else short_timestamp
        count = (flags & 0x7F).bit_count()
        required = 5 + count * len(timestamp)
        if required > length:
            raise RuntimeError("truncated Rock Ridge TF timestamp fields")
        cursor = offset + 5
        for _index in range(count):
            image[cursor : cursor + len(timestamp)] = timestamp
            cursor += len(timestamp)
        normalized_tf_offsets.add(offset)

    def normalize_susp(start: int, length: int) -> None:
        if start < 0 or length < 0 or start + length > len(image):
            raise RuntimeError("Rock Ridge continuation is outside the ISO image")
        cursor = start
        end = start + length
        while cursor + 4 <= end:
            signature = bytes(image[cursor : cursor + 2])
            entry_length = image[cursor + 2]
            if signature == b"\x00\x00" and entry_length == 0:
                break
            if entry_length < 4 or cursor + entry_length > end:
                raise RuntimeError("invalid Rock Ridge System Use entry")
            if signature == b"TF":
                normalize_tf(cursor, end)
            elif signature == b"CE":
                if entry_length < 28 or image[cursor + 3] != 1:
                    raise RuntimeError("invalid Rock Ridge CE entry")
                extent = _both_endian_u32(image, cursor + 4, "CE extent")
                continuation_offset = _both_endian_u32(
                    image, cursor + 12, "CE offset"
                )
                continuation_length = _both_endian_u32(
                    image, cursor + 20, "CE length"
                )
                absolute = extent * ISO_SECTOR_SIZE + continuation_offset
                key = (absolute, continuation_length)
                if key not in visited_continuations:
                    visited_continuations.add(key)
                    normalize_susp(absolute, continuation_length)
            cursor += entry_length

    def visit_directory(
        tree_id: int,
        extent: int,
        directory_size: int,
        *,
        rock_ridge: bool,
    ) -> None:
        key = (tree_id, extent, directory_size)
        if key in visited_directories:
            return
        visited_directories.add(key)
        base = extent * ISO_SECTOR_SIZE
        if directory_size <= 0 or base < 0 or base + directory_size > len(image):
            raise RuntimeError("ISO9660 directory extent is outside the image")
        position = 0
        while position < directory_size:
            record_offset = base + position
            record_length = image[record_offset]
            if record_length == 0:
                position = ((position // ISO_SECTOR_SIZE) + 1) * ISO_SECTOR_SIZE
                continue
            record_end = record_offset + record_length
            if record_length < 34 or record_end > base + directory_size:
                raise RuntimeError("invalid ISO9660 directory record")
            normalize_recording_date(record_offset, record_end)
            name_length = image[record_offset + 32]
            name_start = record_offset + 33
            name_end = name_start + name_length
            if name_end > record_end:
                raise RuntimeError("truncated ISO9660 file identifier")
            if rock_ridge:
                system_use_start = name_end + (1 if (33 + name_length) & 1 else 0)
                if system_use_start < record_end:
                    normalize_susp(system_use_start, record_end - system_use_start)

            flags = image[record_offset + 25]
            identifier = bytes(image[name_start:name_end])
            if flags & 0x02 and identifier not in (b"\x00", b"\x01"):
                child_extent = _both_endian_u32(
                    image, record_offset + 2, "directory extent"
                )
                child_size = _both_endian_u32(
                    image, record_offset + 10, "directory size"
                )
                visit_directory(
                    tree_id,
                    child_extent,
                    child_size,
                    rock_ridge=rock_ridge,
                )
            position += record_length

    for tree_id, (descriptor_type, descriptor_offset) in enumerate(descriptors):
        root_offset = descriptor_offset + 156
        if root_offset >= len(image) or image[root_offset] < 34:
            raise RuntimeError("ISO9660 root directory record is invalid")
        root_end = root_offset + image[root_offset]
        if root_end > descriptor_offset + ISO_SECTOR_SIZE:
            raise RuntimeError("ISO9660 root directory record is truncated")
        normalize_recording_date(root_offset, root_end)
        root_extent = _both_endian_u32(image, root_offset + 2, "root extent")
        root_size = _both_endian_u32(image, root_offset + 10, "root size")
        visit_directory(
            tree_id,
            root_extent,
            root_size,
            rock_ridge=descriptor_type == 1,
        )

    if not normalized_record_offsets:
        raise RuntimeError("no ISO9660 directory timestamps were found")
    if not normalized_tf_offsets:
        raise RuntimeError("no Rock Ridge TF timestamps were found")

    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), original_mode)
            output.write(image)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return len(normalized_tf_offsets)


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


def verify_file(
    path: Path, *, expected_sha256: str | None = None, max_size: int | None = None
) -> tuple[int, str]:
    """Verify an exact file digest and/or an inclusive byte-size limit."""

    if expected_sha256 is None and max_size is None:
        raise RuntimeError("verify-file requires --sha256 and/or --max-size")
    if expected_sha256 is not None and not re.fullmatch(
        r"[0-9a-f]{64}", expected_sha256
    ):
        raise RuntimeError("expected SHA-256 must be exactly 64 lowercase hex digits")
    if max_size is not None and max_size < 0:
        raise RuntimeError("maximum size must not be negative")
    if not path.is_file():
        raise RuntimeError(f"file not found: {path}")

    size = path.stat().st_size
    if max_size is not None and size > max_size:
        raise RuntimeError(
            f"file exceeds maximum size: {path}: {size} > {max_size} bytes"
        )

    actual_sha256 = sha256_file(path)
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"SHA-256 mismatch for {path}: "
            f"{actual_sha256} != {expected_sha256}"
        )
    return size, actual_sha256


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
    # unsquashfs applies its inherited umask while recreating inode modes.  The
    # extracted tree is an editable firmware image, so masking (for example)
    # stock 0775/0664 entries to 0755/0644 silently changes the rebuilt rootfs.
    # Clear the mask in the child only; the caller's umask still governs every
    # file that r1fw itself creates.
    subprocess.run(
        [unsquashfs, "-d", str(destination), str(args.rootfs.resolve())],
        check=True,
        umask=0,
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
        environment = None
        if epoch is not None:
            environment = os.environ.copy()
            environment["TZ"] = "UTC"
        subprocess.run(command, check=True, env=environment)
    if epoch is not None:
        normalize_iso_rock_ridge_timestamps(output, epoch)
    print(f"firmware: {output.stat().st_size} bytes")
    print(f"md5: {md5_file(output)}")
    print(f"sha256: {sha256_file(output)}")


def command_inspect_kernel(args: argparse.Namespace) -> None:
    inspect_uimage(args.ximage.resolve())


def command_verify_file(args: argparse.Namespace) -> None:
    path = args.file.resolve()
    size, digest = verify_file(
        path, expected_sha256=args.sha256, max_size=args.max_size
    )
    print(f"verified file: {path}")
    print(f"size: {size} bytes")
    if args.max_size is not None:
        print(f"maximum size: {args.max_size} bytes")
        print(f"headroom: {args.max_size - size} bytes")
    print(f"sha256: {digest}")


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

    verify = subparsers.add_parser(
        "verify-file", help="verify a file digest and/or maximum byte size"
    )
    verify.add_argument("file", type=Path)
    verify.add_argument("--sha256", help="expected 64-digit lowercase SHA-256")
    verify.add_argument(
        "--max-size", type=int, help="inclusive maximum file size in bytes"
    )
    verify.set_defaults(func=command_verify_file)

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
