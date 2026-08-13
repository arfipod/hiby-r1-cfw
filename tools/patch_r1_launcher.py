#!/usr/bin/env python3
"""Generate deterministic HiBy R1 1.6 CFW launcher resources.

The three vendor launcher layouts remain byte-for-byte stock.  Generated
variants live below ``/usr/resource/r1-cfw/launcher`` and are intended to be
bind-mounted over the active theme's stock layout before ``hiby_player``
starts.  Every generated layout uses the existing
``launcher_apps_vg_step`` descriptor for the CFW tile.

Tile masks are two-digit hexadecimal values with these stable bits::

    01 Music       02 Stream       04 Wireless      08 eBook
    10 System      20 CFW          40 About

Safe masks contain four through six tiles and always contain CFW.  The
default ``71`` mask selects Music, System, CFW, and About.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import os
import re
import stat
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_MASK = 0x71
CFW_BIT = 0x20
ALL_TILE_BITS = 0x7F
RESOURCE_MODE = 0o664
GENERATED_DIRECTORY_MODE = 0o755
CONFIG_KEY = "launcher_mask"

LANGUAGES = (
    "english",
    "french",
    "german",
    "italy",
    "japanese",
    "korean",
    "poland",
    "russian",
    "simplified_chinese",
    "spain",
    "thai",
    "traditional_chinese",
    "ukrainian",
)


@dataclass(frozen=True)
class TileSpec:
    bit: int
    key: str
    group_name: str
    image_name: str
    text_name: str
    icon: str
    ini: str
    text: str
    text_height: int = 70


TILES = (
    TileSpec(
        0x01,
        "music",
        "launcher_apps_vg_player",
        "launcher_apps_iv_player",
        "launcher_apps_tv_player",
        "music",
        "settings.ini",
        "music",
    ),
    TileSpec(
        0x02,
        "stream",
        "launcher_apps_vg_stream_media",
        "launcher_apps_iv_stream_media",
        "launcher_apps_tv_stream_media",
        "stream_media",
        "sys_set.ini",
        "stream_media",
    ),
    TileSpec(
        0x04,
        "wireless",
        "launcher_apps_vg_wireless",
        "launcher_apps_iv_wireless",
        "launcher_apps_tv_wireless",
        "wireless",
        "settings.ini",
        "net_set",
    ),
    TileSpec(
        0x08,
        "ebook",
        "launcher_apps_vg_ebook",
        "launcher_apps_iv_ebook",
        "launcher_apps_tv_ebook",
        "book",
        "book.ini",
        "ebook",
    ),
    TileSpec(
        0x10,
        "system",
        "launcher_apps_vg_sysset",
        "launcher_apps_iv_sysset",
        "launcher_apps_tv_sysset",
        "sys_set",
        "settings.ini",
        "sys_set",
        80,
    ),
    TileSpec(
        CFW_BIT,
        "cfw",
        "launcher_apps_vg_step",
        "launcher_apps_iv_step",
        "launcher_apps_tv_step",
        "cfw",
        "launcher.ini",
        "cfw",
        80,
    ),
    TileSpec(
        0x40,
        "about",
        "launcher_apps_vg_about",
        "launcher_apps_iv_about",
        "launcher_apps_tv_about",
        "about",
        "settings.ini",
        "about",
        80,
    ),
)


@dataclass(frozen=True)
class ThemeSpec:
    output_name: str
    stock_relative_path: Path
    stock_sha256: str
    newline: str
    foreground: str
    background: str | None
    midi: bool = False


THEMES = (
    ThemeSpec(
        "theme1",
        Path("usr/resource/layout/theme1/launcher/hiby_launcher_apps.view"),
        "31929831ac794d9b8193d75873e1a43da099e09597a051ac9df4a1bc599c56b0",
        "\n",
        "0x32526c",
        "0xe4f5fe",
    ),
    ThemeSpec(
        "theme2",
        Path("usr/resource/layout/theme2/launcher/hiby_launcher_apps.view"),
        "8f401dfdfa94214e284d461b10596428d882ec741e4e914dfca232acfefb2c40",
        "\n",
        "0xffffff",
        "0x1",
    ),
    ThemeSpec(
        "midi-theme1",
        Path("usr/resource/layout/midi/theme1/launcher/hiby_launcher_apps.view"),
        "dd643063fc8cf37a73cb64fd40a69a9279a3d4e5e48dae02cc18ebe6d1cd0c69",
        "\r\n",
        "0xffffff",
        None,
        midi=True,
    ),
)


SAFE_MASKS = tuple(
    mask
    for mask in range(ALL_TILE_BITS + 1)
    if mask & CFW_BIT and 4 <= mask.bit_count() <= 6
)

CFW_STRING_LINE = "  <cfw>CFW</cfw>"
MASK_RE = re.compile(r"(?:0[xX])?([0-9A-Fa-f]{1,2})\Z")


class LauncherError(RuntimeError):
    """The rootfs or launcher configuration is unexpected or incomplete."""


@dataclass(frozen=True)
class Node:
    kind: str
    children: tuple["Node", ...]
    properties: tuple[tuple[str, object], ...]


@dataclass(frozen=True)
class FileUpdate:
    path: Path
    data: bytes
    mode: int = RESOURCE_MODE


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mask_name(mask: int) -> str:
    validate_mask(mask)
    return f"{mask:02x}"


def validate_mask(mask: int) -> None:
    if mask < 0 or mask & ~ALL_TILE_BITS:
        raise LauncherError(f"launcher mask is outside 00..7F: {mask:#x}")
    if not mask & CFW_BIT:
        raise LauncherError(f"launcher mask must keep CFW visible: {mask:02x}")
    count = mask.bit_count()
    if count < 4 or count > 6:
        raise LauncherError(
            f"launcher mask must select four through six tiles: {mask:02x}"
        )


def selected_tiles(mask: int) -> tuple[TileSpec, ...]:
    validate_mask(mask)
    return tuple(tile for tile in TILES if mask & tile.bit)


def _require_regular_file(rootfs: Path, relative_path: Path) -> Path:
    path = rootfs / relative_path
    if path.is_symlink() or not path.is_file():
        raise LauncherError(f"required regular file is missing: {relative_path}")
    return path


def verify_stock_layouts(rootfs: Path) -> None:
    for theme in THEMES:
        path = _require_regular_file(rootfs, theme.stock_relative_path)
        actual = sha256_bytes(path.read_bytes())
        if actual != theme.stock_sha256:
            raise LauncherError(
                f"stock launcher SHA-256 mismatch for {theme.stock_relative_path}: "
                f"expected {theme.stock_sha256}, got {actual}"
            )


def _serialize_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _serialize_node(node: Node, *, level: int, newline: str) -> list[str]:
    indent = "\t" * level
    lines = [f"{indent}{_serialize_value(node.kind)}:{{"]
    entries: list[Node | tuple[str, object]] = [*node.children, *node.properties]
    for index, entry in enumerate(entries):
        last = index == len(entries) - 1
        if isinstance(entry, Node):
            child_lines = _serialize_node(entry, level=level + 1, newline=newline)
            if not last:
                child_lines[-1] += ","
            lines.extend(child_lines)
        else:
            key, value = entry
            comma = "" if last else ","
            lines.append(
                f"{'\t' * (level + 1)}{_serialize_value(key)}:"
                f"{_serialize_value(value)}{comma}"
            )
    lines.append(f"{indent}}}")
    return lines


def _document(root: Node, newline: str) -> bytes:
    lines = ["{", *_serialize_node(root, level=1, newline=newline), "}", ""]
    return newline.join(lines).encode("utf-8")


def _tile_geometry(count: int) -> tuple[tuple[tuple[int, int, int, int], ...], int]:
    if count == 4:
        return (
            (
                (0, 0, 480, 246),
                (0, 246, 240, 246),
                (240, 246, 240, 246),
                (0, 492, 480, 258),
            ),
            750,
        )
    rows = (count + 1) // 2
    geometry = tuple(
        ((index % 2) * 240, (index // 2) * 246, 240, 246)
        for index in range(count)
    )
    return geometry, rows * 246


def _image_node(tile: TileSpec, theme: ThemeSpec, width: int) -> Node:
    icon_width = 224 if theme.midi else 140
    x = (width - icon_width) // 2
    properties: list[tuple[str, object]] = [
        ("name", tile.image_name),
        ("img_path", f"launcher\\{tile.icon}.png"),
        ("img_focus_path", f"launcher\\{tile.icon}_s.png"),
        ("x", x),
        ("y", 8 if theme.midi else 28),
    ]
    if not theme.midi:
        properties.append(("color_mode", "color_565"))
    properties.append(("imageview", True))
    return Node("imageview", (), tuple(properties))


def _text_node(tile: TileSpec, theme: ThemeSpec, width: int) -> Node:
    if theme.midi:
        x = 4 if width == 240 else 4
        y = 174
        text_width = width - 8
        text_type = "static|center"
        size = 26
    else:
        x = 2
        y = 174
        text_width = width - 4
        text_type = "static|h_center|top"
        size = 28
    return Node(
        "textview",
        (),
        (
            ("name", tile.text_name),
            ("size", size),
            ("color", theme.foreground),
            ("focus_color", theme.foreground),
            ("type", text_type),
            ("ini", tile.ini),
            ("text", tile.text),
            ("measure", 1),
            ("x", x),
            ("y", y),
            ("w", text_width),
            ("h", tile.text_height),
            ("textview", True),
        ),
    )


def _tile_node(
    tile: TileSpec,
    theme: ThemeSpec,
    geometry: tuple[int, int, int, int],
) -> Node:
    x, y, width, height = geometry
    properties: list[tuple[str, object]] = [
        ("name", tile.group_name),
        ("type", "lg_view"),
    ]
    if theme.background is not None:
        properties.extend(
            (("color", theme.background), ("focus_color", theme.background))
        )
    properties.extend(
        (
            ("x", x),
            ("y", y),
            ("w", width),
            ("h", height),
            ("viewgroup", True),
        )
    )
    return Node(
        "viewgroup",
        (_image_node(tile, theme, width), _text_node(tile, theme, width)),
        tuple(properties),
    )


def _horizontal_line(theme: ThemeSpec, index: int, y: int) -> Node:
    properties: list[tuple[str, object]] = [
        ("name", f"launcher_apps_iv_hor_line_{index}"),
        ("img_path", "launcher\\hor_line.png"),
        ("x", 16 if theme.midi else 0),
        ("y", y),
    ]
    if not theme.midi:
        properties.append(("color_mode", "color_565"))
    properties.append(("imageview", True))
    return Node("imageview", (), tuple(properties))


def _vertical_line(theme: ThemeSpec, index: int, y: int) -> Node:
    properties: list[tuple[str, object]] = [
        ("name", f"launcher_apps_iv_ver_line_{index}"),
        ("img_path", "launcher\\ver_line.png"),
        ("x", 240),
        ("y", y),
    ]
    if not theme.midi:
        properties.append(("color_mode", "color_565"))
    properties.append(("imageview", True))
    return Node("imageview", (), tuple(properties))


def render_layout(mask: int, theme: ThemeSpec) -> bytes:
    tiles = selected_tiles(mask)
    geometry, content_height = _tile_geometry(len(tiles))
    children: list[Node] = [
        _tile_node(tile, theme, position)
        for tile, position in zip(tiles, geometry, strict=True)
    ]

    row_count = 3 if len(tiles) == 4 else (len(tiles) + 1) // 2
    children.extend(
        _horizontal_line(theme, index, index * 246)
        for index in range(1, row_count)
    )
    # The stock vertical-line bitmap is nearly screen-height.  The four-tile
    # layout therefore relies on the two exact 240x246 middle-row group bounds
    # rather than drawing that bitmap through the full-width About tile.
    if len(tiles) > 4:
        line_height = (
            726
            if theme.midi
            else (730 if theme.output_name == "theme1" else 749)
        )
        children.extend(
            _vertical_line(theme, index + 1, y)
            for index, y in enumerate(range(0, content_height, line_height))
        )

    properties: list[tuple[str, object]] = [
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
        # Preserve the stock launcher/Now Playing horizontal page range.
        ("scroll_min_x", -480),
    ]
    if theme.background is not None:
        properties.append(("color", theme.background))
    properties.extend(
        (
            ("flag", "scroll"),
            ("zorder", -9),
            ("viewgroup", True),
            ("add_layout", True),
        )
    )
    return _document(Node("viewgroup", tuple(children), tuple(properties)), theme.newline)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind)
    checksum = binascii.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _png_rgba(width: int, height: int, pixels: bytes) -> bytes:
    expected = width * height * 4
    if len(pixels) != expected:
        raise AssertionError(f"expected {expected} RGBA bytes, got {len(pixels)}")
    rows = b"".join(
        b"\x00" + pixels[offset : offset + width * 4]
        for offset in range(0, len(pixels), width * 4)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _inside_segment(
    x: float,
    y: float,
    start: tuple[float, float],
    end: tuple[float, float],
    radius: float,
) -> bool:
    ax, ay = start
    bx, by = end
    dx = bx - ax
    dy = by - ay
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        closest_x, closest_y = ax, ay
    else:
        position = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / length_squared))
        closest_x = ax + position * dx
        closest_y = ay + position * dy
    return (x - closest_x) ** 2 + (y - closest_y) ** 2 <= radius * radius


def _icon_pixel(
    x: float,
    y: float,
    *,
    center: tuple[float, float],
    badge: tuple[int, int, int, int],
    glyph: tuple[int, int, int, int],
    background: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    cx, cy = center
    local_x = x - cx
    local_y = y - cy
    if local_x * local_x + local_y * local_y > 60 * 60:
        return background

    color = badge
    border = (
        -38 <= local_x <= 38
        and -29 <= local_y <= 29
        and not (-31 <= local_x <= 31 and -22 <= local_y <= 22)
    )
    prompt = _inside_segment(local_x, local_y, (-20, -11), (-8, 0), 3.4) or _inside_segment(
        local_x, local_y, (-8, 0), (-20, 11), 3.4
    )
    underscore = _inside_segment(local_x, local_y, (3, 11), (23, 11), 3.4)
    if border or prompt or underscore:
        color = glyph
    return color


def render_cfw_icon(theme: ThemeSpec, *, focused: bool) -> bytes:
    if theme.midi:
        width, height = 224, 242
        center = (112.0, 104.0)
        background = (28, 27, 29, 255)
        badge = (255, 255, 255, 255) if focused else (44, 202, 204, 255)
        glyph = (44, 202, 204, 255) if focused else (255, 255, 255, 255)
    elif theme.output_name == "theme1":
        width = height = 140
        center = (70.0, 70.0)
        background = (228, 245, 254, 255)
        badge = (44, 202, 204, 255) if focused else (50, 82, 108, 255)
        glyph = (255, 255, 255, 255)
    else:
        width = height = 140
        center = (70.0, 70.0)
        background = (0, 0, 0, 255)
        badge = (44, 202, 204, 255) if focused else (92, 92, 99, 255)
        glyph = (255, 255, 255, 255)

    samples = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            colors = [
                _icon_pixel(
                    x + sample_x,
                    y + sample_y,
                    center=center,
                    badge=badge,
                    glyph=glyph,
                    background=background,
                )
                for sample_x, sample_y in samples
            ]
            pixels.extend(
                round(sum(color[channel] for color in colors) / len(colors))
                for channel in range(4)
            )
    return _png_rgba(width, height, bytes(pixels))


def _launcher_string_update(path: Path, *, require_cfw: bool) -> FileUpdate:
    raw = path.read_bytes()
    if not raw.startswith(b"\xff\xfe") or raw[2:4] == b"\xff\xfe":
        raise LauncherError(f"expected one UTF-16LE BOM: {path}")
    try:
        text = raw[2:].decode("utf-16-le")
    except UnicodeDecodeError as error:
        raise LauncherError(f"invalid UTF-16LE launcher resource: {path}") from error
    if "\r\n" not in text or "\n" in text.replace("\r\n", ""):
        raise LauncherError(f"launcher resource must use CRLF: {path}")
    if text.count("<resources>") != 1 or text.count("</resources>") != 1:
        raise LauncherError(f"unexpected launcher resource root: {path}")

    cfw_open = text.count("<cfw>")
    cfw_close = text.count("</cfw>")
    if cfw_open or cfw_close:
        canonical = f"{CFW_STRING_LINE}\r\n</resources>"
        if cfw_open != 1 or cfw_close != 1 or canonical not in text:
            raise LauncherError(f"conflicting or noncanonical CFW string key: {path}")
        updated = text
    elif require_cfw:
        marker = "</resources>"
        offset = text.index(marker)
        if offset == 0 or not text[:offset].endswith("\r\n"):
            raise LauncherError(f"unexpected </resources> placement: {path}")
        updated = text[:offset] + CFW_STRING_LINE + "\r\n" + text[offset:]
    else:
        raise LauncherError(f"CFW string key is missing: {path}")

    return FileUpdate(path, b"\xff\xfe" + updated.encode("utf-16-le"))


def _string_updates(rootfs: Path, *, require_cfw: bool) -> tuple[FileUpdate, ...]:
    updates = []
    for language in LANGUAGES:
        relative = Path("usr/resource/str") / language / "launcher.ini"
        path = _require_regular_file(rootfs, relative)
        updates.append(_launcher_string_update(path, require_cfw=require_cfw))
    return tuple(updates)


def _layout_updates(rootfs: Path) -> tuple[FileUpdate, ...]:
    base = rootfs / "usr/resource/r1-cfw/launcher"
    return tuple(
        FileUpdate(base / theme.output_name / f"{mask_name(mask)}.view", render_layout(mask, theme))
        for theme in THEMES
        for mask in SAFE_MASKS
    )


def _asset_updates(rootfs: Path) -> tuple[FileUpdate, ...]:
    base = rootfs / "usr/resource/litegui"
    theme_paths = {
        "theme1": Path("theme1"),
        "theme2": Path("theme2"),
        "midi-theme1": Path("midi/theme1"),
    }
    return tuple(
        FileUpdate(
            base
            / theme_paths[theme.output_name]
            / "launcher"
            / ("cfw_s.png" if focused else "cfw.png"),
            render_cfw_icon(theme, focused=focused),
        )
        for theme in THEMES
        for focused in (False, True)
    )


def _atomic_write(update: FileUpdate) -> None:
    path = update.path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LauncherError(f"refusing to replace non-regular output: {path}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), update.mode)
            output.write(update.data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _generated_directories(rootfs: Path) -> tuple[Path, ...]:
    base = rootfs / "usr/resource/r1-cfw"
    launcher = base / "launcher"
    return (base, launcher, *(launcher / theme.output_name for theme in THEMES))


def _prepare_generated_directories(rootfs: Path) -> None:
    for directory in _generated_directories(rootfs):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise LauncherError(
                f"generated launcher path is not a directory: {directory}"
            )
        directory.mkdir(mode=GENERATED_DIRECTORY_MODE, parents=True, exist_ok=True)
        directory.chmod(GENERATED_DIRECTORY_MODE)


def _update_matches(update: FileUpdate) -> bool:
    return (
        not update.path.is_symlink()
        and update.path.is_file()
        and stat.S_IMODE(update.path.stat().st_mode) == update.mode
        and update.path.read_bytes() == update.data
    )


def _validate_output_targets(updates: Iterable[FileUpdate]) -> None:
    for update in updates:
        path = update.path
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise LauncherError(f"refusing to replace non-regular output: {path}")


def generate_rootfs(rootfs: Path) -> tuple[Path, ...]:
    rootfs = rootfs.resolve()
    if not rootfs.is_dir():
        raise LauncherError(f"rootfs directory is missing: {rootfs}")
    verify_stock_layouts(rootfs)
    _reject_extra_generated_entries(rootfs)
    updates = (
        *_string_updates(rootfs, require_cfw=True),
        *_asset_updates(rootfs),
        *_layout_updates(rootfs),
    )
    _validate_output_targets(updates)
    _prepare_generated_directories(rootfs)

    changed = tuple(update for update in updates if not _update_matches(update))
    for update in changed:
        _atomic_write(update)
    return tuple(update.path for update in changed)


def _verify_generated_directories(rootfs: Path) -> None:
    expected_names = {f"{mask_name(mask)}.view" for mask in SAFE_MASKS}
    base = rootfs / "usr/resource/r1-cfw/launcher"
    for directory in _generated_directories(rootfs):
        if directory.is_symlink() or not directory.is_dir():
            raise LauncherError(f"generated launcher directory is missing: {directory}")
        if stat.S_IMODE(directory.stat().st_mode) != GENERATED_DIRECTORY_MODE:
            raise LauncherError(f"generated launcher directory mode mismatch: {directory}")
    for theme in THEMES:
        directory = base / theme.output_name
        actual_names = {path.name for path in directory.iterdir()}
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            raise LauncherError(
                f"unexpected generated launcher set in {directory}; "
                f"missing={missing}, extra={extra}"
            )


def _reject_extra_generated_entries(rootfs: Path) -> None:
    expected_names = {f"{mask_name(mask)}.view" for mask in SAFE_MASKS}
    base = rootfs / "usr/resource/r1-cfw/launcher"
    for theme in THEMES:
        directory = base / theme.output_name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise LauncherError(
                f"generated launcher path is not a directory: {directory}"
            )
        extra = sorted(
            path.name
            for path in directory.iterdir()
            if path.name not in expected_names
        )
        if extra:
            raise LauncherError(
                f"unexpected generated launcher entries in {directory}: {extra}"
            )


def verify_rootfs(rootfs: Path) -> None:
    rootfs = rootfs.resolve()
    if not rootfs.is_dir():
        raise LauncherError(f"rootfs directory is missing: {rootfs}")
    verify_stock_layouts(rootfs)
    _string_updates(rootfs, require_cfw=False)
    _verify_generated_directories(rootfs)
    for update in (*_asset_updates(rootfs), *_layout_updates(rootfs)):
        if update.path.is_symlink() or not update.path.is_file():
            raise LauncherError(f"generated launcher resource is missing: {update.path}")
        if not _update_matches(update):
            raise LauncherError(f"generated launcher resource mismatch: {update.path}")


def _parse_config_mask(text: str) -> int | None:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    if not lines:
        return None
    if len(lines) != 1:
        return None
    line = lines[0]
    if "=" in line:
        key, value = (part.strip() for part in line.split("=", 1))
        if key != CONFIG_KEY:
            return None
    else:
        value = line
    match = MASK_RE.fullmatch(value)
    if not match:
        return None
    mask = int(match.group(1), 16)
    try:
        validate_mask(mask)
    except LauncherError:
        return None
    return mask


def select_config(path: Path) -> int:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            return DEFAULT_MASK
        raw = path.read_bytes()
        if raw.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
            return DEFAULT_MASK
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return DEFAULT_MASK
    selected = _parse_config_mask(text)
    return DEFAULT_MASK if selected is None else selected


def _command_generate(args: argparse.Namespace) -> None:
    changed = generate_rootfs(args.rootfs)
    print(
        f"generated {len(SAFE_MASKS) * len(THEMES)} launcher variants, "
        f"{len(THEMES) * 2} original CFW icons, and "
        f"{len(LANGUAGES)} CFW string keys; changed {len(changed)} files"
    )


def _command_verify(args: argparse.Namespace) -> None:
    verify_rootfs(args.rootfs)
    print(
        f"verified {len(THEMES)} stock launchers, "
        f"{len(SAFE_MASKS) * len(THEMES)} generated variants, "
        f"{len(THEMES) * 2} original CFW icons, and "
        f"{len(LANGUAGES)} CFW string keys"
    )


def _command_select_config(args: argparse.Namespace) -> None:
    print(f"{select_config(args.config):02x}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser(
        "generate", help="generate every safe launcher variant in ROOTFS"
    )
    generate.add_argument("rootfs", type=Path)
    generate.set_defaults(function=_command_generate)

    verify = commands.add_parser(
        "verify", help="verify stock launchers and all generated resources"
    )
    verify.add_argument("rootfs", type=Path)
    verify.set_defaults(function=_command_verify)

    select = commands.add_parser(
        "select-config", help="print a safe normalized mask selected by CONFIG"
    )
    select.add_argument("config", type=Path)
    select.set_defaults(function=_command_select_config)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.function(args)
    except LauncherError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
