#!/usr/bin/env python3
"""Compatibility wrapper for reproducible ISO creation on older cdrkit.

Ubuntu 24.04's genisoimage predates the Debian ``-creation-date`` patch. The
firmware packer intentionally passes that option when SOURCE_DATE_EPOCH is set.
This wrapper removes only that unsupported pair, runs the real cdrkit binary,
and atomically normalizes the ISO9660 creation, modification, and effective
volume-descriptor timestamps. ``r1fw.py`` then performs its existing scoped
Rock Ridge TF normalization.
"""
from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ISO_SECTOR_SIZE = 2048
VOLUME_DATE_OFFSETS = (813, 830, 864)


class CompatibilityError(RuntimeError):
    """The invocation or generated ISO is not safe to normalize."""


def split_arguments(arguments: list[str]) -> tuple[list[str], int | None, Path | None]:
    filtered: list[str] = []
    epoch: int | None = None
    output: Path | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-creation-date":
            if index + 1 >= len(arguments) or epoch is not None:
                raise CompatibilityError("invalid or repeated -creation-date option")
            try:
                epoch = int(arguments[index + 1])
            except ValueError as error:
                raise CompatibilityError("creation date must be an integer epoch") from error
            if epoch < 0:
                raise CompatibilityError("creation date must not be negative")
            index += 2
            continue
        filtered.append(argument)
        if argument == "-o":
            if index + 1 >= len(arguments):
                raise CompatibilityError("missing output after -o")
            output = Path(arguments[index + 1])
            filtered.append(arguments[index + 1])
            index += 2
            continue
        index += 1
    if epoch is not None and output is None:
        raise CompatibilityError("-creation-date requires an ISO output path")
    return filtered, epoch, output


def iso_long_timestamp(epoch: int) -> bytes:
    moment = dt.datetime.fromtimestamp(epoch, tz=dt.timezone.utc)
    if not 1900 <= moment.year <= 2155:
        raise CompatibilityError("creation date is outside the ISO9660 year range")
    return moment.strftime("%Y%m%d%H%M%S00").encode("ascii") + b"\x00"


def normalize_volume_descriptors(path: Path, epoch: int) -> int:
    if path.is_symlink() or not path.is_file():
        raise CompatibilityError(f"ISO output is not a regular file: {path}")
    image = bytearray(path.read_bytes())
    timestamp = iso_long_timestamp(epoch)
    normalized = 0
    sector = 16
    saw_terminator = False
    while (sector + 1) * ISO_SECTOR_SIZE <= len(image):
        offset = sector * ISO_SECTOR_SIZE
        descriptor_type = image[offset]
        if image[offset + 1 : offset + 6] != b"CD001" or image[offset + 6] != 1:
            raise CompatibilityError(
                f"invalid ISO9660 volume descriptor at sector {sector}"
            )
        if descriptor_type in (1, 2):
            for relative in VOLUME_DATE_OFFSETS:
                start = offset + relative
                image[start : start + len(timestamp)] = timestamp
            normalized += 1
        if descriptor_type == 255:
            saw_terminator = True
            break
        sector += 1
    if not saw_terminator or normalized == 0:
        raise CompatibilityError("ISO9660 volume descriptor set is incomplete")

    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(image)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return normalized


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        filtered, epoch, output = split_arguments(arguments)
        real = Path(os.environ.get("R1_REAL_GENISOIMAGE", "/usr/bin/genisoimage"))
        if real.is_symlink():
            real = real.resolve()
        if not real.is_file() or not os.access(real, os.X_OK):
            raise CompatibilityError(f"real genisoimage is unavailable: {real}")
        result = subprocess.run([str(real), *filtered], check=False)
        if result.returncode != 0:
            return result.returncode
        if epoch is not None:
            assert output is not None
            normalize_volume_descriptors(output.resolve(), epoch)
    except (CompatibilityError, OSError) as error:
        print(f"genisoimage compatibility error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
