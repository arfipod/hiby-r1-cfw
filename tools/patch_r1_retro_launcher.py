#!/usr/bin/env python3
"""Generate Retro Handheld launcher variants for every safe CFW mask."""
from __future__ import annotations

import argparse
import os
import stat
import struct
import sys
import tempfile
from pathlib import Path

import patch_r1_launcher as stock


OUTPUT_MODE = 0o664
DIRECTORY_MODE = 0o755
RETRO_FOREGROUND = "0x171a1a"
RETRO_BACKGROUND = "0xf0eee7"

RETRO = stock.ThemeSpec(
    "retro",
    Path("usr/resource/r1-cfw/themes/retro/layout/launcher/hiby_launcher_apps.view"),
    "",
    "\n",
    RETRO_FOREGROUND,
    RETRO_BACKGROUND,
)


class RetroLauncherError(RuntimeError):
    """The generated theme or launcher output is incomplete."""


def _card_node(tile: stock.TileSpec, width: int) -> stock.Node:
    suffix = "_wide" if width == 480 else ""
    return stock.Node(
        "imageview",
        (),
        (
            ("name", f"retro_tile_{tile.key}"),
            ("img_path", f"launcher\\tile_{tile.icon}{suffix}.png"),
            ("img_focus_path", f"launcher\\tile_{tile.icon}{suffix}_s.png"),
            ("x", 8),
            ("y", 8),
            ("color_mode", "color_565"),
            ("imageview", True),
        ),
    )


def _tile_node(
    tile: stock.TileSpec,
    geometry: tuple[int, int, int, int],
) -> stock.Node:
    x, y, width, height = geometry
    return stock.Node(
        "viewgroup",
        (
            _card_node(tile, width),
            stock._image_node(tile, RETRO, width),
            stock._text_node(tile, RETRO, width),
        ),
        (
            ("name", tile.group_name),
            ("type", "lg_view"),
            ("color", RETRO_BACKGROUND),
            ("focus_color", RETRO_BACKGROUND),
            ("x", x),
            ("y", y),
            ("w", width),
            ("h", height),
            ("viewgroup", True),
        ),
    )


def render_layout(mask: int) -> bytes:
    tiles = stock.selected_tiles(mask)
    geometry, content_height = stock._tile_geometry(len(tiles))
    children: list[stock.Node] = [
        _tile_node(tile, position)
        for tile, position in zip(tiles, geometry, strict=True)
    ]

    row_count = 3 if len(tiles) == 4 else (len(tiles) + 1) // 2
    children.extend(
        stock._horizontal_line(RETRO, index, index * 246)
        for index in range(1, row_count)
    )
    if len(tiles) > 4:
        children.append(stock._vertical_line(RETRO, 1, 0))

    properties: tuple[tuple[str, object], ...] = (
        ("name", "vg_launcher_apps_hiby"),
        ("type", "hgl_view"),
        ("x", 0),
        ("y", 0),
        ("w", 480),
        ("h", content_height),
        ("hglview_x", 0),
        ("hglview_y", 50),
        ("hglview_w", 480),
        ("hglview_h", 750),
        ("scroll_max_y", 50),
        ("scroll_min_y", 50),
        ("scroll_max_x", 0),
        ("scroll_min_x", 0),
        ("color", RETRO_BACKGROUND),
        ("flag", "scroll"),
        ("zorder", -9),
        ("viewgroup", True),
        ("add_layout", True),
    )
    return stock._document(
        stock.Node("viewgroup", tuple(children), properties), RETRO.newline
    )


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise RetroLauncherError(f"invalid PNG launcher asset: {path}")
    return struct.unpack(">II", header[16:24])


