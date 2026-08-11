#!/usr/bin/env python3
"""Generate, install, and verify the HiBy R1 Retro Handheld resource tree."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import stat
import sys
from pathlib import Path

from retro_theme_integration import build


TARGET_RELATIVE = Path("usr/resource/r1-cfw/themes/retro")


class RetroThemeError(RuntimeError):
    """The source resources or installed theme are incomplete."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise RetroThemeError(f"required directory is missing: {path}")
    return path


def prepare_source(rootfs: Path, destination: Path) -> Path:
    resource = _require_directory(rootfs / "usr/resource")
    light_layout = _require_directory(resource / "layout/theme1")
    light_assets = _require_directory(resource / "litegui/theme1")
    dark_layout = _require_directory(resource / "layout/theme2")
    dark_assets = _require_directory(resource / "litegui/theme2")

    if destination.exists():
        shutil.rmtree(destination)
    for name, layout, assets in (
        ("light", light_layout, light_assets),
        ("dark", dark_layout, dark_assets),
    ):
        target = destination / name
        target.mkdir(parents=True)
        shutil.copytree(layout, target / "layout", symlinks=True)
        shutil.copytree(assets, target / "litegui", symlinks=True)
    return destination


def build_expected(rootfs: Path, work: Path) -> Path:
    source = work / "source"
    package = work / "package"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    prepare_source(rootfs, source)
    build(source, package, None)
    retro = package / "retro"
    _require_directory(retro / "layout")
    _require_directory(retro / "litegui")
    marker = retro / ".r1-theme"
    if marker.read_text(encoding="ascii") != "theme=retro-handheld\nformat=1\n":
        raise RetroThemeError("generated Retro Handheld marker is invalid")
    return retro


def install(rootfs: Path, work: Path) -> Path:
    expected = build_expected(rootfs, work)
    target = rootfs / TARGET_RELATIVE
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise RetroThemeError(f"refusing to replace non-directory theme: {target}")
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(expected, target, symlinks=True)
    return target


def tree_records(root: Path) -> dict[str, tuple[str, int, str]]:
    records: dict[str, tuple[str, int, str]] = {}
    if root.is_symlink() or not root.is_dir():
        raise RetroThemeError(f"theme tree is missing: {root}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            record = ("symlink", mode, str(path.readlink()))
        elif stat.S_ISDIR(metadata.st_mode):
            record = ("directory", mode, "")
        elif stat.S_ISREG(metadata.st_mode):
            record = ("file", mode, sha256_file(path))
        else:
            raise RetroThemeError(f"unsupported theme entry: {path}")
        records[relative] = record
    return records


def verify(rootfs: Path, work: Path) -> Path:
    installed = rootfs / TARGET_RELATIVE
    expected = build_expected(rootfs, work)
    expected_records = tree_records(expected)
    installed_records = tree_records(installed)
    if installed_records != expected_records:
        missing = sorted(expected_records.keys() - installed_records.keys())
        extra = sorted(installed_records.keys() - expected_records.keys())
        changed = sorted(
            path
            for path in expected_records.keys() & installed_records.keys()
            if expected_records[path] != installed_records[path]
        )
        raise RetroThemeError(
            "installed Retro Handheld tree does not match its deterministic "
            f"regeneration; missing={missing[:5]}, extra={extra[:5]}, "
            f"changed={changed[:5]}"
        )
    return expected


def print_expected_paths(retro: Path) -> None:
    _require_directory(retro)
    print("usr/resource/r1-cfw/themes")
    print(TARGET_RELATIVE.as_posix())
    for path in sorted(retro.rglob("*")):
        print((TARGET_RELATIVE / path.relative_to(retro)).as_posix())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="generate and install the theme")
    generate.add_argument("rootfs", type=Path)
    generate.add_argument("work", type=Path)

    verify_parser = commands.add_parser("verify", help="regenerate and compare the theme")
    verify_parser.add_argument("rootfs", type=Path)
    verify_parser.add_argument("work", type=Path)

    paths = commands.add_parser("expected-paths", help="print strict rootfs paths")
    paths.add_argument("retro", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "generate":
            target = install(args.rootfs.resolve(), args.work.resolve())
            print(f"installed deterministic Retro Handheld theme: {target}")
        elif args.command == "verify":
            expected = verify(args.rootfs.resolve(), args.work.resolve())
            print(f"verified deterministic Retro Handheld theme: {expected}")
        else:
            print_expected_paths(args.retro.resolve())
    except (OSError, RetroThemeError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
