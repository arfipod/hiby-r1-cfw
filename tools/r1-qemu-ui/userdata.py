#!/usr/bin/env python3
"""Inspect and conservatively patch the HiBy R1 firmware 1.6 user profile.

Despite its name, ``/usr/data/user.ini`` is a fixed-size binary structure.
Firmware 1.6 reads and writes the complete structure with one ``fread`` or
``fwrite``; it does not append a checksum.  This module deliberately knows
only the fields established from the proprietary player's accessors and
preserves every other byte.

The factory image contains schema value zero.  The player fills that template
with the current date-coded schema after loading it.  Both exact schemas are
accepted as inputs, and a prepared profile is always stamped with the current
schema so the firmware does not run its legacy migration path.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


USER_DATA_SIZE = 2768
MAGIC_OFFSET = 0
SCHEMA_OFFSET = 4
LANGUAGE_OFFSET = 8
SLEEP_TIME_OFFSET = 12
IDLE_SHUTDOWN_TIME_OFFSET = 20
LANGUAGE_ONBOARDING_PENDING_OFFSET = 608
IDLE_SHUTDOWN_ENABLED_OFFSET = 620
SLEEP_SHUTDOWN_ENABLED_OFFSET = 624
ONBOARDING_COMPLETE_OFFSET = 1796
REGION_CODE_OFFSET = 2068
REGION_CODE_SIZE = 4
TIME_ZONE_OFFSET = 2072
TIME_ZONE_SIZE = 64

QEMU_REGION_CODE = "US"
QEMU_TIME_ZONE = "America/New_York"

MAGIC = 0x000000FF
FACTORY_SCHEMA = 0x00000000
CURRENT_SCHEMA = 0x0134FE6E  # Decimal 20250222; a schema/build date, not a CRC.
SUPPORTED_SOURCE_SCHEMAS = frozenset((FACTORY_SCHEMA, CURRENT_SCHEMA))

LANGUAGE_ENGLISH = 2

_U32 = struct.Struct("<I")


class UserDataError(ValueError):
    """Raised when a user.ini blob is not safe for this firmware-specific tool."""


def _read_u32(data: bytes | bytearray, offset: int) -> int:
    return _U32.unpack_from(data, offset)[0]


def _write_u32(data: bytearray, offset: int, value: int) -> None:
    _U32.pack_into(data, offset, value)


def _read_fixed_ascii(data: bytes, offset: int, size: int) -> str:
    return data[offset : offset + size].split(b"\0", 1)[0].decode("ascii", "strict")


def _write_fixed_ascii(data: bytearray, offset: int, size: int, value: str) -> None:
    encoded = value.encode("ascii", "strict")
    if len(encoded) >= size:
        raise UserDataError(f"fixed profile string is too long: {value!r}")
    data[offset : offset + size] = encoded + bytes(size - len(encoded))


@dataclass(frozen=True)
class UserDataProfile:
    """A validated firmware 1.6 user profile with unknown data left opaque."""

    data: bytes

    @classmethod
    def parse(cls, data: bytes, *, require_current_schema: bool = False) -> "UserDataProfile":
        if len(data) != USER_DATA_SIZE:
            raise UserDataError(
                f"user.ini has {len(data)} bytes; firmware 1.6 requires exactly "
                f"{USER_DATA_SIZE}"
            )

        magic = _read_u32(data, MAGIC_OFFSET)
        if magic != MAGIC:
            raise UserDataError(
                f"invalid user.ini magic 0x{magic:08x}; expected 0x{MAGIC:08x}"
            )

        schema = _read_u32(data, SCHEMA_OFFSET)
        allowed = {CURRENT_SCHEMA} if require_current_schema else SUPPORTED_SOURCE_SCHEMAS
        if schema not in allowed:
            expected = ", ".join(f"0x{value:08x}" for value in sorted(allowed))
            raise UserDataError(
                f"unsupported user.ini schema 0x{schema:08x}; expected {expected}"
            )
        return cls(bytes(data))

    @classmethod
    def read(cls, path: Path, *, require_current_schema: bool = False) -> "UserDataProfile":
        try:
            data = path.read_bytes()
        except OSError as error:
            raise UserDataError(f"cannot read {path}: {error}") from error
        return cls.parse(data, require_current_schema=require_current_schema)

    @property
    def schema(self) -> int:
        return _read_u32(self.data, SCHEMA_OFFSET)

    def inspect(self) -> dict[str, Any]:
        """Return only fields whose offsets and meanings are verified."""

        return {
            "size": len(self.data),
            "magic": f"0x{_read_u32(self.data, MAGIC_OFFSET):08x}",
            "schema": f"0x{self.schema:08x}",
            "schema_decimal": self.schema,
            "factory_schema": self.schema == FACTORY_SCHEMA,
            "language": _read_u32(self.data, LANGUAGE_OFFSET),
            "language_name": (
                "english"
                if _read_u32(self.data, LANGUAGE_OFFSET) == LANGUAGE_ENGLISH
                else "unknown-to-this-tool"
            ),
            "language_onboarding_pending": bool(
                _read_u32(self.data, LANGUAGE_ONBOARDING_PENDING_OFFSET)
            ),
            "onboarding_complete": bool(
                _read_u32(self.data, ONBOARDING_COMPLETE_OFFSET)
            ),
            "region_code": _read_fixed_ascii(
                self.data, REGION_CODE_OFFSET, REGION_CODE_SIZE
            ),
            "time_zone": _read_fixed_ascii(
                self.data, TIME_ZONE_OFFSET, TIME_ZONE_SIZE
            ),
            "idle_shutdown": {
                "enabled": bool(
                    _read_u32(self.data, IDLE_SHUTDOWN_ENABLED_OFFSET)
                ),
                "raw_enabled": _read_u32(
                    self.data, IDLE_SHUTDOWN_ENABLED_OFFSET
                ),
                "time_setting": _read_u32(self.data, IDLE_SHUTDOWN_TIME_OFFSET),
            },
            "sleep_shutdown": {
                "enabled": bool(
                    _read_u32(self.data, SLEEP_SHUTDOWN_ENABLED_OFFSET)
                ),
                "raw_enabled": _read_u32(
                    self.data, SLEEP_SHUTDOWN_ENABLED_OFFSET
                ),
                "time_setting": _read_u32(self.data, SLEEP_TIME_OFFSET),
            },
        }

    def prepare_for_qemu(self) -> "UserDataProfile":
        """Complete English onboarding and disable inactivity shutdown modes.

        Timer values remain unchanged because their enum ranges are not needed
        when the corresponding enable fields are zero.  Retaining valid timer
        values also avoids surprising the vendor UI if either mode is enabled
        later.
        """

        patched = bytearray(self.data)
        _write_u32(patched, SCHEMA_OFFSET, CURRENT_SCHEMA)
        _write_u32(patched, LANGUAGE_OFFSET, LANGUAGE_ENGLISH)
        _write_u32(patched, LANGUAGE_ONBOARDING_PENDING_OFFSET, 0)
        _write_u32(patched, IDLE_SHUTDOWN_ENABLED_OFFSET, 0)
        _write_u32(patched, SLEEP_SHUTDOWN_ENABLED_OFFSET, 0)
        # The stock player still opens Language -> Region -> Time zone when
        # the earlier language flag is clear unless the independent first-run
        # completion field and valid region/time-zone strings are present.
        # These offsets and values were established by comparing the profile
        # immediately before and after completing that exact stock path under
        # qemu-user, then confirming that the resulting fields skip onboarding
        # in a fresh runtime.
        _write_u32(patched, ONBOARDING_COMPLETE_OFFSET, 1)
        _write_fixed_ascii(
            patched, REGION_CODE_OFFSET, REGION_CODE_SIZE, QEMU_REGION_CODE
        )
        _write_fixed_ascii(
            patched, TIME_ZONE_OFFSET, TIME_ZONE_SIZE, QEMU_TIME_ZONE
        )
        return UserDataProfile.parse(bytes(patched), require_current_schema=True)


def _atomic_write(path: Path, data: bytes, mode: int = 0o664) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", dir=path.parent, delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
    except OSError as error:
        raise UserDataError(f"cannot write {path}: {error}") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def write_prepared_profile(source: Path, destination: Path) -> UserDataProfile:
    """Prepare a separate profile and atomically write it to ``destination``."""

    source = source.resolve()
    destination = destination.resolve()
    if source == destination:
        raise UserDataError(
            "source and destination are the same; use a disposable output path"
        )
    profile = UserDataProfile.read(source)
    prepared = profile.prepare_for_qemu()
    try:
        source_mode = stat.S_IMODE(source.stat().st_mode)
    except OSError as error:
        raise UserDataError(f"cannot stat {source}: {error}") from error
    _atomic_write(destination, prepared.data, source_mode)
    return prepared


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or prepare a HiBy R1 firmware 1.6 binary user.ini"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="show verified fields")
    inspect_parser.add_argument("profile", type=Path)
    inspect_parser.add_argument(
        "--require-current-schema",
        action="store_true",
        help="reject the schema-zero factory template",
    )

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="write an English profile with idle and sleep shutdown disabled",
    )
    prepare_parser.add_argument("source", type=Path)
    prepare_parser.add_argument("destination", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            profile = UserDataProfile.read(
                args.profile,
                require_current_schema=args.require_current_schema,
            )
            print(json.dumps(profile.inspect(), indent=2, sort_keys=True))
            return 0

        profile = write_prepared_profile(args.source, args.destination)
        print(json.dumps(profile.inspect(), indent=2, sort_keys=True))
        return 0
    except UserDataError as error:
        print(f"userdata.py: error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