def verify_assets(rootfs: Path) -> None:
    retro = rootfs / "usr/resource/r1-cfw/themes/retro"
    marker = retro / ".r1-theme"
    if marker.is_symlink() or not marker.is_file():
        raise RetroLauncherError("Retro Handheld marker is missing")
    if marker.read_text(encoding="ascii") != "theme=retro-handheld\nformat=1\n":
        raise RetroLauncherError("Retro Handheld marker is malformed")
    base = retro / "litegui/launcher"
    if base.is_symlink() or not base.is_dir():
        raise RetroLauncherError(f"retro launcher asset directory is missing: {base}")
    for tile in stock.TILES:
        for focused in (False, True):
            state = "_s" if focused else ""
            icon = base / f"{tile.icon}{state}.png"
            narrow = base / f"tile_{tile.icon}{state}.png"
            wide = base / f"tile_{tile.icon}_wide{state}.png"
            expected = ((icon, (140, 140)), (narrow, (224, 230)), (wide, (464, 230)))
            for path, geometry in expected:
                if path.is_symlink() or not path.is_file():
                    raise RetroLauncherError(f"required launcher asset is missing: {path}")
                if _png_dimensions(path) != geometry:
                    raise RetroLauncherError(
                        f"launcher asset geometry mismatch: {path}: "
                        f"{_png_dimensions(path)} != {geometry}"
                    )
    for name in ("hor_line.png", "ver_line.png"):
        path = base / name
        if path.is_symlink() or not path.is_file():
            raise RetroLauncherError(f"required launcher separator is missing: {path}")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RetroLauncherError(f"refusing to replace non-regular output: {path}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), OUTPUT_MODE)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def output_directory(rootfs: Path) -> Path:
    return rootfs / "usr/resource/r1-cfw/launcher/retro"


def generate(rootfs: Path) -> int:
    verify_assets(rootfs)
    directory = output_directory(rootfs)
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise RetroLauncherError(f"invalid Retro launcher output: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(DIRECTORY_MODE)
    expected_names = {f"{stock.mask_name(mask)}.view" for mask in stock.SAFE_MASKS}
    extra = sorted(path.name for path in directory.iterdir() if path.name not in expected_names)
    if extra:
        raise RetroLauncherError(f"unexpected Retro launcher entries: {extra}")
    changed = 0
    for mask in stock.SAFE_MASKS:
        path = directory / f"{stock.mask_name(mask)}.view"
        data = render_layout(mask)
        matches = (
            path.is_file()
            and not path.is_symlink()
            and stat.S_IMODE(path.stat().st_mode) == OUTPUT_MODE
            and path.read_bytes() == data
        )
        if not matches:
            _atomic_write(path, data)
            changed += 1
    return changed


def verify(rootfs: Path) -> None:
    verify_assets(rootfs)
    directory = output_directory(rootfs)
    if directory.is_symlink() or not directory.is_dir():
        raise RetroLauncherError(f"Retro launcher directory is missing: {directory}")
    if stat.S_IMODE(directory.stat().st_mode) != DIRECTORY_MODE:
        raise RetroLauncherError(f"Retro launcher directory mode mismatch: {directory}")
    expected_names = {f"{stock.mask_name(mask)}.view" for mask in stock.SAFE_MASKS}
    actual_names = {path.name for path in directory.iterdir()}
    if actual_names != expected_names:
        raise RetroLauncherError(
            f"unexpected Retro launcher set; missing={sorted(expected_names-actual_names)}, "
            f"extra={sorted(actual_names-expected_names)}"
        )
    for mask in stock.SAFE_MASKS:
        path = directory / f"{stock.mask_name(mask)}.view"
        if path.is_symlink() or not path.is_file():
            raise RetroLauncherError(f"Retro launcher variant is missing: {path}")
        if stat.S_IMODE(path.stat().st_mode) != OUTPUT_MODE:
            raise RetroLauncherError(f"Retro launcher mode mismatch: {path}")
        if path.read_bytes() != render_layout(mask):
            raise RetroLauncherError(f"Retro launcher content mismatch: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("generate", "verify"):
        command = commands.add_parser(name)
        command.add_argument("rootfs", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rootfs = args.rootfs.resolve()
        if args.command == "generate":
            changed = generate(rootfs)
            print(
                f"generated {len(stock.SAFE_MASKS)} Retro launcher variants; "
                f"changed {changed} files"
            )
        else:
            verify(rootfs)
            print(f"verified {len(stock.SAFE_MASKS)} Retro launcher variants")
    except (OSError, RetroLauncherError, stock.LauncherError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
